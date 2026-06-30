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
    from .search import execute_search, SearchArgs, filter_search_urls
    from .storage import load_accounts, resolve_active_account_index

    def _search_handler(args: dict, **kw: Any) -> str:
        query = str(args.get("query", ""))
        urls = filter_search_urls(args.get("urls")) or None

        accounts_data = load_accounts()
        accounts = accounts_data.get("accounts", [])
        if not isinstance(accounts, list) or not accounts:
            return json.dumps({"error": "No Antigravity accounts configured"})

        active_idx = resolve_active_account_index(accounts_data, family="gemini")
        acc = accounts[active_idx]
        refresh_token = acc.get("refreshToken", "")
        if not refresh_token:
            return json.dumps({"error": "No refresh token for active account"})

        from .token import format_refresh_parts, refresh_access_token
        packed_refresh = format_refresh_parts({
            "refreshToken": refresh_token,
            "projectId": acc.get("projectId") or "",
            "managedProjectId": acc.get("managedProjectId") or "",
        })
        refreshed = refresh_access_token(
            {"refresh": packed_refresh, "email": acc.get("email")},
            persist=True,
            set_active=True,
        )
        access_token = refreshed.get("access", "")
        if not access_token:
            return json.dumps({"error": "Failed to refresh access token"})

        project_id = acc.get("projectId") or acc.get("managedProjectId") or ""

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
