"""HTTP interceptor — patches GeminiCloudCodeClient to transform request headers
via httpx event hooks. Body stays in Code Assist format (API may accept it)."""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from .config import get_config
from .transform.envelope import (
    build_antigravity_headers,
    resolve_model_for_header_style,
)

logger = logging.getLogger(__name__)

_PATCHED = False
_ORIGINAL_INIT = None


def _antigravity_request_hook(request: httpx.Request) -> None:
    """Transform headers to Antigravity style. Does NOT modify body."""
    if "cloudcode-pa.googleapis.com" not in str(request.url):
        return

    config = get_config()
    
    # Read body to determine model and header style
    try:
        body = json.loads(request.read())
    except Exception:
        return
    
    if not isinstance(body, dict) or "request" not in body:
        return
    
    model = str(body.get("model", ""))
    header_style = "gemini-cli" if config.cli_first else "antigravity"
    model = resolve_model_for_header_style(model, header_style)
    
    # Replace headers with Antigravity-style headers
    new_headers = build_antigravity_headers(header_style=header_style)
    for key in list(request.headers.keys()):
        if key.lower() not in ("host", "authorization", "content-type", "accept", "accept-encoding", "content-length"):
            del request.headers[key]
    for key, val in new_headers.items():
        request.headers[key] = val

    # Inject fingerprint
    try:
        from .fingerprint import generate_fingerprint
        fp = generate_fingerprint()
        if fp:
            ua = fp.get("userAgent")
            if ua and isinstance(ua, str):
                request.headers["User-Agent"] = ua
            cm = fp.get("clientMetadata")
            if cm:
                request.headers["Client-Metadata"] = json.dumps(cm)
    except Exception:
        pass

    logger.debug("Antigravity headers injected for model=%s", model)


def _antigravity_response_hook(response: httpx.Response) -> None:
    """Handle side effects (401 refresh, 429 rotation)."""
    from .config import get_config
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

    if response.status_code >= 500:
        try:
            from .endpoints import mark_endpoint_failed
            from urllib.parse import urlparse
            p = urlparse(str(response.request.url))
            mark_endpoint_failed(f"https://{p.netloc}")
        except Exception:
            pass


def _wrap_http_client(http_client: httpx.Client) -> httpx.Client:
    """Add request/response event hooks for header transformation."""
    if not http_client.event_hooks.get("request"):
        http_client.event_hooks["request"] = []
    if not http_client.event_hooks.get("response"):
        http_client.event_hooks["response"] = []
    http_client.event_hooks["request"].append(_antigravity_request_hook)
    http_client.event_hooks["response"].append(_antigravity_response_hook)
    return http_client


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
        _wrap_http_client(self._http)

    GeminiCloudCodeClient.__init__ = _patched_init
    _PATCHED = True
    logger.info("Antigravity interceptor installed (headers-only)")
    return True


def is_installed() -> bool:
    return _PATCHED
