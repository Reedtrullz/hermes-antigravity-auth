"""HTTP transport interceptor — monkey-patches GeminiCloudCodeClient to
install Antigravity request/response transformation hooks via httpx event_hooks."""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_PATCHED = False
_ORIGINAL_INIT = None


def _antigravity_request_hook(request: httpx.Request) -> None:
    """Transform Code Assist envelope → Antigravity envelope + randomized headers.

    The native GeminiCloudCodeClient wraps requests as:
        {"project": "...", "model": "...", "user_prompt_id": "...", "request": {...}}

    We rewrite this to the Antigravity envelope:
        {"project": "...", "model": "...", "userAgent": "antigravity",
         "requestId": "agent-<uuid>", "requestType": "agent", "request": {...}}

    Headers are also replaced with randomized Antigravity-style headers.
    """
    from .transform.envelope import (
        build_antigravity_headers,
        build_antigravity_envelope,
        resolve_model_for_header_style,
    )
    from .config import get_config

    # Only transform requests going to the Cloud Code endpoint
    if "cloudcode-pa.googleapis.com" not in str(request.url):
        return

    try:
        body = json.loads(request.content)
    except (json.JSONDecodeError, TypeError):
        return

    # Only transform requests that look like Code Assist envelopes
    if not isinstance(body, dict) or "request" not in body:
        return

    config = get_config()
    model = str(body.get("model", ""))
    project_id = str(body.get("project", ""))
    inner = body["request"]

    header_style = "gemini-cli" if config.cli_first else "antigravity"
    model = resolve_model_for_header_style(model, header_style)

    # Build the Antigravity envelope
    envelope = build_antigravity_envelope(
        request_payload=inner,
        model=model,
        project_id=project_id,
        header_style=header_style,
    )

    # --- Endpoint fallback chain (daily → autopush → prod) ---
    from .constants import (
        ANTIGRAVITY_ENDPOINT_PROD,
    )
    from .endpoints import select_endpoint

    # Rewrite URL to use Antigravity endpoint
    endpoint = select_endpoint(config)
    old_url = str(request.url)
    new_url = old_url.replace(
        "https://cloudcode-pa.googleapis.com", endpoint
    )
    if new_url != old_url:
        request.url = httpx.URL(new_url)

    # Replace the body
    request.content = json.dumps(envelope).encode("utf-8")

    # --- Strip Claude thinking blocks when keep_thinking=False ---
    from .transform import is_claude_model, strip_all_thinking_blocks

    if is_claude_model(model) and not config.keep_thinking:
        inner = envelope.get("request") if isinstance(envelope, dict) else {}
        if isinstance(inner, dict) and "contents" in inner:
            strip_all_thinking_blocks(inner["contents"])
            # Re-serialize after stripping
            request.content = json.dumps(envelope).encode("utf-8")

    # --- Sanitize tool schemas when claude_tool_hardening enabled ---
    from .transform.schema import clean_json_schema

    if config.claude_tool_hardening:
        inner = envelope.get("request") if isinstance(envelope, dict) else {}
        tools = inner.get("tools") if isinstance(inner, dict) else None
        if isinstance(tools, list):
            for tool in tools:
                if isinstance(tool, dict):
                    # Gemini format: tools have functionDeclarations
                    func_decls = tool.get("functionDeclarations")
                    if isinstance(func_decls, list):
                        for fd in func_decls:
                            if isinstance(fd, dict) and "parameters" in fd:
                                fd["parameters"] = clean_json_schema(fd["parameters"])
                    # OpenAI format: tools have parameters directly
                    elif "parameters" in tool:
                        tool["parameters"] = clean_json_schema(tool["parameters"])
            request.content = json.dumps(envelope).encode("utf-8")

    # Replace headers with randomized Antigravity headers
    new_headers = build_antigravity_headers(header_style=header_style)
    for key in list(request.headers.keys()):
        if key.lower() not in ("host", "content-type", "content-length", "accept-encoding"):
            del request.headers[key]
    for key, val in new_headers.items():
        request.headers[key] = val

    logger.debug("Transformed request to Antigravity envelope for model=%s", model)


def _antigravity_response_hook(response: httpx.Response) -> None:
    """Handle Antigravity-specific response quirks.

    The Antigravity response envelope {"response": {"candidates": [...]}}
    is already handled by _translate_gemini_response's inner-unwrap logic
    in Hermes' gemini_cloudcode_adapter.py.

    This hook handles:
    - Preview access errors → rewrite to clearer messages
    - Non-200 responses with Antigravity error envelopes
    """
    # --- Token refresh on 401 ---
    if response.status_code == 401:
        from .token import refresh_access_token
        from .storage import load_accounts
        from .config import get_config

        config = get_config()
        if config.proactive_token_refresh:
            accounts_data = load_accounts()
            active_idx = accounts_data.get("activeIndex", 0)
            accounts = accounts_data.get("accounts", [])

            if 0 <= active_idx < len(accounts):
                acc = accounts[active_idx]
                refresh = acc.get("refreshToken", "")
                if refresh:
                    try:
                        refreshed = refresh_access_token({"refresh": refresh})
                        new_token = refreshed.get("access", "")
                        if new_token:
                            # Sync to Hermes' OAuth store so next request gets fresh token
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
                                pass  # sync is best-effort
                            logger.info("Token refreshed after 401 for %s", acc.get("email"))
                    except Exception as exc:
                        logger.warning("Token refresh failed after 401: %s", exc)

    from .transform.response import rewrite_preview_access_error

    if not response.is_success:
        return

    try:
        body = json.loads(response.content)
    except (json.JSONDecodeError, TypeError):
        return

    if not isinstance(body, dict):
        return

    # Check for Antigravity-specific error patterns inside the response envelope
    response_inner = body.get("response")
    inner: dict[str, Any] = response_inner if isinstance(response_inner, dict) else body
    error: dict[str, Any] | None = inner.get("error") if isinstance(inner.get("error"), dict) else None

    if error is not None:
        # Rewrite preview access errors to more actionable messages
        rewritten = rewrite_preview_access_error(inner, response.status_code, None)
        if rewritten is not None:
            inner["error"] = rewritten.get("error", inner.get("error", {}))
            new_content = json.dumps(body).encode("utf-8")
            response._content = new_content

    # --- Session recovery: detect recoverable errors ---
    from .recovery import detect_error_type, is_recoverable_error
    from .config import get_config

    config = get_config()
    if config.session_recovery:
      # Check for recoverable errors in the response
      error_obj = inner.get("error") if isinstance(inner, dict) else None
      if error_obj and is_recoverable_error(error_obj):
        error_type = detect_error_type(error_obj)
        logger.info("Detected recoverable error: %s", error_type)

    logger.debug("Antigravity response processed: %s", response.status_code)


def _wrap_http_client(http_client: httpx.Client) -> httpx.Client:
    """Create a new httpx.Client that wraps the original with Antigravity hooks.

    Preserves the original transport (keepalive, connection pooling).
    """
    return httpx.Client(
        event_hooks={
            "request": [_antigravity_request_hook],
            "response": [_antigravity_response_hook],
        },
        transport=http_client._transport,
    )


def install() -> bool:
    """Monkey-patch GeminiCloudCodeClient.__init__ to wrap self._http.

    Safe to call multiple times — only patches once.
    Returns True if the patch was applied, False if already patched or
    GeminiCloudCodeClient is not importable.
    """
    global _PATCHED, _ORIGINAL_INIT
    if _PATCHED:
        return False
    try:
        from agent.gemini_cloudcode_adapter import GeminiCloudCodeClient
    except ImportError:
        logger.warning(
            "GeminiCloudCodeClient not importable — is hermes-agent installed?"
        )
        return False
    _ORIGINAL_INIT = GeminiCloudCodeClient.__init__

    def _patched_init(self, *args: Any, **kwargs: Any) -> None:
        _ORIGINAL_INIT(self, *args, **kwargs)
        self._http = _wrap_http_client(self._http)

    GeminiCloudCodeClient.__init__ = _patched_init
    _PATCHED = True
    logger.info("Antigravity HTTP interceptor installed")
    return True


def is_installed() -> bool:
    """Return whether the interceptor has been installed."""
    return _PATCHED
