"""Session error detection and recovery toast notifications."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Literal

RecoveryErrorType = Literal["tool_result_missing", "thinking_block_order", "thinking_disabled_violation"] | None


@dataclass
class ResumeConfig:
  session_id: str
  agent: str | None = None
  model: str | None = None


RECOVERY_RESUME_TEXT = "[session recovered - continuing previous task]"


def get_error_message(error: Any) -> str:
  if not error:
    return ""
  if isinstance(error, str):
    return error.lower()

  if isinstance(error, dict):
    data = error.get("data")
    candidates = [data, error.get("error"), error]
    if isinstance(data, dict):
      candidates.append(data.get("error"))

    for candidate in candidates:
      if isinstance(candidate, dict):
        msg = candidate.get("message")
        if isinstance(msg, str) and msg:
          return msg.lower()

  try:
    return json.dumps(error).lower()
  except Exception:
    return ""


def extract_message_index(error: Any) -> int | None:
  message = get_error_message(error)
  match = re.search(r"messages\.(\d+)", message)
  if match and match.group(1):
    return int(match.group(1))
  return None


def detect_error_type(error: Any) -> RecoveryErrorType:
  message = get_error_message(error)

  has_expected_found_thinking_order = (
    ("expected thinking" in message or "expected a thinking" in message)
    and "found" in message
  )

  if "tool_use" in message and "tool_result" in message:
    return "tool_result_missing"

  if (
    "thinking" in message
    and (
      "first block" in message
      or "must start with" in message
      or "preceeding" in message
      or "preceding" in message
      or has_expected_found_thinking_order
    )
  ):
    return "thinking_block_order"

  if "thinking is disabled" in message and "cannot contain" in message:
    return "thinking_disabled_violation"

  return None


def is_recoverable_error(error: Any) -> bool:
  return detect_error_type(error) is not None


TOAST_TITLES: dict[str, str] = {
  "tool_result_missing": "Tool Crash Recovery",
  "thinking_block_order": "Thinking Block Recovery",
  "thinking_disabled_violation": "Thinking Strip Recovery",
}

TOAST_MESSAGES: dict[str, str] = {
  "tool_result_missing": "Injecting cancelled tool results...",
  "thinking_block_order": "Fixing message structure...",
  "thinking_disabled_violation": "Stripping thinking blocks...",
}


def get_recovery_toast_content(error_type: RecoveryErrorType) -> dict[str, str]:
  if not error_type:
    return {"title": "Session Recovery", "message": "Attempting to recover session..."}
  return {
    "title": TOAST_TITLES.get(error_type, "Session Recovery"),
    "message": TOAST_MESSAGES.get(error_type, "Attempting to recover session..."),
  }


def get_recovery_success_toast() -> dict[str, str]:
  return {"title": "Session Recovered", "message": "Continuing where you left off..."}


def get_recovery_failure_toast() -> dict[str, str]:
  return {"title": "Recovery Failed", "message": "Please retry or start a new session."}
