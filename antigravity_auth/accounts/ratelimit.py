"""Rate limit handling: reason parsing, exponential backoff, and cooldowns."""
from __future__ import annotations

import random
import time
from typing import Any

from .state import (
    ManagedAccount,
    ModelFamily,
    HeaderStyle,
    RATE_LIMIT_REASON_QUOTA_EXHAUSTED,
    RATE_LIMIT_REASON_RATE_LIMIT_EXCEEDED,
    RATE_LIMIT_REASON_MODEL_CAPACITY_EXHAUSTED,
    RATE_LIMIT_REASON_SERVER_ERROR,
    RATE_LIMIT_REASON_UNKNOWN,
    RateLimitState,
)

# Backoff constants (from TS: accounts.ts lines 29-36)
QUOTA_EXHAUSTED_BACKOFFS = [60_000, 300_000, 1_800_000, 7_200_000]
RATE_LIMIT_EXCEEDED_BACKOFF = 30_000
MODEL_CAPACITY_EXHAUSTED_BASE_BACKOFF = 45_000
MODEL_CAPACITY_EXHAUSTED_JITTER_MAX = 30_000
SERVER_ERROR_BACKOFF = 20_000
UNKNOWN_BACKOFF = 60_000
MIN_BACKOFF_MS = 2_000

# Capacity retry constants (from endpoint fallback)
CAPACITY_RETRY_MAX = 3
CAPACITY_RETRY_BASE_MS = 1000
CAPACITY_RETRY_MAX_MS = 8000

# Cooldown constants
COOLDOWN_MS = 30_000
MAX_CONSECUTIVE_FAILURES = 5
FAILURE_TTL_MS = 3600_000  # 1 hour


def _now_ms() -> float:
  return time.time() * 1000


def _generate_jitter(max_jitter_ms: int) -> float:
  return random.random() * max_jitter_ms - (max_jitter_ms / 2)


def get_quota_key(family: ModelFamily, header_style: HeaderStyle, model: str | None = None) -> str:
  """Resolve the quota key for a given family, header style, and optional model."""
  if family == "claude":
    return "claude"
  base = "gemini-cli" if header_style == "gemini-cli" else "gemini-antigravity"
  if model:
    return f"{base}:{model}"
  return base


def parse_rate_limit_reason(
  reason: str | None,
  message: str | None,
  status: int | None = None,
) -> str:
  """Parse a rate limit reason from status code, reason string, and message.

  Mirrors TS: accounts.ts parseRateLimitReason()
  """
  # Status code checks
  if status == 529 or status == 503:
    return RATE_LIMIT_REASON_MODEL_CAPACITY_EXHAUSTED
  if status == 500:
    return RATE_LIMIT_REASON_SERVER_ERROR

  # Explicit reason string
  if reason:
    upper = reason.upper()
    if upper == "QUOTA_EXHAUSTED":
      return RATE_LIMIT_REASON_QUOTA_EXHAUSTED
    if upper == "RATE_LIMIT_EXCEEDED":
      return RATE_LIMIT_REASON_RATE_LIMIT_EXCEEDED
    if upper == "MODEL_CAPACITY_EXHAUSTED":
      return RATE_LIMIT_REASON_MODEL_CAPACITY_EXHAUSTED

  # Message text scanning
  if message:
    lower = message.lower()

    # Capacity / overloaded - check FIRST
    if "capacity" in lower or "overloaded" in lower or "resource exhausted" in lower:
      return RATE_LIMIT_REASON_MODEL_CAPACITY_EXHAUSTED

    # RPM / TPM
    if any(x in lower for x in ("per minute", "rate limit", "too many requests", "presque")):
      return RATE_LIMIT_REASON_RATE_LIMIT_EXCEEDED

    # Quota
    if "exhausted" in lower or "quota" in lower:
      return RATE_LIMIT_REASON_QUOTA_EXHAUSTED

  # Default 429
  if status == 429:
    return RATE_LIMIT_REASON_UNKNOWN

  return RATE_LIMIT_REASON_UNKNOWN


def calculate_backoff_ms(
  reason: str,
  consecutive_failures: int = 0,
  retry_after_ms: float | None = None,
) -> int:
  """Calculate backoff milliseconds for a given rate limit reason.

  Mirrors TS: accounts.ts calculateBackoffMs()
  """
  # Respect explicit Retry-After header if reasonable
  if retry_after_ms and retry_after_ms > 0:
    return max(int(retry_after_ms), MIN_BACKOFF_MS)

  if reason == RATE_LIMIT_REASON_QUOTA_EXHAUSTED:
    idx = min(consecutive_failures, len(QUOTA_EXHAUSTED_BACKOFFS) - 1)
    return QUOTA_EXHAUSTED_BACKOFFS[idx]
  if reason == RATE_LIMIT_REASON_RATE_LIMIT_EXCEEDED:
    return RATE_LIMIT_EXCEEDED_BACKOFF
  if reason == RATE_LIMIT_REASON_MODEL_CAPACITY_EXHAUSTED:
    return MODEL_CAPACITY_EXHAUSTED_BASE_BACKOFF + int(_generate_jitter(MODEL_CAPACITY_EXHAUSTED_JITTER_MAX))
  if reason == RATE_LIMIT_REASON_SERVER_ERROR:
    return SERVER_ERROR_BACKOFF

  return UNKNOWN_BACKOFF


def clear_expired_rate_limits(state: RateLimitState) -> None:
  """Remove rate limit entries that have expired."""
  now = _now_ms()
  for key in state.keys():
    val = state.get(key)
    if val is not None and now >= val:
      state.delete(key)


def is_rate_limited_for_quota_key(state: RateLimitState, key: str) -> bool:
  """Check if a specific quota key is rate limited."""
  reset_time = state.get(key)
  return reset_time is not None and _now_ms() < reset_time


def is_rate_limited_for_family(state: RateLimitState, family: ModelFamily, model: str | None = None) -> bool:
  """Check if an account is rate limited for a given family (both antigravity and gemini-cli)."""
  clear_expired_rate_limits(state)
  if family == "claude":
    return is_rate_limited_for_quota_key(state, "claude")
  antigravity_limited = is_rate_limited_for_header_style(state, family, "antigravity", model)
  cli_limited = is_rate_limited_for_header_style(state, family, "gemini-cli", model)
  return antigravity_limited and cli_limited


def is_rate_limited_for_header_style(
  state: RateLimitState,
  family: ModelFamily,
  header_style: HeaderStyle,
  model: str | None = None,
) -> bool:
  """Check if an account is rate limited for a specific header style."""
  clear_expired_rate_limits(state)
  if family == "claude":
    return is_rate_limited_for_quota_key(state, "claude")

  # Check model-specific quota first
  if model:
    model_key = get_quota_key(family, header_style, model)
    if is_rate_limited_for_quota_key(state, model_key):
      return True

  # Then check base family quota
  base_key = get_quota_key(family, header_style)
  return is_rate_limited_for_quota_key(state, base_key)


class RateLimitTracker:
  """Tracks rate limit state with time-window deduplication.

  Mirrors account manager rate limit handling from the original implementation.
  """

  def __init__(self) -> None:
    # Dedup window: track recent rate limit events per (account_index, quota_key)
    self._dedup_window: dict[str, float] = {}
    # Dedup window duration in ms
    self._dedup_window_ms: float = 5000

  def is_duplicate(self, account_index: int, quota_key: str) -> bool:
    """Check if this rate limit event is a duplicate within the dedup window."""
    dedup_key = f"{account_index}:{quota_key}"
    now = _now_ms()
    last_seen = self._dedup_window.get(dedup_key)
    if last_seen is not None and (now - last_seen) < self._dedup_window_ms:
      return True
    self._dedup_window[dedup_key] = now
    return False

  def clear(self) -> None:
    self._dedup_window.clear()


def mark_rate_limited(
  account: ManagedAccount,
  retry_after_ms: float,
  family: ModelFamily,
  header_style: HeaderStyle = "antigravity",
  model: str | None = None,
) -> None:
  """Mark an account as rate limited for a given quota key."""
  key = get_quota_key(family, header_style, model)
  account.rate_limit_reset_times.set(key, _now_ms() + retry_after_ms)


def mark_rate_limited_with_reason(
  account: ManagedAccount,
  family: ModelFamily,
  header_style: HeaderStyle,
  model: str | None,
  reason: str,
  retry_after_ms: float | None = None,
  failure_ttl_ms: float = FAILURE_TTL_MS,
) -> int:
  """Mark an account rate limited with a specific reason and calculate backoff.

  Returns the backoff time in ms.
  """
  now = _now_ms()

  # TTL-based reset: if last failure was more than failure_ttl_ms ago, reset count
  if account.last_failure_time is not None and (now - account.last_failure_time) > failure_ttl_ms:
    account.consecutive_failures = 0

  failures = (account.consecutive_failures or 0) + 1
  account.consecutive_failures = failures
  account.last_failure_time = now

  backoff_ms = calculate_backoff_ms(reason, failures - 1, retry_after_ms)
  key = get_quota_key(family, header_style, model)
  account.rate_limit_reset_times.set(key, now + backoff_ms)

  return backoff_ms


def is_account_cooling_down(account: ManagedAccount) -> bool:
  """Check if an account is in cooldown."""
  if account.cooling_down_until is None:
    return False
  if _now_ms() >= account.cooling_down_until:
    account.cooling_down_until = None
    account.cooldown_reason = None
    return False
  return True


def clear_account_cooldown(account: ManagedAccount) -> None:
  """Clear cooldown state for an account."""
  account.cooling_down_until = None
  account.cooldown_reason = None


def get_min_wait_time_for_family(
  accounts: list[ManagedAccount],
  family: ModelFamily,
  model: str | None = None,
  header_style: HeaderStyle | None = None,
  strict: bool = False,
) -> float:
  """Get minimum wait time until any account becomes available for a family."""
  if strict and header_style:
    available = [
      a for a in accounts
      if a.enabled is not False
      and not is_rate_limited_for_header_style(a.rate_limit_reset_times, family, header_style, model)
    ]
  else:
    available = [
      a for a in accounts
      if a.enabled is not False
      and not is_rate_limited_for_family(a.rate_limit_reset_times, family, model)
    ]

  if available:
    return 0

  now = _now_ms()
  wait_times: list[float] = []
  for a in accounts:
    state = a.rate_limit_reset_times
    if family == "claude":
      t = state.get("claude")
      if t is not None:
        wait_times.append(max(0.0, t - now))
    elif strict and header_style:
      key = get_quota_key(family, header_style, model)
      t = state.get(key)
      if t is not None:
        wait_times.append(max(0.0, t - now))
    else:
      antigravity_key = get_quota_key(family, "antigravity", model)
      cli_key = get_quota_key(family, "gemini-cli", model)
      t1 = state.get(antigravity_key)
      t2 = state.get(cli_key)
      account_wait = min(
        max(0.0, t1 - now) if t1 is not None else float("inf"),
        max(0.0, t2 - now) if t2 is not None else float("inf"),
      )
      if account_wait != float("inf"):
        wait_times.append(account_wait)

  return min(wait_times) if wait_times else 0.0
