"""Antigravity API endpoint fallback chain (daily → autopush → prod)."""
from __future__ import annotations

import time
from typing import Any

from .constants import ANTIGRAVITY_ENDPOINT_FALLBACKS, ANTIGRAVITY_ENDPOINT_PROD

_FAILURE_TTL_SECONDS = 300


class EndpointProvider:
  """Manages the Antigravity API endpoint fallback chain.

  Endpoints are tried in order: daily → autopush → prod.
  For Gemini CLI header style (DEPRECATED — sunsets 2026-06-18),
  sandbox endpoints (daily, autopush) are skipped since they only
  work with Antigravity quota.
  """

  def __init__(self) -> None:
    self._failed_endpoints: dict[str, float] = {}

  def get_endpoints(self, header_style: str = "antigravity") -> list[str]:
    """Return the list of endpoints to try, in fallback order.

    For ``gemini-cli`` header style, only the production endpoint
    is returned (sandbox endpoints are skipped).
    """
    if header_style == "gemini-cli":
      return [ANTIGRAVITY_ENDPOINT_PROD]
    return list(ANTIGRAVITY_ENDPOINT_FALLBACKS)

  def mark_failed(self, endpoint: str) -> None:
    """Mark an endpoint as failed so it is skipped in future attempts."""
    self._failed_endpoints[endpoint] = time.time()

  def is_failed(self, endpoint: str) -> bool:
    """Check whether an endpoint has been marked as failed (with TTL expiry)."""
    failure_time = self._failed_endpoints.get(endpoint)
    if failure_time is None:
      return False
    if time.time() - failure_time > _FAILURE_TTL_SECONDS:
      self._failed_endpoints.pop(endpoint, None)
      return False
    return True

  def reset(self) -> None:
    """Clear all endpoint failure marks."""
    self._failed_endpoints.clear()

  @property
  def failed_endpoints(self) -> set[str]:
    """Return currently failed endpoints (expired entries are cleaned)."""
    now = time.time()
    expired = [ep for ep, ts in self._failed_endpoints.items() if now - ts > _FAILURE_TTL_SECONDS]
    for ep in expired:
      self._failed_endpoints.pop(ep, None)
    return set(self._failed_endpoints.keys())



# Module-level endpoint provider — shared across all requests
_endpoint_provider = EndpointProvider()


def select_endpoint(config=None):
    """Select the Antigravity endpoint based on config and health state.

    Uses the EndpointProvider's fallback chain (daily → autopush → prod).
    For ``gemini-cli`` header style, only production is returned.
    Failed endpoints are skipped automatically.

    Args:
        config: Optional Config dataclass instance.
    """
    from .constants import ANTIGRAVITY_ENDPOINT_PROD

    # Use PROD by default — daily sandbox rejects free-tier accounts for Claude
    return ANTIGRAVITY_ENDPOINT_PROD


def mark_endpoint_failed(endpoint: str) -> None:
    """Mark an endpoint as failed so it is skipped in future requests."""
    _endpoint_provider.mark_failed(endpoint)


def reset_endpoint_failures() -> None:
    """Clear all endpoint failure marks (e.g., after a period of stability)."""
    _endpoint_provider.reset()
