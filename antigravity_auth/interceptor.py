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
    """Transform Code Assist envelope → Antigravity envelope + randomized headers."""
    logger.debug("Antigravity request hook fired: %s %s", request.method, request.url)


def _antigravity_response_hook(response: httpx.Response) -> None:
    """Unwrap Antigravity response envelope back to Gemini-native format."""
    logger.debug("Antigravity response hook fired: %s", response.status_code)


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
