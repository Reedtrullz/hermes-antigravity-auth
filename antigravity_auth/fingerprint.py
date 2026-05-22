"""Per-account device fingerprint generation for Antigravity header spoofing."""
from __future__ import annotations

import os
import random
import secrets
import uuid
from typing import Any


PLATFORM_CHOICES = ["darwin", "win32"]
ARCHITECTURES = ["x64", "arm64"]
IDE_TYPES = ["ANTIGRAVITY"]
PLATFORM_NAMES = ["WINDOWS", "MACOS"]

SDK_CLIENTS = [
  "google-cloud-sdk vscode_cloudshelleditor/0.1",
  "google-cloud-sdk vscode/1.86.0",
  "google-cloud-sdk vscode/1.87.0",
  "google-cloud-sdk vscode/1.96.0",
]

OS_VERSIONS: dict[str, list[str]] = {
  "darwin": ["10.15.7", "11.6.8", "12.6.3", "13.5.2", "14.2.1", "14.5"],
  "win32": ["10.0.19041", "10.0.19042", "10.0.19043", "10.0.22000", "10.0.22621", "10.0.22631"],
  "linux": ["5.15.0", "5.19.0", "6.1.0", "6.2.0", "6.5.0", "6.6.0"],
}

MAX_FINGERPRINT_HISTORY = 5


def _random_from(items: list[str]) -> str:
  return items[random.randint(0, len(items) - 1)]


def _platform_to_display_name(platform: str) -> str:
  return "WINDOWS" if platform == "win32" else "MACOS"


def generate_device_id() -> str:
  return str(uuid.uuid4())


def generate_session_token() -> str:
  return secrets.token_hex(16)


def generate_fingerprint() -> dict[str, Any]:
  platform = _random_from(PLATFORM_CHOICES)
  arch = _random_from(ARCHITECTURES)
  os_version = _random_from(OS_VERSIONS.get(platform, OS_VERSIONS["darwin"]))
  ide_type = "ANTIGRAVITY"
  sdk_client = _random_from(SDK_CLIENTS)

  user_agent = (
    f"Mozilla/5.0 (Macintosh; Intel Mac OS X {os_version}) "
    f"AppleWebKit/537.36 (KHTML, like Gecko) "
    f"Antigravity/1.18.3 Chrome/138.0.7204.235 Electron/37.3.1 Safari/537.36"
  ) if platform == "darwin" else (
    f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    f"AppleWebKit/537.36 (KHTML, like Gecko) "
    f"Antigravity/1.18.3 Chrome/138.0.7204.235 Electron/37.3.1 Safari/537.36"
  )

  return {
    "deviceId": generate_device_id(),
    "sessionToken": generate_session_token(),
    "userAgent": user_agent,
    "apiClient": sdk_client,
    "clientMetadata": {
      "ideType": ide_type,
      "platform": _platform_to_display_name(platform),
      "pluginType": "GEMINI",
    },
    "createdAt": _now_ms(),
  }


def update_fingerprint_version(fingerprint: dict[str, Any]) -> bool:
  """Ensure the fingerprint has the current version fields.

  Returns True if the fingerprint was modified.
  """
  changed = False
  if "createdAt" not in fingerprint:
    fingerprint["createdAt"] = _now_ms()
    changed = True
  if "apiClient" not in fingerprint:
    fingerprint["apiClient"] = _random_from(SDK_CLIENTS)
    changed = True
  return changed


def build_fingerprint_headers(fingerprint: dict[str, Any] | None) -> dict[str, str]:
  """Build User-Agent header from a fingerprint.

  Returns an empty dict if no fingerprint is provided.
  """
  if fingerprint is None:
    return {}
  ua = fingerprint.get("userAgent", "")
  if ua:
    return {"User-Agent": ua}
  return {}


def _now_ms() -> float:
  import time
  return time.time() * 1000
