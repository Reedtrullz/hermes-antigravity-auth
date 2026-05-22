"""Dataclasses for ManagedAccount, RateLimitState, and rate limit constants."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..constants import ANTIGRAVITY_ENDPOINT_FALLBACKS


ModelFamily = str  # "claude" | "gemini"
HeaderStyle = str  # "antigravity" | "gemini-cli"
AccountSelectionStrategy = str  # "sticky" | "round-robin" | "hybrid"
CooldownReason = str  # "auth-failure" | "network-error" | "project-error" | "validation-required"
RateLimitReason = str  # see RateLimitReason values below
QuotaKey = str  # "claude" | "gemini-antigravity" | "gemini-cli" | f"{base}:{model}"

RATE_LIMIT_REASON_QUOTA_EXHAUSTED = "QUOTA_EXHAUSTED"
RATE_LIMIT_REASON_RATE_LIMIT_EXCEEDED = "RATE_LIMIT_EXCEEDED"
RATE_LIMIT_REASON_MODEL_CAPACITY_EXHAUSTED = "MODEL_CAPACITY_EXHAUSTED"
RATE_LIMIT_REASON_SERVER_ERROR = "SERVER_ERROR"
RATE_LIMIT_REASON_UNKNOWN = "UNKNOWN"


@dataclass
class RateLimitState:
  """Per-quota-key rate limit reset times (epoch ms)."""
  claude: float | None = None
  gemini_antigravity: float | None = None
  gemini_cli: float | None = None
  # Model-specific keys: "gemini-antigravity:gemini-3-flash-preview" etc.
  extras: dict[str, float] = field(default_factory=dict)

  def get(self, key: str) -> float | None:
    if key == "claude":
      return self.claude
    if key == "gemini-antigravity":
      return self.gemini_antigravity
    if key == "gemini-cli":
      return self.gemini_cli
    return self.extras.get(key)

  def set(self, key: str, value: float) -> None:
    if key == "claude":
      self.claude = value
    elif key == "gemini-antigravity":
      self.gemini_antigravity = value
    elif key == "gemini-cli":
      self.gemini_cli = value
    else:
      self.extras[key] = value

  def delete(self, key: str) -> None:
    if key == "claude":
      self.claude = None
    elif key == "gemini-antigravity":
      self.gemini_antigravity = None
    elif key == "gemini-cli":
      self.gemini_cli = None
    else:
      self.extras.pop(key, None)

  def keys(self) -> list[str]:
    result: list[str] = []
    if self.claude is not None:
      result.append("claude")
    if self.gemini_antigravity is not None:
      result.append("gemini-antigravity")
    if self.gemini_cli is not None:
      result.append("gemini-cli")
    result.extend(self.extras.keys())
    return result

  def to_dict(self) -> dict[str, float]:
    d: dict[str, float] = {}
    if self.claude is not None:
      d["claude"] = self.claude
    if self.gemini_antigravity is not None:
      d["gemini-antigravity"] = self.gemini_antigravity
    if self.gemini_cli is not None:
      d["gemini-cli"] = self.gemini_cli
    d.update(self.extras)
    return d

  @classmethod
  def from_dict(cls, data: dict[str, float] | None) -> RateLimitState:
    state = cls()
    if not data:
      return state
    state.claude = data.get("claude")
    state.gemini_antigravity = data.get("gemini-antigravity")
    state.gemini_cli = data.get("gemini-cli")
    for k, v in data.items():
      if k not in ("claude", "gemini-antigravity", "gemini-cli"):
        state.extras[k] = v
    return state


@dataclass
class RefreshParts:
  refresh_token: str
  project_id: str | None = None
  managed_project_id: str | None = None


@dataclass
class ManagedAccount:
  index: int
  refresh_parts: RefreshParts
  email: str | None = None
  added_at: float = 0.0
  last_used: float = 0.0
  access: str | None = None
  expires: float | None = None
  enabled: bool = True
  rate_limit_reset_times: RateLimitState = field(default_factory=RateLimitState)
  last_switch_reason: str | None = None
  cooling_down_until: float | None = None
  cooldown_reason: CooldownReason | None = None
  consecutive_failures: int = 0
  last_failure_time: float | None = None
  touched_for_quota: dict[str, float] = field(default_factory=dict)
  fingerprint: dict[str, Any] | None = None
  fingerprint_history: list[dict[str, Any]] | None = None
  cached_quota: dict[str, dict[str, Any]] | None = None
  cached_quota_updated_at: float | None = None
  verification_required: bool = False
  verification_required_at: float | None = None
  verification_required_reason: str | None = None
  verification_url: str | None = None
