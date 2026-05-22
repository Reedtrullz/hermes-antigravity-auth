"""HealthScoreTracker: scores accounts by success rate for rotation decisions."""
from __future__ import annotations

import time
from typing import Any


DEFAULT_HEALTH_SCORE_CONFIG: dict[str, float] = {
  "initial": 70,
  "success_reward": 1,
  "rate_limit_penalty": -10,
  "failure_penalty": -20,
  "recovery_rate_per_hour": 2,
  "min_usable": 50,
  "max_score": 100,
}


class HealthScoreTracker:
  """Tracks health scores for accounts. Higher score = healthier account.

  Mirrors TS: rotation.ts HealthScoreTracker
  """

  def __init__(self, config: dict[str, Any] | None = None) -> None:
    self._config = {**DEFAULT_HEALTH_SCORE_CONFIG, **(config or {})}
    self._scores: dict[int, dict[str, Any]] = {}

  def get_score(self, account_index: int) -> float:
    state = self._scores.get(account_index)
    if state is None:
      return self._config["initial"]
    now = _now_ms()
    hours_since_update = (now - state["last_updated"]) / (1000 * 60 * 60)
    recovered = int(hours_since_update * self._config["recovery_rate_per_hour"])
    return min(self._config["max_score"], state["score"] + recovered)

  def record_success(self, account_index: int) -> None:
    now = _now_ms()
    current = self.get_score(account_index)
    self._scores[account_index] = {
      "score": min(self._config["max_score"], current + self._config["success_reward"]),
      "last_updated": now,
      "last_success": now,
      "consecutive_failures": 0,
    }

  def record_rate_limit(self, account_index: int) -> None:
    now = _now_ms()
    current = self.get_score(account_index)
    self._scores[account_index] = {
      "score": max(0, current + self._config["rate_limit_penalty"]),
      "last_updated": now,
      "consecutive_failures": (self._scores.get(account_index, {}).get("consecutive_failures", 0) or 0) + 1,
    }

  def record_failure(self, account_index: int) -> None:
    now = _now_ms()
    current = self.get_score(account_index)
    self._scores[account_index] = {
      "score": max(0, current + self._config["failure_penalty"]),
      "last_updated": now,
      "consecutive_failures": (self._scores.get(account_index, {}).get("consecutive_failures", 0) or 0) + 1,
    }

  def is_usable(self, account_index: int) -> bool:
    return self.get_score(account_index) >= self._config["min_usable"]


def _now_ms() -> float:
  return time.time() * 1000
