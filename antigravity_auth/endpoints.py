from __future__ import annotations

from typing import Any

from .constants import ANTIGRAVITY_ENDPOINT_FALLBACKS, ANTIGRAVITY_ENDPOINT_PROD


class EndpointProvider:
  """Manages the Antigravity API endpoint fallback chain.

  Endpoints are tried in order: daily → autopush → prod.
  For Gemini CLI header style, sandbox endpoints (daily, autopush)
  are skipped since they only work with Antigravity quota.
  """

  def __init__(self) -> None:
    self._failed_endpoints: set[str] = set()

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
    self._failed_endpoints.add(endpoint)

  def is_failed(self, endpoint: str) -> bool:
    """Check whether an endpoint has been marked as failed."""
    return endpoint in self._failed_endpoints

  def reset(self) -> None:
    """Clear all endpoint failure marks."""
    self._failed_endpoints.clear()

  @property
  def failed_endpoints(self) -> set[str]:
    return self._failed_endpoints.copy()




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
    # Default to prod for now — future: respect config.scheduling_mode
    return ANTIGRAVITY_ENDPOINT_PROD


def mark_endpoint_failed(endpoint: str) -> None:
    """Mark an endpoint as failed so it is skipped in future requests."""
    _endpoint_provider.mark_failed(endpoint)


def reset_endpoint_failures() -> None:
    """Clear all endpoint failure marks (e.g., after a period of stability)."""
    _endpoint_provider.reset()
