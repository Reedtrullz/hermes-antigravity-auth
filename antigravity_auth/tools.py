"""Hermes tool registration for Antigravity features."""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


def register_tools() -> None:
    """Register Antigravity tools with Hermes' tool registry.

    Uses Hermes' internal registry import. Gracefully degrades
    if Hermes is not available (e.g., during unit tests).
    """
    try:
        from tools.registry import registry
    except ImportError:
        logger.debug("Hermes tool registry not available — skipping tool registration")
        return

    _register_search_tool(registry)


def _register_search_tool(registry: Any) -> None:
    """Register execute_search as google_antigravity_search."""
    from .search import execute_search, SearchArgs
    from .storage import load_accounts

    def _search_handler(args: dict, **kw: Any) -> str:
        query = str(args.get("query", ""))
        urls: list[str] | None = args.get("urls")
        if isinstance(urls, str):
            urls = [str(urls)]
        elif not isinstance(urls, list):
            urls = None

        accounts_data = load_accounts()
        active_idx = accounts_data.get("activeIndex", 0)
        accounts = accounts_data.get("accounts", [])
        if not accounts:
            return json.dumps({"error": "No Antigravity accounts configured"})

        acc = accounts[active_idx]
        refresh_token = acc.get("refreshToken", "")
        if not refresh_token:
            return json.dumps({"error": "No refresh token for active account"})

        from .token import refresh_access_token
        refreshed = refresh_access_token({"refresh": refresh_token})
        access_token = refreshed.get("access", "")
        if not access_token:
            return json.dumps({"error": "Failed to refresh access token"})

        project_id = acc.get("projectId") or ""

        search_args = SearchArgs(query=query, urls=urls, thinking=True)
        return execute_search(search_args, access_token, project_id)

    def _check_requirements() -> bool:
        try:
            from .storage import load_accounts
            accounts_data = load_accounts()
            return len(accounts_data.get("accounts", [])) > 0
        except Exception:
            return False

    registry.register(
        name="google_antigravity_search",
        toolset="search",
        schema={
            "name": "google_antigravity_search",
            "description": (
                "Search the web using Google Search via Antigravity. "
                "Returns grounded results with citations. "
                "Optionally provide URLs to analyze specific pages."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The search query",
                    },
                    "urls": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional URLs to fetch and analyze",
                    },
                },
                "required": ["query"],
            },
        },
        handler=lambda args, **kw: _search_handler(args, **kw),
        check_fn=_check_requirements,
        requires_env=[],
    )
