"""Safe OAuth credential resolution for Antigravity."""

from __future__ import annotations

import json
import os
import secrets
import stat
from pathlib import Path
from typing import Any


class MissingOAuthCredentialsError(RuntimeError):
  """Raised when OAuth client credentials are not configured."""


def _strip(value: Any) -> str:
  if not isinstance(value, str):
    return ""
  return value.strip()


def _hermes_home() -> Path:
  """Return the Hermes home directory from env or the default path."""
  return Path(os.environ.get("HERMES_HOME") or "~/.hermes").expanduser()


def _credential_file_path() -> Path:
  """Return the canonical Hermes Antigravity credential file path."""
  return _hermes_home() / "antigravity-credentials.json"


def _secret_file_opener(path: str, flags: int) -> int:
  return os.open(path, flags | os.O_CREAT | os.O_EXCL, 0o600)


def credential_file_path() -> Path:
  """Return the configured external Antigravity credential file path."""
  return _credential_file_path()


def _load_file_credentials() -> tuple[str, str]:
  """Load OAuth credentials from the external Hermes credential file.

  Missing, malformed, or non-object JSON files are treated as absent.
  """
  path = _credential_file_path()
  try:
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o077:
      os.chmod(path, 0o600)
    data = json.loads(path.read_text(encoding="utf-8"))
  except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError):
    return "", ""

  if not isinstance(data, dict):
    return "", ""

  client_id = _strip(data.get("client_id")) or _strip(data.get("ANTIGRAVITY_CLIENT_ID"))
  client_secret = _strip(data.get("client_secret")) or _strip(data.get("ANTIGRAVITY_CLIENT_SECRET"))
  return client_id, client_secret


def resolve_oauth_credentials() -> tuple[str, str]:
  """Resolve OAuth credentials with precedence: env > Hermes credential JSON."""
  env_client_id = os.environ.get("ANTIGRAVITY_CLIENT_ID", "").strip()
  env_client_secret = os.environ.get("ANTIGRAVITY_CLIENT_SECRET", "").strip()

  file_client_id, file_client_secret = _load_file_credentials()
  client_id = env_client_id or file_client_id
  client_secret = env_client_secret or file_client_secret
  if client_id and client_secret:
    return client_id, client_secret

  return "", ""


def write_oauth_credentials(client_id: str, client_secret: str, path: Path | None = None) -> Path:
  """Write OAuth credentials to the external Hermes-owned credential file."""
  clean_client_id = client_id.strip()
  clean_client_secret = client_secret.strip()
  if not clean_client_id or not clean_client_secret:
    raise MissingOAuthCredentialsError("Both client_id and client_secret are required.")

  target = path or _credential_file_path()
  target.parent.mkdir(parents=True, exist_ok=True)
  os.chmod(target.parent, 0o700)
  tmp_path = target.with_name(
    f"{target.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp"
  )
  try:
    with open(tmp_path, "w", encoding="utf-8", opener=_secret_file_opener) as f:
      f.write(
        json.dumps({
          "client_id": clean_client_id,
          "client_secret": clean_client_secret,
        }, indent=2, sort_keys=True) + "\n"
      )
    os.replace(tmp_path, target)
    os.chmod(target, 0o600)
  except Exception:
    try:
      tmp_path.unlink()
    except FileNotFoundError:
      pass
    except Exception:
      pass
    raise
  return target
