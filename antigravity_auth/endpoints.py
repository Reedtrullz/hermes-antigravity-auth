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


class CapacityRetryTracker:
  """Tracks per-endpoint capacity retry counts and backoff timing.

  When a server responds with a capacity error (429/503), the caller
  can retry the same endpoint up to ``max_retries`` times before
  moving to the next endpoint.

  Exponential backoff: 1s → 2s → 4s → … → ``max_backoff_ms``.
  """

  def __init__(
    self,
    max_retries: int = 3,
    base_backoff_ms: int = 1000,
    max_backoff_ms: int = 8000,
  ) -> None:
    self._max_retries = max_retries
    self._base_backoff_ms = base_backoff_ms
    self._max_backoff_ms = max_backoff_ms
    self._counts: dict[str, int] = {}
    self._last_endpoint: str | None = None

  def should_retry(self, endpoint: str) -> bool:
    """Check whether the endpoint can be retried (under max retries).

    Resets the counter when a different endpoint is passed,
    because switching endpoints means we exhausted the previous one.
    """
    if endpoint != self._last_endpoint:
      self._counts[endpoint] = 0
      self._last_endpoint = endpoint
    count = self._counts.get(endpoint, 0)
    return count < self._max_retries

  def record_retry(self, endpoint: str) -> int:
    """Increment the retry count for *endpoint* and return the new count."""
    if endpoint != self._last_endpoint:
      self._counts[endpoint] = 0
      self._last_endpoint = endpoint
    current = self._counts.get(endpoint, 0) + 1
    self._counts[endpoint] = current
    return current

  def get_backoff_ms(self, count: int) -> int:
    """Return deterministic exponential backoff for retry *count*.

    Backoff = min(base * 2^count, max_backoff_ms)
    """
    delay = self._base_backoff_ms * (2 ** count)
    return min(delay, self._max_backoff_ms)

  def get_backoff_ms_with_jitter(self, count: int) -> int:
    """Return backoff with ±10 % jitter to prevent thundering herd."""
    delay = self.get_backoff_ms(count)
    jitter = delay * (0.9 + random.random() * 0.2)
    return round(jitter)

  def reset(self, endpoint: str) -> None:
    """Reset the retry counter for a specific endpoint."""
    self._counts.pop(endpoint, None)
    if self._last_endpoint == endpoint:
      self._last_endpoint = None

  def reset_all(self) -> None:
    """Reset retry counters for all endpoints."""
    self._counts.clear()
    self._last_endpoint = None

  @property
  def retry_counts(self) -> dict[str, int]:
    return dict(self._counts)

  @property
  def max_retries(self) -> int:
    return self._max_retries

  @property
  def max_backoff_ms(self) -> int:
    return self._max_backoff_ms


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

    header_style = "gemini-cli" if (config is not None and config.cli_first) else "antigravity"
    endpoints = _endpoint_provider.get_endpoints(header_style)
    for endpoint in endpoints:
        if not _endpoint_provider.is_failed(endpoint):
            return endpoint
    # All endpoints failed — return prod as last resort
    return ANTIGRAVITY_ENDPOINT_PROD


def mark_endpoint_failed(endpoint: str) -> None:
    """Mark an endpoint as failed so it is skipped in future requests."""
    _endpoint_provider.mark_failed(endpoint)


def reset_endpoint_failures() -> None:
    """Clear all endpoint failure marks (e.g., after a period of stability)."""
    _endpoint_provider.reset()
