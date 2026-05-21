"""HTTP interceptor — wraps GeminiCloudCodeClient's httpx client to inject
Antigravity headers AND optionally transform request bodies before serialization.

Architecture: a proxy object intercepts post()/stream() calls, modifying the
json=body dict and headers BEFORE httpx processes them. httpx serializes the
modified dict fresh, computing Content-Length correctly — no transport hacking.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from .config import get_config
from .endpoints import select_endpoint
from .transform.envelope import (
    build_antigravity_headers,
    resolve_model_for_header_style,
)

logger = logging.getLogger(__name__)

_PATCHED = False
_ORIGINAL_INIT = None


# =============================================================================
# HTTP proxy — intercepts post()/stream() before httpx serializes the body
# =============================================================================

class _HttpProxy:
    """Wraps an httpx.Client, transforming body and headers on post()/stream()."""

    def __init__(self, original: httpx.Client):
        self._original = original

    # --- passthrough attributes ---
    def __getattr__(self, name: str) -> Any:
        return getattr(self._original, name)

    # --- intercept post ---
    def post(self, url, **kwargs: Any) -> httpx.Response:
        url = self._transform(url, kwargs)
        return self._original.post(url, **kwargs)

    # --- intercept stream ---
    def stream(self, method, url, **kwargs: Any):
        url = self._transform(url, kwargs)
        return self._original.stream(method, url, **kwargs)

    # --- core transformation ---
    def _transform(self, url: Any, kwargs: dict) -> Any:
        """Transform body, headers, and URL. Returns (possibly rewritten) URL."""
        url_str = str(url)
        if "cloudcode-pa.googleapis.com" not in url_str:
            return url

        body = kwargs.get("json")
        if not isinstance(body, dict) or "request" not in body:
            return url

        config = get_config()
        model = str(body.get("model", ""))
        header_style = "gemini-cli" if config.cli_first else "antigravity"
        model = resolve_model_for_header_style(model, header_style)

        # --- Body mutation: thinking blocks ---
        if _is_claude_model(model) and not config.keep_thinking:
            inner = body.get("request", {})
            if isinstance(inner, dict) and "contents" in inner:
                from .transform.thinking import strip_thinking_blocks
                inner["contents"] = strip_thinking_blocks(inner["contents"], is_claude=True)

        # --- Body mutation: schema sanitization ---
        if config.claude_tool_hardening:
            inner = body.get("request", {})
            tools = inner.get("tools") if isinstance(inner, dict) else None
            if isinstance(tools, list):
                from .transform.schema import clean_json_schema
                for tool in tools:
                    if isinstance(tool, dict):
                        fds = tool.get("functionDeclarations")
                        if isinstance(fds, list):
                            for fd in fds:
                                if isinstance(fd, dict) and "parameters" in fd:
                                    fd["parameters"] = clean_json_schema(fd["parameters"])
                        elif "parameters" in tool:
                            tool["parameters"] = clean_json_schema(tool["parameters"])

        # --- URL rewriting ---
        endpoint = select_endpoint(config)
        new_url = url_str.replace("https://cloudcode-pa.googleapis.com", endpoint)
        if new_url != url_str:
            url = httpx.URL(new_url) if isinstance(url, str) else new_url

        # --- Header injection ---
        headers = kwargs.get("headers")
        if headers is None:
            headers = {}
            kwargs["headers"] = headers
        new_headers = build_antigravity_headers(header_style=header_style)
        for key, val in new_headers.items():
            headers[key] = val

        # --- Fingerprint ---
        try:
            from .fingerprint import generate_fingerprint
            fp = generate_fingerprint()
            if fp:
                cm = fp.get("clientMetadata")
                if cm:
                    headers["Client-Metadata"] = json.dumps(cm)
        except Exception:
            pass

        return url


def _is_claude_model(model: str) -> bool:
    try:
        from .transform.messages import is_claude_model
        return is_claude_model(model)
    except Exception:
        return "claude" in model.lower()


# =============================================================================
# Response hook (side effects only — no body mutation)
# =============================================================================

def _response_hook(response: httpx.Response) -> None:
    config = get_config()

    if response.status_code == 401 and config.proactive_token_refresh:
        try:
            from .token import refresh_access_token
            from .storage import load_accounts
            from .cli import sync_token_to_google_oauth
            d = load_accounts()
            accs = d.get("accounts", [])
            idx = d.get("activeIndex", 0)
            if 0 <= idx < len(accs):
                a = accs[idx]
                r = refresh_access_token({"refresh": a.get("refreshToken", "")})
                if r.get("access"):
                    sync_token_to_google_oauth(
                        access_token=r["access"], refresh_token=a.get("refreshToken", ""),
                        project_id=a.get("projectId", ""), email=a.get("email"),
                        expires_ms=r.get("expires"),
                    )
        except Exception as e:
            logger.warning("Token refresh failed: %s", e)

    if response.status_code == 403:
        # Account is ineligible (shadow-banned) — mark for cooldown and rotate
        try:
            from .accounts.manager import AccountManager
            mgr = AccountManager.load_from_disk()
            active = mgr.get_current_account_for_family("gemini")
            if active:
                import time
                active.cooling_down_until = (time.time() + 86400) * 1000  # 24h cooldown
                active.cooldown_reason = "auth-failure"
                mgr.save_to_disk()
                next_acc = mgr.get_current_or_next_for_family("gemini", strategy="hybrid")
                if next_acc and next_acc.index != active.index:
                    from .token import refresh_access_token
                    from .cli import sync_token_to_google_oauth
                    r = refresh_access_token({"refresh": next_acc.refresh_parts.refresh_token})
                    if r.get("access"):
                        sync_token_to_google_oauth(
                            access_token=r["access"], refresh_token=next_acc.refresh_parts.refresh_token,
                            project_id=next_acc.refresh_parts.project_id or "", email=next_acc.email,
                            expires_ms=r.get("expires"),
                        )
                        logger.info("Rotated to %s after 403 ineligible account", next_acc.email)
        except Exception as e:
            logger.warning("403 handler error: %s", e)

    if response.status_code == 429 and config.switch_on_first_rate_limit:
        try:
            from .accounts.manager import AccountManager
            from .accounts.ratelimit import mark_rate_limited
            mgr = AccountManager.load_from_disk()
            active = mgr.get_current_account_for_family("gemini")
            if active:
                retry = config.default_retry_after_seconds
                rh = response.headers.get("Retry-After") or response.headers.get("retry-after")
                if rh:
                    try: retry = int(rh)
                    except ValueError: pass
                mark_rate_limited(active, float(retry * 1000), "gemini", "antigravity")
                mark_rate_limited(active, float(retry * 1000), "gemini", "gemini-cli")
                mgr.save_to_disk()
                next_acc = mgr.get_current_or_next_for_family("gemini", strategy="hybrid")
                if next_acc and next_acc.index != active.index:
                    from .token import refresh_access_token
                    from .cli import sync_token_to_google_oauth
                    r = refresh_access_token({"refresh": next_acc.refresh_parts.refresh_token})
                    if r.get("access"):
                        sync_token_to_google_oauth(
                            access_token=r["access"], refresh_token=next_acc.refresh_parts.refresh_token,
                            project_id=next_acc.refresh_parts.project_id or "", email=next_acc.email,
                            expires_ms=r.get("expires"),
                        )
                        logger.info("Rotated to %s after rate limit", next_acc.email)
                else:
                    logger.debug("No other account available for rotation")
        except Exception as e:
            logger.warning("Rate limit handler error: %s", e)

    if response.status_code >= 500:
        try:
            from .endpoints import mark_endpoint_failed
            from urllib.parse import urlparse
            p = urlparse(str(response.request.url))
            mark_endpoint_failed(f"https://{p.netloc}")
        except Exception:
            pass


# =============================================================================
# Install
# =============================================================================

def _wrap_http_client(http_client: httpx.Client) -> _HttpProxy:
    proxy = _HttpProxy(http_client)
    if not http_client.event_hooks.get("response"):
        http_client.event_hooks["response"] = []
    http_client.event_hooks["response"].append(_response_hook)
    return proxy


def install() -> bool:
    global _PATCHED, _ORIGINAL_INIT
    if _PATCHED:
        return False
    try:
        from agent.gemini_cloudcode_adapter import GeminiCloudCodeClient
    except ImportError:
        return False
    _ORIGINAL_INIT = GeminiCloudCodeClient.__init__

    def _patched_init(self, *args: Any, **kwargs: Any) -> None:
        _ORIGINAL_INIT(self, *args, **kwargs)
        self._http = _wrap_http_client(self._http)

    GeminiCloudCodeClient.__init__ = _patched_init
    _PATCHED = True
    logger.info("Antigravity interceptor installed (body + headers via proxy)")
    return True


def is_installed() -> bool:
    return _PATCHED
