"""AccountManager: in-memory multi-account selection with sticky rotation."""
from __future__ import annotations

import json
import os
import threading
import time
from typing import Any

from .._time_utils import now_ms
from ..constants import ANTIGRAVITY_DEFAULT_PROJECT_ID
from ..storage import get_accounts_json_path
from .ratelimit import (
    clear_expired_rate_limits,
    get_quota_key,
    is_rate_limited_for_family,
    is_rate_limited_for_header_style,
    is_account_cooling_down,
    mark_rate_limited,
    mark_rate_limited_with_reason,
    clear_account_cooldown,
)
from .rotation import HealthScoreTracker
from .state import (
    ManagedAccount,
    ModelFamily,
    HeaderStyle,
    RateLimitState,
    RefreshParts,
    AccountSelectionStrategy,
    CooldownReason,
)

SAVE_DEBOUNCE_MS = 1000
PID_OFFSET_MAX = 10


def _clamp_non_negative_int(value: Any, fallback: int) -> int:
  if not isinstance(value, (int, float)):
    return fallback
  return max(0, int(value))


def _read_accounts_file() -> dict[str, Any] | None:
  """Read accounts storage from the antigravity-accounts.json file."""
  path = get_accounts_json_path()
  if not path.exists():
    return None
  try:
    with open(path, "r", encoding="utf-8") as f:
      return json.load(f)
  except (json.JSONDecodeError, IOError):
    return None


def _write_accounts_file(data: dict[str, Any]) -> bool:
  """Atomically write accounts storage to disk."""
  path = get_accounts_json_path()
  temp_path = path.with_name(f"{path.name}.{os.getpid()}.tmp")
  try:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(temp_path, "w", encoding="utf-8") as f:
      json.dump(data, f, indent=2)
    os.replace(temp_path, path)
    os.chmod(path, 0o600)
    return True
  except (IOError, OSError):
    try:
      if temp_path.exists():
        temp_path.unlink()
    except OSError:
      pass
    return False


class AccountManager:
  """In-memory multi-account manager with sticky account selection.

  Uses the same account until it hits a rate limit, then switches.
  Rate limits are tracked per-model-family (claude/gemini).

  Mirrors TS: accounts.ts AccountManager
  """

  def __init__(self) -> None:
    self._accounts: list[ManagedAccount] = []
    self._cursor: int = 0
    self._current_account_by_family: dict[str, int] = {
      "claude": -1,
      "gemini": -1,
    }
    self._session_offset_applied: dict[str, bool] = {
      "claude": False,
      "gemini": False,
    }
    self._health_tracker = HealthScoreTracker()
    self._lock = threading.Lock()
    self._save_pending: bool = False
    self._save_timer: threading.Timer | None = None

  # ========== Account Loading ==========

  @classmethod
  def load_from_disk(cls) -> AccountManager:
    """Load accounts from the accounts storage file."""
    manager = cls()
    stored = _read_accounts_file()
    if stored is None:
      return manager
    manager._load_from_stored(stored)
    return manager

  def _load_from_stored(self, stored: dict[str, Any]) -> None:
    """Load accounts from stored JSON data."""
    accounts_data = stored.get("accounts", [])
    if not accounts_data:
      return

    base_now = now_ms()
    loaded: list[ManagedAccount] = []

    for idx, acc_data in enumerate(accounts_data):
      if not isinstance(acc_data, dict):
        continue
      refresh_token = acc_data.get("refreshToken")
      if not refresh_token or not isinstance(refresh_token, str):
        continue

      account = ManagedAccount(
        index=idx,
        refresh_parts=RefreshParts(
          refresh_token=refresh_token,
          project_id=acc_data.get("projectId"),
          managed_project_id=acc_data.get("managedProjectId"),
        ),
        email=acc_data.get("email"),
        added_at=_clamp_non_negative_int(acc_data.get("addedAt"), base_now),
        last_used=_clamp_non_negative_int(acc_data.get("lastUsed"), 0),
        enabled=acc_data.get("enabled", True) is not False,
        rate_limit_reset_times=RateLimitState.from_dict(
          acc_data.get("rateLimitResetTimes")
        ),
        cooling_down_until=acc_data.get("coolingDownUntil"),
        cooldown_reason=acc_data.get("cooldownReason"),
        fingerprint=acc_data.get("fingerprint"),
        fingerprint_history=acc_data.get("fingerprintHistory"),
        cached_quota=acc_data.get("cachedQuota"),
        cached_quota_updated_at=acc_data.get("cachedQuotaUpdatedAt"),
        verification_required=acc_data.get("verificationRequired", False),
        verification_required_at=acc_data.get("verificationRequiredAt"),
        verification_required_reason=acc_data.get("verificationRequiredReason"),
        verification_url=acc_data.get("verificationUrl"),
      )
      loaded.append(account)

    self._accounts = loaded
    self._cursor = _clamp_non_negative_int(stored.get("activeIndex", 0), 0)
    if self._accounts:
      self._cursor = self._cursor % len(self._accounts)
      default_index = self._cursor

      family_map = stored.get("activeIndexByFamily", {})
      if isinstance(family_map, dict):
        self._current_account_by_family["claude"] = (
          _clamp_non_negative_int(family_map.get("claude"), default_index)
          % len(self._accounts)
        )
        self._current_account_by_family["gemini"] = (
          _clamp_non_negative_int(family_map.get("gemini"), default_index)
          % len(self._accounts)
        )

  # ========== Account Queries ==========

  def get_account_count(self) -> int:
    return len(self.get_enabled_accounts())

  def get_total_account_count(self) -> int:
    return len(self._accounts)

  def get_enabled_accounts(self) -> list[ManagedAccount]:
    return [a for a in self._accounts if a.enabled is not False]

  def get_accounts_snapshot(self) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for a in self._accounts:
      d: dict[str, Any] = {
        "index": a.index,
        "email": a.email,
        "refresh_parts": {
          "refresh_token": a.refresh_parts.refresh_token,
          "project_id": a.refresh_parts.project_id,
          "managed_project_id": a.refresh_parts.managed_project_id,
        },
        "enabled": a.enabled,
        "last_used": a.last_used,
      }
      result.append(d)
    return result

  def get_current_account_for_family(self, family: ModelFamily) -> ManagedAccount | None:
    current_index = self._current_account_by_family.get(family, -1)
    if 0 <= current_index < len(self._accounts):
      account = self._accounts[current_index]
      if account and account.enabled is not False:
        return account
    return None

  def get_accounts(self) -> list[ManagedAccount]:
    return list(self._accounts)

  # ========== Account Selection ==========

  def get_current_or_next_for_family(
    self,
    family: ModelFamily,
    model: str | None = None,
    strategy: AccountSelectionStrategy = "sticky",
    header_style: HeaderStyle = "antigravity",
    pid_offset_enabled: bool = False,
    soft_quota_threshold_percent: float = 100,
    soft_quota_cache_ttl_ms: float = 600_000,
  ) -> ManagedAccount | None:
    quota_key = get_quota_key(family, header_style, model)

    if strategy == "round-robin":
      next_acc = self._get_next_for_family(
        family, model, header_style,
        soft_quota_threshold_percent, soft_quota_cache_ttl_ms,
      )
      if next_acc:
        self._mark_touched_for_quota(next_acc, quota_key)
        self._current_account_by_family[family] = next_acc.index
      return next_acc

    if strategy == "hybrid":
      return self._select_hybrid(family, model, header_style, quota_key,
                                  soft_quota_threshold_percent, soft_quota_cache_ttl_ms)

    # Sticky (default) strategy
    if pid_offset_enabled and len(self._accounts) > 1:
        with self._lock:
            if not self._session_offset_applied.get(family, False):
                import os as _os
                pid = _os.getpid()
                pid_offset = pid % len(self._accounts)
                base_index = self._current_account_by_family.get(family, 0)
                new_index = (base_index + pid_offset) % len(self._accounts)
                self._current_account_by_family[family] = new_index
                self._session_offset_applied[family] = True

    current = self.get_current_account_for_family(family)
    if current:
      clear_expired_rate_limits(current.rate_limit_reset_times)
      is_limited = is_rate_limited_for_header_style(
        current.rate_limit_reset_times, family, header_style, model
      )
      is_over = self._is_over_soft_quota(
        current, family, soft_quota_threshold_percent, soft_quota_cache_ttl_ms, model
      )
      if not is_limited and not is_over and not is_account_cooling_down(current):
        self._mark_touched_for_quota(current, quota_key)
        return current

    next_acc = self._get_next_for_family(
      family, model, header_style,
      soft_quota_threshold_percent, soft_quota_cache_ttl_ms,
    )
    if next_acc:
      self._mark_touched_for_quota(next_acc, quota_key)
      self._current_account_by_family[family] = next_acc.index
    return next_acc

  def _get_next_for_family(
    self,
    family: ModelFamily,
    model: str | None = None,
    header_style: HeaderStyle = "antigravity",
    soft_quota_threshold_percent: float = 100,
    soft_quota_cache_ttl_ms: float = 600_000,
  ) -> ManagedAccount | None:
    available = [
      a for a in self._accounts
      if a.enabled is not False
      and not is_rate_limited_for_header_style(a.rate_limit_reset_times, family, header_style, model)
      and not self._is_over_soft_quota(a, family, soft_quota_threshold_percent,
                                        soft_quota_cache_ttl_ms, model)
      and not is_account_cooling_down(a)
    ]

    if not available:
      return None

    idx = self._cursor % len(available)
    self._cursor += 1
    return available[idx]

  def _select_hybrid(
    self,
    family: ModelFamily,
    model: str | None,
    header_style: HeaderStyle,
    quota_key: str,
    soft_quota_threshold_percent: float,
    soft_quota_cache_ttl_ms: float,
  ) -> ManagedAccount | None:
    """Hybrid selection: prefer healthy, recently unused accounts."""
    candidates = [
      a for a in self._accounts
      if a.enabled is not False
      and not is_rate_limited_for_family(a.rate_limit_reset_times, family, model)
      and not self._is_over_quota_simple(a, family, soft_quota_threshold_percent,
                                          soft_quota_cache_ttl_ms, model)
      and not is_account_cooling_down(a)
    ]

    if not candidates:
      return None

    # Sort by health score (prefer healthier), then by last_used (prefer longer idle)
    def _sort_key(a: ManagedAccount) -> tuple[float, float]:
      score = self._health_tracker.get_score(a.index)
      return (-score, a.last_used)

    candidates.sort(key=_sort_key)
    selected = candidates[0]
    if selected:
      selected.last_used = now_ms()
      self._mark_touched_for_quota(selected, quota_key)
      self._current_account_by_family[family] = selected.index
    return selected

  def mark_account_used(self, account_index: int) -> None:
    for a in self._accounts:
      if a.index == account_index:
        a.last_used = now_ms()
        break

  def mark_switched(self, account: ManagedAccount, reason: str, family: ModelFamily) -> None:
    account.last_switch_reason = reason
    self._current_account_by_family[family] = account.index

  # ========== Rate Limit Operations ==========

  def mark_rate_limited(
    self,
    account: ManagedAccount,
    retry_after_ms: float,
    family: ModelFamily,
    header_style: HeaderStyle = "antigravity",
    model: str | None = None,
  ) -> None:
    mark_rate_limited(account, retry_after_ms, family, header_style, model)

  def mark_rate_limited_with_reason(
    self,
    account: ManagedAccount,
    family: ModelFamily,
    header_style: HeaderStyle,
    model: str | None,
    reason: str,
    retry_after_ms: float | None = None,
    failure_ttl_ms: float = 3600_000,
  ) -> int:
    result = mark_rate_limited_with_reason(
      account, family, header_style, model, reason, retry_after_ms, failure_ttl_ms,
    )
    self._health_tracker.record_rate_limit(account.index)
    return result

  def mark_request_success(self, account: ManagedAccount) -> None:
    if account.consecutive_failures:
      account.consecutive_failures = 0
      self._health_tracker.record_success(account.index)

  def has_other_account_with_antigravity_available(
    self,
    current_account_index: int,
    family: ModelFamily,
    model: str | None = None,
  ) -> bool:
    if family == "claude":
      return False
    return any(
      a.index != current_account_index
      and a.enabled is not False
      and not is_account_cooling_down(a)
      and not is_rate_limited_for_header_style(a.rate_limit_reset_times, family, "antigravity", model)
      for a in self._accounts
    )

  # ========== Account Management ==========

  def set_account_enabled(self, account_index: int, enabled: bool) -> bool:
    account = self._accounts[account_index] if 0 <= account_index < len(self._accounts) else None
    if account is None:
      return False
    account.enabled = enabled
    if not enabled:
      for family in ("claude", "gemini"):
        if self._current_account_by_family.get(family) == account_index:
          next_acc = next(
            (a for i, a in enumerate(self._accounts) if i != account_index and a.enabled is not False),
            None,
          )
          self._current_account_by_family[family] = next_acc.index if next_acc else -1
    self._request_save_to_disk()
    return True

  def remove_account(self, account_index: int) -> bool:
    if account_index < 0 or account_index >= len(self._accounts):
      return False
    self._accounts.pop(account_index)
    # Reindex
    for i, a in enumerate(self._accounts):
      a.index = i
    if not self._accounts:
      self._cursor = 0
      self._current_account_by_family["claude"] = -1
      self._current_account_by_family["gemini"] = -1
      return True
    if self._cursor > account_index:
      self._cursor -= 1
    self._cursor = self._cursor % len(self._accounts)
    for family in ("claude", "gemini"):
      idx = self._current_account_by_family.get(family, 0)
      if idx > account_index:
        idx -= 1
      if idx >= len(self._accounts):
        idx = -1
      self._current_account_by_family[family] = idx
    self._request_save_to_disk()
    return True

  # ========== Persistence ==========

  def save_to_disk(self) -> bool:
    claude_index = max(0, self._current_account_by_family.get("claude", 0))
    gemini_index = max(0, self._current_account_by_family.get("gemini", 0))

    accounts_data: list[dict[str, Any]] = []
    for a in self._accounts:
      acc_dict: dict[str, Any] = {
        "email": a.email,
        "refreshToken": a.refresh_parts.refresh_token,
        "projectId": a.refresh_parts.project_id,
        "managedProjectId": a.refresh_parts.managed_project_id,
        "addedAt": a.added_at,
        "lastUsed": a.last_used,
        "enabled": a.enabled,
        "lastSwitchReason": a.last_switch_reason,
      }

      rl_dict = a.rate_limit_reset_times.to_dict()
      if rl_dict:
        acc_dict["rateLimitResetTimes"] = rl_dict

      if a.cooling_down_until is not None:
        acc_dict["coolingDownUntil"] = a.cooling_down_until
        acc_dict["cooldownReason"] = a.cooldown_reason

      if a.fingerprint:
        acc_dict["fingerprint"] = a.fingerprint
      if a.fingerprint_history:
        acc_dict["fingerprintHistory"] = a.fingerprint_history
      if a.cached_quota:
        acc_dict["cachedQuota"] = a.cached_quota
        acc_dict["cachedQuotaUpdatedAt"] = a.cached_quota_updated_at
      if a.verification_required:
        acc_dict["verificationRequired"] = True
        acc_dict["verificationRequiredAt"] = a.verification_required_at
        acc_dict["verificationRequiredReason"] = a.verification_required_reason
        acc_dict["verificationUrl"] = a.verification_url

      accounts_data.append(acc_dict)

    storage = {
      "version": 4,
      "accounts": accounts_data,
      "activeIndex": claude_index,
      "activeIndexByFamily": {
        "claude": claude_index,
        "gemini": gemini_index,
      },
    }
    return _write_accounts_file(storage)

  def _request_save_to_disk(self) -> None:
    with self._lock:
      if self._save_timer is not None:
        self._save_timer.cancel()
        self._save_timer = None
      self._save_pending = True

    def _do_save():
      self.save_to_disk()
      with self._lock:
        self._save_pending = False
        self._save_timer = None

    timer = threading.Timer(SAVE_DEBOUNCE_MS / 1000, _do_save)
    timer.daemon = True
    self._save_timer = timer
    timer.start()

  # ========== Quota Cache ==========

  def update_quota_cache(
    self,
    account_index: int,
    quota_groups: dict[str, dict[str, Any]],
  ) -> None:
    account = self._accounts[account_index] if 0 <= account_index < len(self._accounts) else None
    if account:
      account.cached_quota = quota_groups
      account.cached_quota_updated_at = now_ms()

  def _is_over_soft_quota(
    self,
    account: ManagedAccount,
    family: ModelFamily,
    threshold_percent: float,
    cache_ttl_ms: float,
    model: str | None = None,
  ) -> bool:
    """Check if account exceeds the soft quota threshold."""
    if threshold_percent >= 100:
      return False
    if not account.cached_quota:
      return False
    if account.cached_quota_updated_at is None:
      return False
    age = now_ms() - account.cached_quota_updated_at
    if age > cache_ttl_ms:
      return False

    from .quota import resolve_quota_group
    quota_group = resolve_quota_group(family, model)
    group_data = account.cached_quota.get(quota_group)
    if group_data is None:
      return False
    remaining_fraction = group_data.get("remainingFraction")
    if remaining_fraction is None:
      return False
    remaining_fraction = max(0.0, min(1.0, float(remaining_fraction)))
    used_percent = (1 - remaining_fraction) * 100
    return used_percent >= threshold_percent

  def _is_over_quota_simple(
    self,
    account: ManagedAccount,
    family: ModelFamily,
    threshold_percent: float,
    cache_ttl_ms: float,
    model: str | None = None,
  ) -> bool:
    """Simplified quota check - uses same logic as _is_over_soft_quota."""
    return self._is_over_soft_quota(account, family, threshold_percent, cache_ttl_ms, model)

  # ========== Health Tracker ==========

  @property
  def health_tracker(self) -> HealthScoreTracker:
    return self._health_tracker

  def _mark_touched_for_quota(self, account: ManagedAccount, quota_key: str) -> None:
    account.touched_for_quota[quota_key] = now_ms()
