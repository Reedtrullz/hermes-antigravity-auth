"""Configuration dataclass with YAML loader and TTL cache."""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field, fields, replace
from pathlib import Path
from typing import Any

from antigravity_auth.storage import get_hermes_home


logger = logging.getLogger(__name__)


@dataclass
class SignatureCacheConfig:
  enabled: bool = True
  memory_ttl_seconds: int = 3600
  disk_ttl_seconds: int = 172800
  write_interval_seconds: int = 60


@dataclass
class HealthScoreConfig:
  initial: int = 70
  success_reward: int = 1
  rate_limit_penalty: int = -10
  failure_penalty: int = -20
  recovery_rate_per_hour: float = 2.0
  min_usable: int = 50
  max_score: int = 100


@dataclass
class TokenBucketConfig:
  max_tokens: int = 50
  regeneration_rate_per_minute: float = 6.0
  initial_tokens: int = 50


@dataclass
class Config:
  quiet_mode: bool = False
  toast_scope: str = "root_only"
  debug: bool = False
  debug_tui: bool = False
  log_dir: str | None = None
  keep_thinking: bool = False
  session_recovery: bool = True
  auto_resume: bool = True
  resume_text: str = "continue"
  signature_cache: SignatureCacheConfig = field(default_factory=SignatureCacheConfig)
  empty_response_max_attempts: int = 4
  empty_response_retry_delay_ms: int = 2000
  tool_id_recovery: bool = True
  claude_tool_hardening: bool = True
  claude_prompt_auto_caching: bool = False
  proactive_token_refresh: bool = True
  proactive_refresh_buffer_seconds: int = 1800
  proactive_refresh_check_interval_seconds: int = 300
  max_rate_limit_wait_seconds: int = 300
  quota_fallback: bool = False
  # DEPRECATED — Gemini CLI sunsets 2026-06-18.  Use the default
  # antigravity header style (Electron UA + fingerprint) instead.
  cli_first: bool = False
  account_selection_strategy: str = "hybrid"
  pid_offset_enabled: bool = False
  switch_on_first_rate_limit: bool = True
  scheduling_mode: str = "cache_first"
  max_cache_first_wait_seconds: int = 60
  failure_ttl_seconds: int = 3600
  default_retry_after_seconds: int = 60
  max_backoff_seconds: int = 60
  request_jitter_max_ms: int = 0
  soft_quota_threshold_percent: int = 90
  quota_refresh_interval_minutes: int = 15
  soft_quota_cache_ttl_minutes: str | int = "auto"
  health_score: HealthScoreConfig = field(default_factory=HealthScoreConfig)
  token_bucket: TokenBucketConfig = field(default_factory=TokenBucketConfig)

  @property
  def quiet(self) -> bool:
    return self.quiet_mode


DEFAULT_CONFIG: Config = Config()

CONFIG_FIELD_NAMES = (
  "quiet_mode", "toast_scope", "debug", "debug_tui", "log_dir",
  "keep_thinking",
  "session_recovery", "auto_resume", "resume_text",
  "empty_response_max_attempts", "empty_response_retry_delay_ms",
  "tool_id_recovery",
  "claude_tool_hardening", "claude_prompt_auto_caching",
  "proactive_token_refresh", "proactive_refresh_buffer_seconds",
  "proactive_refresh_check_interval_seconds",
  "max_rate_limit_wait_seconds", "quota_fallback", "cli_first",
  "account_selection_strategy", "pid_offset_enabled",
  "switch_on_first_rate_limit", "scheduling_mode",
  "max_cache_first_wait_seconds", "failure_ttl_seconds",
  "default_retry_after_seconds", "max_backoff_seconds",
  "request_jitter_max_ms", "soft_quota_threshold_percent",
  "quota_refresh_interval_minutes", "soft_quota_cache_ttl_minutes",
)

NESTED_CONFIG_FIELD_NAMES = ("signature_cache", "health_score", "token_bucket")


def _parse_bool(value: str) -> bool:
  return value.lower() in ("1", "true", "yes", "on")


def load_config_from_dict(data: dict[str, Any]) -> Config:
  kwargs: dict[str, Any] = {}

  for field_name in CONFIG_FIELD_NAMES:
    if field_name in data:
      kwargs[field_name] = data[field_name]

  if "signature_cache" in data and isinstance(data["signature_cache"], dict):
    kwargs["signature_cache"] = SignatureCacheConfig(
      **_known_dataclass_kwargs(SignatureCacheConfig, data["signature_cache"])
    )

  if "health_score" in data and isinstance(data["health_score"], dict):
    kwargs["health_score"] = HealthScoreConfig(
      **_known_dataclass_kwargs(HealthScoreConfig, data["health_score"])
    )

  if "token_bucket" in data and isinstance(data["token_bucket"], dict):
    kwargs["token_bucket"] = TokenBucketConfig(
      **_known_dataclass_kwargs(TokenBucketConfig, data["token_bucket"])
    )

  return Config(**kwargs)


def _known_dataclass_kwargs(cls: type[Any], values: dict[str, Any]) -> dict[str, Any]:
  """Filter a dict to keyword arguments accepted by a config dataclass."""
  allowed = {field_info.name for field_info in fields(cls)}
  return {key: value for key, value in values.items() if key in allowed}


def _extract_config_data(data: dict[str, Any]) -> dict[str, Any]:
  """Return Antigravity plugin config from Hermes config.yaml.

  Hermes stores plugin config under plugins.entries.<plugin-name>. Older
  development snapshots used root-level keys, so root keys are still accepted
  and nested plugin config wins on conflict.
  """
  extracted: dict[str, Any] = {}

  for field_name in (*CONFIG_FIELD_NAMES, *NESTED_CONFIG_FIELD_NAMES):
    if field_name in data:
      extracted[field_name] = data[field_name]

  plugins = data.get("plugins")
  if not isinstance(plugins, dict):
    return extracted

  entries = plugins.get("entries")
  if not isinstance(entries, dict):
    return extracted

  antigravity = entries.get("antigravity")
  if isinstance(antigravity, dict):
    extracted.update(antigravity)

  return extracted


def load_config_from_yaml(yaml_path: Path) -> Config | None:
  if not yaml_path.exists():
    return None

  try:
    import yaml
  except ImportError:
    logger.warning(
      "Ignoring %s because PyYAML is not installed. Install with "
      "hermes-antigravity-auth[yaml] or pip install pyyaml to enable YAML "
      "configuration.",
      yaml_path,
    )
    return None

  try:
    with open(yaml_path, "r", encoding="utf-8") as f:
      data = yaml.safe_load(f)
    if not isinstance(data, dict):
      return None
    return load_config_from_dict(_extract_config_data(data))
  except Exception:
    return None


_config_cache: Config | None = None
_config_cache_time: float = 0.0
_CONFIG_CACHE_TTL_SECONDS: float = 30.0  # re-read config.yaml every 30s


def get_config(force_reload: bool = False) -> Config:
    global _config_cache, _config_cache_time

    if force_reload:
        _config_cache = None

    import time as _time
    now = _time.time()
    if _config_cache is not None and (now - _config_cache_time) < _CONFIG_CACHE_TTL_SECONDS:
        return _config_cache

    config = DEFAULT_CONFIG

    yaml_path = get_hermes_home() / "config.yaml"
    yaml_config = load_config_from_yaml(yaml_path)
    if yaml_config is not None:
        config = yaml_config

    config = apply_env_overrides(config)
    _config_cache = config
    _config_cache_time = _time.time()
    return config


def invalidate_config_cache() -> None:
    """Invalidate the configuration cache, forcing a reload on next get_config call."""
    global _config_cache, _config_cache_time
    _config_cache = None
    _config_cache_time = 0.0


def apply_env_overrides(config: Config) -> Config:
  kwargs: dict[str, Any] = {}

  if "HERMES_ANTIGRAVITY_DEBUG" in os.environ:
    kwargs["debug"] = _parse_bool(os.environ["HERMES_ANTIGRAVITY_DEBUG"])

  if "HERMES_ANTIGRAVITY_QUIET" in os.environ:
    kwargs["quiet_mode"] = _parse_bool(os.environ["HERMES_ANTIGRAVITY_QUIET"])

  if "HERMES_ANTIGRAVITY_KEEP_THINKING" in os.environ:
    kwargs["keep_thinking"] = _parse_bool(os.environ["HERMES_ANTIGRAVITY_KEEP_THINKING"])

  if "HERMES_ANTIGRAVITY_CLI_FIRST" in os.environ:
    kwargs["cli_first"] = _parse_bool(os.environ["HERMES_ANTIGRAVITY_CLI_FIRST"])

  if "HERMES_ANTIGRAVITY_ACCOUNT_SELECTION_STRATEGY" in os.environ:
    kwargs["account_selection_strategy"] = os.environ["HERMES_ANTIGRAVITY_ACCOUNT_SELECTION_STRATEGY"]

  if "HERMES_ANTIGRAVITY_SCHEDULING_MODE" in os.environ:
    kwargs["scheduling_mode"] = os.environ["HERMES_ANTIGRAVITY_SCHEDULING_MODE"]

  if "HERMES_ANTIGRAVITY_DEBUG_TUI" in os.environ:
    kwargs["debug_tui"] = _parse_bool(os.environ["HERMES_ANTIGRAVITY_DEBUG_TUI"])

  if "HERMES_ANTIGRAVITY_LOG_DIR" in os.environ:
    kwargs["log_dir"] = os.environ["HERMES_ANTIGRAVITY_LOG_DIR"]

  return replace(config, **kwargs)
