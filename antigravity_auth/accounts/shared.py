"""Shared singleton AccountManager for cross-module state consistency."""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .manager import AccountManager

_lock = threading.Lock()
_instance: AccountManager | None = None


def set_global_manager(manager: AccountManager) -> None:
    """Set the shared AccountManager singleton."""
    global _instance
    with _lock:
        _instance = manager


def get_global_manager() -> AccountManager | None:
    """Get the shared AccountManager singleton, or None if not initialized."""
    with _lock:
        return _instance


def get_or_create_global_manager() -> AccountManager:
    """Get or lazily create the shared AccountManager singleton."""
    global _instance
    with _lock:
        if _instance is None:
            from .manager import AccountManager
            _instance = AccountManager.load_from_disk()
        return _instance
