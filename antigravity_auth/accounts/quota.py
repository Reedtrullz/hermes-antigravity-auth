"""Dual quota pool classification, soft threshold checks, and live quota API."""
from __future__ import annotations

import json
import time
import urllib.request
from typing import Any

from ..constants import ANTIGRAVITY_ENDPOINT_PROD

FETCH_TIMEOUT_MS = 10000

QuotaGroup = str  # "claude" | "gemini-pro" | "gemini-flash"


def _now_ms() -> float:
  return time.time() * 1000


def normalize_remaining_fraction(value: Any) -> float:
  if not isinstance(value, (int, float)):
    return 0.0
  if value < 0:
    return 0.0
  if value > 1:
    return 1.0
  return float(value)


def parse_reset_time(reset_time: str | None) -> float | None:
  if not reset_time:
    return None
  try:
    parsed = _parse_iso_timestamp(reset_time)
    return parsed
  except (ValueError, OverflowError):
    return None


def _parse_iso_timestamp(ts: str) -> float:
  """Parse an ISO 8601 timestamp string to epoch ms.

  Supports formats like "2026-05-20T12:00:00Z" and
  "2026-05-20T12:00:00.123456Z".
  """
  # Python 3.11+ has fromisoformat, but we need to handle Z suffix
  # and fractional seconds
  import datetime
  # Handle Z suffix
  normalized = ts.replace("Z", "+00:00")
  try:
    dt = datetime.datetime.fromisoformat(normalized)
    return dt.timestamp() * 1000
  except ValueError:
    return 0.0


def classify_quota_group(model_name: str, display_name: str | None = None) -> QuotaGroup | None:
  """Classify a model name into a quota group.

  Mirrors TS: quota.ts classifyQuotaGroup()
  """
  combined = f"{model_name} {display_name or ''}".lower()
  if "claude" in combined:
    return "claude"
  is_gemini_3 = "gemini-3" in combined or "gemini 3" in combined
  if not is_gemini_3:
    return None
  # Use model family to determine flash vs pro
  if "flash" in combined:
    return "gemini-flash"
  return "gemini-pro"


def compute_soft_quota_cache_ttl_ms(
  ttl_config: str | int,
  refresh_interval_minutes: int,
) -> float:
  """Compute the soft quota cache TTL in milliseconds.

  Mirrors TS: accounts.ts computeSoftQuotaCacheTtlMs()
  """
  if ttl_config == "auto":
    return max(2 * refresh_interval_minutes, 10) * 60 * 1000
  return int(ttl_config) * 60 * 1000


def is_over_soft_quota_threshold(
  cached_quota: dict[str, dict[str, Any]] | None,
  cached_quota_updated_at: float | None,
  family: str,
  threshold_percent: float,
  cache_ttl_ms: float,
  model: str | None = None,
) -> bool:
  """Check if an account is over the soft quota threshold.

  Mirrors TS: accounts.ts isOverSoftQuotaThreshold()
  """
  if threshold_percent >= 100:
    return False
  if not cached_quota:
    return False
  if cached_quota_updated_at is None:
    return False
  age = _now_ms() - cached_quota_updated_at
  if age > cache_ttl_ms:
    return False

  quota_group = resolve_quota_group(family, model)
  group_data = cached_quota.get(quota_group)
  if group_data is None:
    return False
  remaining_fraction = group_data.get("remainingFraction")
  if remaining_fraction is None:
    return False

  remaining_fraction = max(0.0, min(1.0, float(remaining_fraction)))
  used_percent = (1 - remaining_fraction) * 100
  return used_percent >= threshold_percent


def resolve_quota_group(family: str, model: str | None = None) -> QuotaGroup:
  """Resolve the quota group for a given family and optional model.

  Mirrors TS: accounts.ts resolveQuotaGroup()
  """
  if model:
    classified = classify_quota_group(model)
    if classified:
      return classified
  return "claude" if family == "claude" else "gemini-pro"


def fetch_quota_from_api(access_token: str, project_id: str) -> list | None:
    """Fetch live quota data from the Antigravity API.

    Calls v1internal:retrieveUserQuota. Returns list of bucket dicts
    with modelId, remainingFraction, resetTime, or None on failure.
    """
    import json
    import urllib.request
    from ..constants import ANTIGRAVITY_ENDPOINT_PROD, get_antigravity_headers

    url = f"{ANTIGRAVITY_ENDPOINT_PROD}/v1internal:retrieveUserQuota"
    headers = get_antigravity_headers()
    headers["Authorization"] = f"Bearer {access_token}"
    headers["Content-Type"] = "application/json"

    envelope = json.dumps({"project": project_id}).encode("utf-8")

    try:
        req = urllib.request.Request(url, data=envelope, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="ignore"))
            return data.get("buckets") or []
    except Exception:
        return None
