"""HTTP transport interceptor — monkey-patches GeminiCloudCodeClient to
install Antigravity request/response transformation.

Architecture:
- Transport wrapper: intercepts at httpcore.Request level to rewrite body
  before httpx/h11 processes it (avoids Content-Length mismatch)
- httpx event hooks: handle header modifications and response processing
  (no body mutation in event hooks — that's the transport's job)
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx
import httpcore

from .config import get_config
from .endpoints import select_endpoint
from .transform.envelope import (
    build_antigravity_headers,
    build_antigravity_envelope,
    resolve_model_for_header_style,
)

logger = logging.getLogger(__name__)

_PATCHED = False
_ORIGINAL_INIT = None


# =============================================================================
# Transport-level interception (body mutation — safe from Content-Length bugs)
# =============================================================================

class _AntigravityTransport:
    """Wraps the original httpcore transport, rewriting request bodies."""

    def __init__(self, original_transport):
        self._original = original_transport

    def _forward(self, request, body_bytes):
        """Forward a request with the already-read body bytes (stream was consumed)."""
        new_req = httpcore.Request(
            method=request.method,
            url=request.url,
            headers=request.headers,
            content=body_bytes,
            extensions=request.extensions,
        )
        return self._original.handle_request(new_req)

    def handle_request(self, request: httpcore.Request):
        """Transform the request body before forwarding to the real transport."""
        url_str = str(request.url) if hasattr(request, 'url') else ""
        
        # Only intercept Cloud Code requests
        if b"cloudcode-pa.googleapis.com" not in (request.url.host or b""):
            return self._original.handle_request(request)

        # Read the body from the stream iterator (consumes it)
        body_bytes = b"".join(request.stream)
        try:
            body = json.loads(body_bytes)
        except (json.JSONDecodeError, TypeError):
            return self._forward(request, body_bytes)

        if not isinstance(body, dict) or "request" not in body:
            return self._forward(request, body_bytes)

        config = get_config()
        model = str(body.get("model", ""))
        project_id = str(body.get("project", ""))
        inner = body["request"]

        header_style = "gemini-cli" if config.cli_first else "antigravity"
        model = resolve_model_for_header_style(model, header_style)

        envelope = build_antigravity_envelope(
            request_payload=inner,
            model=model,
            project_id=project_id,
            header_style=header_style,
        )

        # --- Strip Claude thinking blocks ---
        if _is_claude_model(model) and not config.keep_thinking:
            inner_req = envelope.get("request") if isinstance(envelope, dict) else {}
            if isinstance(inner_req, dict) and "contents" in inner_req:
                from .transform.thinking import strip_all_thinking_blocks
                strip_all_thinking_blocks(inner_req["contents"])

        # --- Sanitize tool schemas ---
        if config.claude_tool_hardening:
            inner_req = envelope.get("request") if isinstance(envelope, dict) else {}
            tools = inner_req.get("tools") if isinstance(inner_req, dict) else None
            if isinstance(tools, list):
                from .transform.schema import clean_json_schema
                for tool in tools:
                    if isinstance(tool, dict):
                        func_decls = tool.get("functionDeclarations")
                        if isinstance(func_decls, list):
                            for fd in func_decls:
                                if isinstance(fd, dict) and "parameters" in fd:
                                    fd["parameters"] = clean_json_schema(fd["parameters"])
                        elif "parameters" in tool:
                            tool["parameters"] = clean_json_schema(tool["parameters"])

        new_body = json.dumps(envelope).encode("utf-8")

        # --- Rewrite URL through endpoint fallback ---
        endpoint = select_endpoint(config)
        old_url = url_str if isinstance(url_str, str) else str(request.url)
        new_url_str = old_url.replace("https://cloudcode-pa.googleapis.com", endpoint)
        new_url = httpcore.URL(new_url_str)

        # --- Rewrite headers ---
        new_headers = build_antigravity_headers(header_style=header_style)
        raw_headers: list[tuple[bytes, bytes]] = []
        for name, value in request.headers:
            lower = name.lower()
            if lower in (b"host", b"authorization", b"content-type", b"accept", b"accept-encoding"):
                raw_headers.append((name, value))
        for key, val in new_headers.items():
            raw_headers.append((key.encode(), val.encode()))

        # --- Inject device fingerprint ---
        try:
            from .fingerprint import generate_fingerprint
            fingerprint = generate_fingerprint()
            if fingerprint:
                fp_ua = fingerprint.get("userAgent")
                if fp_ua and isinstance(fp_ua, str):
                    raw_headers = [(k, v) for k, v in raw_headers if k.lower() != b"user-agent"]
                    raw_headers.append((b"User-Agent", fp_ua.encode()))
                fp_meta = fingerprint.get("clientMetadata")
                if fp_meta:
                    raw_headers = [(k, v) for k, v in raw_headers if k.lower() != b"client-metadata"]
                    raw_headers.append((b"Client-Metadata", json.dumps(fp_meta).encode()))
        except Exception:
            pass

        new_request = httpcore.Request(
            method=request.method,
            url=new_url,
            headers=raw_headers,
            content=new_body,
            extensions=request.extensions,
        )
        return self._original.handle_request(new_request)


def _is_claude_model(model: str) -> bool:
    try:
        from .transform.messages import is_claude_model
        return is_claude_model(model)
    except Exception:
        return "claude" in model.lower()


# =============================================================================
# Response hook (header-level only — no body mutation)
# =============================================================================

def _antigravity_response_hook(response: httpx.Response) -> None:
    """Handle Antigravity-specific response quirks.

    This hook does NOT modify the response body — only handles side effects."""
    from .config import get_config

    config = get_config()

    # --- Token refresh on 401 ---
    if response.status_code == 401 and config.proactive_token_refresh:
        try:
            from .token import refresh_access_token
            from .storage import load_accounts

            accounts_data = load_accounts()
            active_idx = accounts_data.get("activeIndex", 0)
            accounts = accounts_data.get("accounts", [])

            if 0 <= active_idx < len(accounts):
                acc = accounts[active_idx]
                refresh = acc.get("refreshToken", "")
                if refresh:
                    refreshed = refresh_access_token({"refresh": refresh})
                    new_token = refreshed.get("access", "")
                    if new_token:
                        try:
                            from .cli import sync_token_to_google_oauth
                            sync_token_to_google_oauth(
                                access_token=new_token,
                                refresh_token=refresh,
                                project_id=acc.get("projectId", ""),
                                email=acc.get("email"),
                                expires_ms=refreshed.get("expires"),
                            )
                        except Exception:
                            pass
                        logger.info("Token refreshed after 401 for %s", acc.get("email"))
        except Exception as exc:
            logger.warning("Token refresh failed after 401: %s", exc)

    # --- Account rotation on rate limit ---
    if response.status_code == 429 and config.switch_on_first_rate_limit:
        try:
            from .accounts.manager import AccountManager
            from .accounts.ratelimit import mark_rate_limited

            manager = AccountManager.load_from_disk()
            active = manager.get_current_account_for_family("gemini")
            if active:
                retry_after_seconds = config.default_retry_after_seconds
                retry_after_hdr = response.headers.get("Retry-After") or response.headers.get("retry-after")
                if retry_after_hdr:
                    try:
                        retry_after_seconds = int(retry_after_hdr)
                    except ValueError:
                        pass

                mark_rate_limited(active, float(retry_after_seconds * 1000), "gemini", "antigravity")
                logger.warning("Rate limited on account %s", active.email)

                next_acc = manager.get_current_or_next_for_family("gemini", strategy="hybrid")
                if next_acc and next_acc.index != active.index:
                    try:
                        from .token import refresh_access_token
                        refreshed = refresh_access_token({"refresh": next_acc.refresh_parts.refresh_token})
                        access_token = refreshed.get("access", "")
                        from .cli import sync_token_to_google_oauth
                        sync_token_to_google_oauth(
                            access_token=access_token,
                            refresh_token=next_acc.refresh_parts.refresh_token,
                            project_id=next_acc.refresh_parts.project_id or "",
                            email=next_acc.email,
                            expires_ms=refreshed.get("expires"),
                        )
                        logger.info("Rotated to account %s after rate limit", next_acc.email)
                    except Exception as exc:
                        logger.warning("Account rotation failed: %s", exc)
        except Exception as exc:
            logger.warning("Rate limit handler error: %s", exc)

    # --- Mark endpoint failed on 5xx ---
    if response.status_code >= 500:
        try:
            from .endpoints import mark_endpoint_failed
            req_url = str(response.request.url)
            from urllib.parse import urlparse
            parsed = urlparse(req_url)
            endpoint = f"https://{parsed.netloc}"
            mark_endpoint_failed(endpoint)
        except Exception:
            pass

    # --- Preview access error rewriting ---
    if response.is_success:
        try:
            from .transform.response import rewrite_preview_access_error
            body = json.loads(response.content)
            if isinstance(body, dict):
                response_inner = body.get("response")
                inner = response_inner if isinstance(response_inner, dict) else body
                error = inner.get("error") if isinstance(inner, dict) and isinstance(inner.get("error"), dict) else None
                if error is not None:
                    rewritten = rewrite_preview_access_error(inner, response.status_code, None)
                    if rewritten is not None:
                        inner["error"] = rewritten.get("error", inner.get("error", {}))
                        response._content = json.dumps(body).encode("utf-8")
        except Exception:
            pass

    # --- Session recovery detection ---
    if config.session_recovery and response.is_success:
        try:
            from .recovery import detect_error_type, is_recoverable_error
            body = json.loads(response.content)
            if isinstance(body, dict):
                response_inner = body.get("response")
                inner = response_inner if isinstance(response_inner, dict) else body
                error_obj = inner.get("error") if isinstance(inner, dict) else None
                if error_obj and is_recoverable_error(error_obj):
                    error_type = detect_error_type(error_obj)
                    logger.info("Detected recoverable error: %s", error_type)
        except Exception:
            pass


# =============================================================================
# Install / uninstall
# =============================================================================

def _wrap_http_client(http_client: httpx.Client) -> httpx.Client:
    """Replace the transport with our Antigravity wrapper and add response hook."""
    original_transport = http_client._transport
    http_client._transport = _AntigravityTransport(original_transport)
    # Add response-only event hook (no body mutation)
    if http_client.event_hooks.get("response") is None:
        http_client.event_hooks["response"] = []
    http_client.event_hooks["response"].append(_antigravity_response_hook)
    return http_client


def install() -> bool:
    """Monkey-patch GeminiCloudCodeClient.__init__ to wrap the transport."""
    global _PATCHED, _ORIGINAL_INIT
    if _PATCHED:
        return False
    try:
        from agent.gemini_cloudcode_adapter import GeminiCloudCodeClient
    except ImportError:
        logger.warning("GeminiCloudCodeClient not importable — is hermes-agent installed?")
        return False

    _ORIGINAL_INIT = GeminiCloudCodeClient.__init__

    def _patched_init(self, *args: Any, **kwargs: Any) -> None:
        _ORIGINAL_INIT(self, *args, **kwargs)
        _wrap_http_client(self._http)

    GeminiCloudCodeClient.__init__ = _patched_init
    _PATCHED = True
    logger.info("Antigravity HTTP interceptor installed (transport-level)")
    return True


def is_installed() -> bool:
    return _PATCHED
