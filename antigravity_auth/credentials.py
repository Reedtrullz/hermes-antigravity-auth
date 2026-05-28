"""Safe OAuth credential resolution for Antigravity."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def _strip(value: Any) -> str:
  if not isinstance(value, str):
    return ""
  return value.strip()


def _hermes_home() -> Path:
  """Return the Hermes home directory from env or the default path."""
  return Path(os.environ.get("HERMES_HOME") or "~/.hermes").expanduser()


def _credential_file_path() -> Path:
  """Return the configured Antigravity credential file path."""
  path = os.environ.get("HERMES_ANTIGRAVITY_CREDENTIALS_FILE")
  if path:
    return Path(path).expanduser()
  return _hermes_home() / "antigravity-credentials.json"


def _load_file_credentials() -> tuple[str, str]:
  """Load OAuth credentials from the external Hermes credential file.

  Missing, malformed, or non-object JSON files are treated as absent.
  """
  try:
    data = json.loads(_credential_file_path().read_text(encoding="utf-8"))
  except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError):
    return "", ""

  if not isinstance(data, dict):
    return "", ""

  client_id = _strip(data.get("client_id")) or _strip(data.get("ANTIGRAVITY_CLIENT_ID"))
  client_secret = _strip(data.get("client_secret")) or _strip(data.get("ANTIGRAVITY_CLIENT_SECRET"))
  return client_id, client_secret


def resolve_oauth_credentials() -> tuple[str, str]:
  """Resolve OAuth credentials with per-field env override precedence."""
  env_client_id = os.environ.get("ANTIGRAVITY_CLIENT_ID", "").strip()
  env_client_secret = os.environ.get("ANTIGRAVITY_CLIENT_SECRET", "").strip()

  if env_client_id and env_client_secret:
    return env_client_id, env_client_secret

  file_client_id, file_client_secret = _load_file_credentials()
  return env_client_id or file_client_id, env_client_secret or file_client_secret
