"""Per-account device fingerprint generation for Antigravity header spoofing."""
from __future__ import annotations

import os
import random
import secrets
import uuid
from typing import Any

from ._time_utils import now_ms
from .constants import ANTIGRAVITY_VERSION_FALLBACK


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

  # Electron-style UA — Antigravity backend expects full Chrome/Electron UA,
  # not the short "antigravity/1.18.3 platform/arch" form.
  chrome_major = random.randint(130, 140)
  electron_major = random.randint(32, 38)
  if platform == "darwin":
    os_ua = f"Macintosh; Intel Mac OS X {os_version.replace('.', '_')}"
  else:
    os_ua = f"Windows NT {os_version.split('.')[0]}.{os_version.split('.')[1]}; Win64; x64"
  user_agent = (
    f"Mozilla/5.0 ({os_ua}) AppleWebKit/537.36 "
    f"(KHTML, like Gecko) Antigravity/{ANTIGRAVITY_VERSION_FALLBACK} "
    f"Chrome/{chrome_major}.0.7204.235 "
    f"Electron/{electron_major}.3.1 Safari/537.36"
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
    "createdAt": now_ms(),
  }


def update_fingerprint_version(fingerprint: dict[str, Any]) -> bool:
  """Ensure the fingerprint has the current version fields.

  Returns True if the fingerprint was modified.

  Repairs fingerprints from older plugin versions. Current Antigravity IDE
  fingerprints use ``ideType: ANTIGRAVITY`` paired with ``pluginType: GEMINI``.
  """
  changed = False
  if "createdAt" not in fingerprint:
    fingerprint["createdAt"] = now_ms()
    changed = True
  if "apiClient" not in fingerprint:
    fingerprint["apiClient"] = _random_from(SDK_CLIENTS)
    changed = True

  # Ensure clientMetadata exists with correct pluginType.
  # The Antigravity backend expects pluginType: GEMINI paired with
  # ideType: ANTIGRAVITY (matching the Antigravity IDE's fingerprint).
  cm = fingerprint.get("clientMetadata")
  if isinstance(cm, dict):
    if cm.get("pluginType") != "GEMINI":
      cm["pluginType"] = "GEMINI"
      changed = True
  else:
    # Missing clientMetadata entirely — generate it
    platform = _platform_to_display_name(
      "win32" if "Windows" in str(fingerprint.get("userAgent", ""))
      else "darwin"
    )
    fingerprint["clientMetadata"] = {
      "ideType": "ANTIGRAVITY",
      "platform": platform,
      "pluginType": "GEMINI",
    }
    changed = True

  # Repair outdated Antigravity version in the userAgent string.
  # The backend rejects versions older than 2.0.0 with a deprecation warning.
  ua = fingerprint.get("userAgent", "")
  if isinstance(ua, str) and "Antigravity/" in ua:
    import re
    old_ver = re.search(r"Antigravity/([\d.]+)", ua)
    if old_ver and old_ver.group(1) != ANTIGRAVITY_VERSION_FALLBACK:
      new_ua = ua[: old_ver.start()] + f"Antigravity/{ANTIGRAVITY_VERSION_FALLBACK}" + ua[old_ver.end() :]
      fingerprint["userAgent"] = new_ua
      changed = True

  return changed


def build_fingerprint_headers(fingerprint: dict[str, Any] | None) -> dict[str, str]:
  """Build stable HTTP headers from a fingerprint.

  Returns an empty dict if no fingerprint is provided.
  """
  if fingerprint is None:
    return {}

  headers = {}
  ua = fingerprint.get("userAgent", "")
  if ua:
    headers["User-Agent"] = ua
  api_client = fingerprint.get("apiClient", "")
  if api_client:
    headers["X-Goog-Api-Client"] = api_client
  return headers
