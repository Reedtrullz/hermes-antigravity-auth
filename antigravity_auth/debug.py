"""Structured logging with file rotation and debug diagnostics."""
from __future__ import annotations

import json
import os
import re
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from antigravity_auth.storage import get_hermes_home

DEBUG = 10
INFO = 20
WARN = 30
ERROR = 40

_debug_enabled: bool = False
_debug_tui_enabled: bool = False
_log_file_path: str | None = None
_log_writer: Callable[[str], None] | None = None

_request_counter = 0
_request_counter_lock = threading.Lock()

MAX_BODY_PREVIEW_CHARS = 12000
MAX_BODY_LOG_CHARS = 50000

_LEVEL_LABELS = {
  DEBUG: "debug",
  INFO: "info",
  WARN: "warn",
  ERROR: "error",
}


class Logger:
  def __init__(self, module: str) -> None:
    self._service = f"antigravity.{module}"

  def debug(self, message: str, extra: dict | None = None) -> None:
    self._log("debug", message, extra)

  def info(self, message: str, extra: dict | None = None) -> None:
    self._log("info", message, extra)

  def warn(self, message: str, extra: dict | None = None) -> None:
    self._log("warn", message, extra)

  def error(self, message: str, extra: dict | None = None) -> None:
    self._log("error", message, extra)

  def _log(self, level: str, message: str, extra: dict | None = None) -> None:
    service = self._service
    line = f"[{service}] {level}: {message}"
    if extra:
      line += f" {json.dumps(extra)}"

    if _debug_enabled:
      _log_debug(line)

    if os.environ.get("HERMES_ANTIGRAVITY_CONSOLE_LOG") == "1":
      print(line, file=sys.stderr)


def createLogger(module: str) -> Logger:
  return Logger(module)


def is_debug_enabled() -> bool:
  return _debug_enabled


def get_log_file_path() -> str | None:
  return _log_file_path


def _get_logs_dir(log_dir: str | None = None) -> Path:
  if log_dir:
    logs_dir = Path(log_dir)
  else:
    logs_dir = get_hermes_home() / "logs" / "antigravity"
  logs_dir.mkdir(parents=True, exist_ok=True)
  try:
    os.chmod(logs_dir, 0o700)
  except Exception:
    pass
  return logs_dir


def _private_log_opener(path: str, flags: int) -> int:
  return os.open(path, flags, 0o600)


def _create_log_file_path(log_dir: str | None = None) -> str:
  logs_dir = _get_logs_dir(log_dir)
  cleanup_old_logs(str(logs_dir), 25)
  timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d-%H-%M-%S")
  return str(logs_dir / f"antigravity-debug-{timestamp}.log")


def cleanup_old_logs(logs_dir: str, max_files: int = 25) -> None:
  try:
    log_path = Path(logs_dir)
    if not log_path.is_dir():
      return

    files = [
      f for f in log_path.iterdir()
      if f.is_file() and f.name.startswith("antigravity-debug-") and f.name.endswith(".log")
    ]

    if len(files) <= max_files:
      return

    sorted_files = sorted(files, key=lambda f: f.stat().st_mtime, reverse=True)

    for f in sorted_files[max_files:]:
      try:
        f.unlink()
      except Exception:
        pass
  except Exception:
    pass


def _create_log_writer(file_path: str | None = None) -> Callable[[str], None]:
  if not file_path:
    return lambda line: None

  try:
    f = open(file_path, "a", encoding="utf-8", opener=_private_log_opener)
    try:
      os.chmod(file_path, 0o600)
    except Exception:
      pass
    f.reconfigure = lambda: None
    _lock = threading.Lock()

    def writer(line: str) -> None:
      timestamp = datetime.now(timezone.utc).isoformat()
      formatted = f"[{timestamp}] {line}"
      with _lock:
        try:
          f.write(f"{formatted}\n")
          f.flush()
        except Exception:
          pass

    return writer
  except Exception:
    return lambda line: None


def initialize_debug(
  config_debug: bool,
  config_debug_tui: bool = False,
  log_dir: str | None = None,
) -> None:
  global _debug_enabled, _debug_tui_enabled, _log_file_path, _log_writer

  if not config_debug:
    _debug_enabled = False
    _debug_tui_enabled = config_debug_tui
    _log_file_path = None
    _log_writer = None
    return

  _debug_enabled = True
  _debug_tui_enabled = config_debug_tui
  _log_file_path = _create_log_file_path(log_dir)
  _log_writer = _create_log_writer(_log_file_path)


def _log_debug(line: str) -> None:
  if _log_writer:
    _log_writer(line)


def _mask_headers(headers: dict) -> dict:
  if not headers:
    return {}

  result = {}
  for key, value in headers.items():
    if key.lower() == "authorization":
      result[key] = "[redacted]"
    else:
      result[key] = value
  return result


def _sanitize_body(body: str) -> str:
  """Redact token values from debug log bodies."""
  redactions = (
    ("access_token", "***"),
    ("refresh_token", "***"),
    ("id_token", "[REDACTED]"),
    ("accessToken", "[REDACTED]"),
    ("refreshToken", "[REDACTED]"),
    ("idToken", "[REDACTED]"),
  )
  for key, replacement in redactions:
    body = re.sub(rf'"{key}"\s*:\s*"[^"]+"', f'"{key}":"{replacement}"', body)
  body = re.sub(r'Bearer\s+[A-Za-z0-9._~+/=-]+', 'Bearer [REDACTED]', body)
  return body


def truncate_text(text: str, max_chars: int = 12000) -> str:
  if len(text) <= max_chars:
    return text
  return f"{text[:max_chars]}... (truncated {len(text) - max_chars} chars)"


def format_body_preview(body: str | None, max_chars: int = 12000) -> str:
  if body is None:
    return ""
  if isinstance(body, str):
    return truncate_text(body, max_chars)
  return truncate_text(str(body), max_chars)


def format_error_for_log(error: Exception | str | None) -> str:
  if error is None:
    return ""
  if isinstance(error, Exception):
    return str(error)
  return error


def format_account_label(email: str | None, account_index: int) -> str:
  if email:
    return email
  return f"Account {account_index + 1}"


def format_account_context_label(email: str | None, account_index: int) -> str:
  if email:
    return email
  if account_index >= 0:
    return f"Account {account_index + 1}"
  return "All accounts"


def start_antigravity_debug_request(meta: dict) -> str | None:
  if not _debug_enabled:
    return None

  global _request_counter
  with _request_counter_lock:
    _request_counter += 1
    request_id = f"ANTIGRAVITY-{_request_counter}"

  method = meta.get("method", "GET")
  resolved_url = meta.get("resolvedUrl", "")
  _log_debug(f"[Antigravity Debug {request_id}] pid={os.getpid()} {method} {resolved_url}")

  original_url = meta.get("originalUrl")
  if original_url and original_url != resolved_url:
    _log_debug(f"[Antigravity Debug {request_id}] Original URL: {original_url}")

  project_id = meta.get("projectId")
  if project_id:
    _log_debug(f"[Antigravity Debug {request_id}] Project: {project_id}")

  streaming = meta.get("streaming", False)
  _log_debug(f"[Antigravity Debug {request_id}] Streaming: {'yes' if streaming else 'no'}")

  headers = meta.get("headers")
  _log_debug(f"[Antigravity Debug {request_id}] Headers: {json.dumps(_mask_headers(headers or {}))}")

  body = meta.get("body")
  body_preview = format_body_preview(_sanitize_body(body) if body else None)
  if body_preview:
    _log_debug(f"[Antigravity Debug {request_id}] Body Preview: {body_preview}")

  return request_id


def log_antigravity_debug_response(
  context_id: str | None,
  status: int,
  duration_ms: float,
  meta: dict | None = None,
) -> None:
  if not _debug_enabled or not context_id:
    return

  meta = meta or {}

  _log_debug(f"[Antigravity Debug {context_id}] Response {status} ({duration_ms}ms)")

  headers_override = meta.get("headersOverride")
  _log_debug(
    f"[Antigravity Debug {context_id}] Response Headers: "
    f"{json.dumps(_mask_headers(headers_override or {}))}"
  )

  note = meta.get("note")
  if note:
    _log_debug(f"[Antigravity Debug {context_id}] Note: {note}")

  error = meta.get("error")
  if error is not None:
    _log_debug(f"[Antigravity Debug {context_id}] Error: {format_error_for_log(error)}")

  body = meta.get("body")
  if body:
    _log_debug(
      f"[Antigravity Debug {context_id}] Response Body Preview: "
      f"{truncate_text(_sanitize_body(body) if isinstance(body, str) else str(body), MAX_BODY_PREVIEW_CHARS)}"
    )


def log_account_context(
  label: str,
  account_index: int,
  email: str | None,
  family: str,
  total_accounts: int,
  rate_limit_state: dict | None = None,
) -> None:
  if not _debug_enabled:
    return

  account_label = format_account_context_label(email, account_index)

  if account_index >= 0:
    index_label = f"{account_index + 1}/{total_accounts}"
  else:
    index_label = f"-/{total_accounts}"

  rate_limit_info = ""
  if rate_limit_state:
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    active_rate_limits: dict[str, str] = {}
    for key, reset_time in rate_limit_state.items():
      if isinstance(reset_time, (int, float)) and reset_time > now_ms:
        remaining_sec = int((reset_time - now_ms) / 1000)
        active_rate_limits[key] = f"{remaining_sec}s"
    if active_rate_limits:
      rate_limit_info = f" rateLimits={json.dumps(active_rate_limits)}"

  _log_debug(
    f"[Account] {label}: {account_label} ({index_label}) "
    f"family={family}{rate_limit_info}"
  )


def log_rate_limit_event(
  account_index: int,
  email: str | None,
  family: str,
  status: int,
  retry_after_ms: float,
  body_info: dict | None = None,
) -> None:
  if not _debug_enabled:
    return

  account_label = format_account_label(email, account_index)
  _log_debug(
    f"[RateLimit] {status} on {account_label} "
    f"family={family} retryAfterMs={retry_after_ms}"
  )

  if body_info:
    if body_info.get("message"):
      _log_debug(f"[RateLimit] message: {body_info['message']}")
    if body_info.get("quotaResetTime"):
      _log_debug(f"[RateLimit] quotaResetTime: {body_info['quotaResetTime']}")
    if body_info.get("retryDelayMs") is not None:
      _log_debug(f"[RateLimit] body retryDelayMs: {body_info['retryDelayMs']}")
    if body_info.get("reason"):
      _log_debug(f"[RateLimit] reason: {body_info['reason']}")


def log_quota_status(
  account_email: str | None,
  account_index: int,
  quota_percent: float,
  family: str | None = None,
) -> None:
  if not _debug_enabled:
    return

  account_label = format_account_label(account_email, account_index)
  family_info = f" family={family}" if family else ""
  if quota_percent <= 0:
    status = "EXHAUSTED"
  elif quota_percent < 20:
    status = "LOW"
  else:
    status = "OK"
  _log_debug(
    f"[Quota] {account_label} remaining={quota_percent:.1f}% "
    f"status={status}{family_info}"
  )


def log_model_used(
  requested_model: str,
  actual_model: str,
  account_email: str | None = None,
) -> None:
  if not _debug_enabled:
    return

  account_info = f" account={account_email}" if account_email else ""
  if requested_model != actual_model:
    _log_debug(f"[Model] requested={requested_model} actual={actual_model}{account_info}")
  else:
    _log_debug(f"[Model] {actual_model}{account_info}")


def log_retry_attempt(
  attempt: int,
  max_attempts: int,
  reason: str,
  delay_ms: float | None = None,
) -> None:
  if not _debug_enabled:
    return

  delay_info = f" delay={delay_ms}ms" if delay_ms is not None else ""
  max_info = "∞" if max_attempts < 0 else str(max_attempts)
  _log_debug(f"[Retry] Attempt {attempt}/{max_info} reason={reason}{delay_info}")
