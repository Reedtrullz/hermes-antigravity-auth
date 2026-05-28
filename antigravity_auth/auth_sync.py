"""Helpers for syncing Antigravity credentials into Hermes runtime stores."""

from __future__ import annotations

import os
import sys
import time

_module_dir = os.path.dirname(os.path.abspath(__file__))
_removed_sys_path = None
if sys.path and os.path.abspath(sys.path[0]) == _module_dir:
  # Direct module fallback imports can put this package first on sys.path;
  # temporarily remove it so stdlib dataclasses does not import our token.py.
  _removed_sys_path = sys.path.pop(0)
try:
  from dataclasses import dataclass
finally:
  if _removed_sys_path is not None:
    sys.path.insert(0, _removed_sys_path)

try:
  from .storage import sync_token_to_auth_json
  from .token import parse_refresh_parts
except ImportError:
  from storage import sync_token_to_auth_json

  import importlib.util
  from pathlib import Path

  _token_path = Path(__file__).with_name("token.py")
  _token_spec = importlib.util.spec_from_file_location(
      "_antigravity_auth_token_fallback",
      _token_path,
  )
  if _token_spec is None or _token_spec.loader is None:
    raise ImportError(f"Unable to load token helpers from {_token_path}")
  _token_module = importlib.util.module_from_spec(_token_spec)
  _token_spec.loader.exec_module(_token_module)
  parse_refresh_parts = _token_module.parse_refresh_parts


@dataclass
class AuthSyncResult:
  auth_json: bool
  google_oauth: bool

  @property
  def ok(self) -> bool:
    return self.auth_json and self.google_oauth

  def __bool__(self) -> bool:
    return self.ok


def sync_token_to_google_oauth(
    access_token: str,
    refresh_token: str,
    project_id: str = "",
    email: str | None = None,
    expires_ms: int | None = None,
) -> bool:
  """Write credentials to Hermes' native auth/google_oauth.json store."""
  try:
    from agent.google_oauth import GoogleCredentials, save_credentials
  except Exception:
    return False

  parts = parse_refresh_parts(refresh_token)
  resolved_project_id = project_id or parts.get("projectId") or ""
  resolved_expires_ms = expires_ms or int(time.time() * 1000) + 3600 * 1000

  credentials = GoogleCredentials(
    access_token=access_token,
    refresh_token=parts.get("refreshToken", ""),
    expires_ms=resolved_expires_ms,
    email=email or "",
    project_id=resolved_project_id,
    managed_project_id=parts.get("managedProjectId") or "",
  )
  save_credentials(credentials)
  return True


def sync_token_to_all_auth_stores(
    access_token: str,
    refresh_token: str,
    project_id: str = "",
    email: str | None = None,
    expires_ms: int | None = None,
    set_active: bool = True,
) -> AuthSyncResult:
  """Sync active credentials to auth.json and google_oauth.json together."""
  auth_json = True
  google_oauth = True
  try:
    sync_token_to_auth_json(
      access_token=access_token,
      refresh_token=refresh_token,
      project_id=project_id,
      email=email,
      set_active=set_active,
    )
  except Exception:
    auth_json = False

  try:
    google_oauth = bool(sync_token_to_google_oauth(
      access_token=access_token,
      refresh_token=refresh_token,
      project_id=project_id,
      email=email,
      expires_ms=expires_ms,
    ))
  except Exception:
    google_oauth = False

  return AuthSyncResult(auth_json=auth_json, google_oauth=google_oauth)


def sync_token_to_all_auth_stores_bool(*args, **kwargs) -> bool:
  return sync_token_to_all_auth_stores(*args, **kwargs).ok
