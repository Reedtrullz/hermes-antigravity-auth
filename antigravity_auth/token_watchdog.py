"""Background token refresh watchdog.

Polls Hermes' OAuth token store periodically and refreshes
tokens before they expire (buffer: proactive_refresh_buffer_seconds,
default 1800s = 30 minutes).
"""

from __future__ import annotations

import logging
import threading
import time

logger = logging.getLogger(__name__)

_watchdog_thread: threading.Thread | None = None
_watchdog_stop: threading.Event = threading.Event()


def _watchdog_loop() -> None:
    """Background loop: check token expiry, refresh if needed."""
    from .config import get_config

    from .config import DEFAULT_CONFIG

    check_interval = DEFAULT_CONFIG.proactive_refresh_check_interval_seconds

    while not _watchdog_stop.is_set():
        try:
            config = get_config()
            check_interval = config.proactive_refresh_check_interval_seconds
            if not config.proactive_token_refresh:
                _watchdog_stop.wait(check_interval)
                continue

            _refresh_if_needed(config)
        except Exception as exc:
            logger.debug("Token watchdog error: %s", exc)

        _watchdog_stop.wait(check_interval)


def _refresh_if_needed(config) -> None:
    """Check the active account's token and refresh if within buffer window."""
    try:
        from .storage import load_accounts
        from .token import format_refresh_parts, refresh_access_token
        from agent.google_oauth import load_credentials as load_google_creds
    except ImportError:
        return  # Hermes not available

    creds = load_google_creds()
    if not creds or not creds.refresh_token:
        return

    now_ms = int(time.time() * 1000)
    expires_ms = getattr(creds, "expires_ms", 0) or 0
    buffer_ms = config.proactive_refresh_buffer_seconds * 1000

    if expires_ms > 0 and (expires_ms - now_ms) > buffer_ms:
        return  # Token still fresh

    # Token is within buffer window or expired — refresh
    try:
        from .auth_sync import sync_token_to_google_oauth
        accounts_data = load_accounts()
        active_idx = accounts_data.get("activeIndex", 0)
        accounts = accounts_data.get("accounts", [])

        if 0 <= active_idx < len(accounts):
            acc = accounts[active_idx]
            raw_refresh = acc.get("refreshToken", "")
            if not raw_refresh:
                return
            packed_refresh = format_refresh_parts({
                "refreshToken": raw_refresh,
                "projectId": acc.get("projectId") or "",
                "managedProjectId": acc.get("managedProjectId") or "",
            })
            refreshed = refresh_access_token({"refresh": packed_refresh, "email": acc.get("email")})
            new_token = refreshed.get("access", "")
            if new_token:
                synced_refresh = refreshed.get("refresh") or packed_refresh
                sync_token_to_google_oauth(
                    access_token=new_token,
                    refresh_token=synced_refresh,
                    project_id=acc.get("projectId") or "",
                    email=acc.get("email"),
                    expires_ms=refreshed.get("expires"),
                )
                logger.debug("Proactively refreshed token for %s", acc.get("email", "unknown"))
    except Exception as exc:
        logger.debug("Proactive token refresh failed: %s", exc)


def start_watchdog() -> None:
    """Start the background token refresh thread. Idempotent."""
    global _watchdog_thread
    if _watchdog_thread is not None and _watchdog_thread.is_alive():
        return
    _watchdog_stop.clear()
    _watchdog_thread = threading.Thread(
        target=_watchdog_loop, daemon=True, name="antigravity-token-watchdog"
    )
    _watchdog_thread.start()
    from .config import DEFAULT_CONFIG
    interval = DEFAULT_CONFIG.proactive_refresh_check_interval_seconds
    logger.info("Token watchdog started (interval=%ss)", interval)


def stop_watchdog() -> None:
    """Stop the background token refresh thread."""
    _watchdog_stop.set()
    global _watchdog_thread
    if _watchdog_thread is not None:
        _watchdog_thread.join(timeout=5)
        _watchdog_thread = None
