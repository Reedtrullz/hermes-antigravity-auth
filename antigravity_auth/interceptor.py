"""HTTP interceptor — injects Antigravity headers via httpx event hooks."""

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
    if "cloudcode-pa" not in str(request.url):
        return

    config = get_config()
    
    try:
        body = json.loads(request.read())
    except Exception:
        return
    
    if not isinstance(body, dict) or "request" not in body:
        return
    
    model = str(body.get("model", ""))
    header_style = "gemini-cli" if config.cli_first else "antigravity"

    if header_style == "gemini-cli":
        logger.warning(
            "Gemini CLI header style is DEPRECATED — Gemini CLI sunsets 2026-06-18. "
            "Set cli_first: false in config to use the Antigravity header style."
        )

    model = resolve_model_for_header_style(model, header_style)
    
    new_headers = build_antigravity_headers(header_style=header_style)
    for key in list(request.headers.keys()):
        if key.lower() not in ("host", "authorization", "content-type", "accept", "accept-encoding", "content-length"):
            del request.headers[key]
    for key, val in new_headers.items():
        request.headers[key] = val

    try:
        from .fingerprint import generate_fingerprint
        fp = generate_fingerprint()
        if fp:
            cm = fp.get("clientMetadata")
            if cm:
                request.headers["Client-Metadata"] = json.dumps(cm)
    except Exception:
        pass

    logger.debug("Antigravity headers injected for model=%s", model)


def _antigravity_response_hook(response: httpx.Response) -> None:
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

    if response.status_code == 403:
        try:
            from .accounts.manager import AccountManager
            mgr = AccountManager.load_from_disk()
            active = mgr.get_current_account_for_family("gemini")
            if active:
                import time
                active.cooling_down_until = (time.time() + 86400) * 1000
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
                        logger.info("Rotated to %s after 403", next_acc.email)
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


def _wrap_http_client(http_client: httpx.Client) -> httpx.Client:
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
    logger.info("Antigravity interceptor installed (headers + response hooks)")
    return True


def is_installed() -> bool:
    return _PATCHED
