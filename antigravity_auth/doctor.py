"""Diagnostic checks for the Hermes Antigravity plugin."""
from __future__ import annotations

import importlib
import importlib.metadata
import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .redaction import redact_secret_text, redact_secrets
from .storage import (
  _probe_process_file_lock,
  get_accounts_json_path,
  get_auth_json_path,
  get_hermes_home,
  load_accounts,
  resolve_active_account_index,
)
from .token import format_refresh_parts, refresh_access_token


@dataclass
class DoctorRow:
  status: str
  check: str
  detail: str
  fix: str = ""


def _row(status: str, check: str, detail: str, fix: str = "") -> DoctorRow:
  return DoctorRow(status=status, check=check, detail=redact_secret_text(detail), fix=redact_secret_text(fix))


def _path_mode(path: Path) -> int | None:
  try:
    return stat.S_IMODE(path.stat().st_mode)
  except Exception:
    return None


def _check_entrypoint() -> DoctorRow:
  try:
    eps = importlib.metadata.entry_points()
    if hasattr(eps, "select"):
      selected = list(eps.select(group="hermes_agent.plugins"))
    else:
      selected = list(eps.get("hermes_agent.plugins", []))  # type: ignore[attr-defined]
    for ep in selected:
      if ep.name == "antigravity-cli" and ep.value == "antigravity_auth.hermes_plugin":
        return _row("PASS", "plugin entrypoint", "antigravity-cli entrypoint is installed")
    return _row(
      "WARN",
      "plugin entrypoint",
      "antigravity-cli entrypoint was not found in installed package metadata",
      "Run pip install -e . or reinstall hermes-antigravity-auth in the Python environment used by Hermes.",
    )
  except Exception as exc:
    return _row("WARN", "plugin entrypoint", f"could not inspect entry points: {exc}", "Verify package installation with pip show hermes-antigravity-auth.")


def _check_hermes_adapter() -> list[DoctorRow]:
  rows: list[DoctorRow] = []
  try:
    module = importlib.import_module("agent.gemini_cloudcode_adapter")
    rows.append(_row("PASS", "Hermes adapter import", "agent.gemini_cloudcode_adapter imports"))
    missing = [name for name in ("GeminiCloudCodeClient", "wrap_code_assist_request") if not hasattr(module, name)]
    if missing:
      rows.append(_row(
        "FAIL",
        "Hermes adapter symbols",
        "missing " + ", ".join(missing),
        "Upgrade Hermes Agent or verify the google-gemini-cli Cloud Code adapter is installed.",
      ))
    else:
      rows.append(_row("PASS", "Hermes adapter symbols", "GeminiCloudCodeClient and wrap_code_assist_request exist"))
  except Exception as exc:
    rows.append(_row(
      "FAIL",
      "Hermes adapter import",
      f"could not import agent.gemini_cloudcode_adapter: {exc}",
      "Run inside the Hermes Agent environment or install a Hermes version with google-gemini-cli support.",
    ))
  return rows


def _check_interceptor() -> DoctorRow:
  try:
    from . import interceptor
    if interceptor.is_installed():
      return _row("PASS", "interceptor", "interceptor is installed in this process")
    try:
      adapter = importlib.import_module("agent.gemini_cloudcode_adapter")
      if hasattr(adapter, "GeminiCloudCodeClient") and hasattr(adapter, "wrap_code_assist_request"):
        return _row("WARN", "interceptor", "interceptor is not installed yet, but Hermes symbols are importable", "Ensure the antigravity-cli plugin is enabled in ~/.hermes/config.yaml and restart Hermes.")
    except Exception:
      pass
    return _row("FAIL", "interceptor", "interceptor is not installed and Hermes adapter symbols are unavailable", "Enable the plugin from within Hermes or install a compatible Hermes build.")
  except Exception as exc:
    return _row("FAIL", "interceptor", f"could not inspect interceptor: {exc}", "Reinstall hermes-antigravity-auth and rerun doctor.")


def _check_retry_behavior() -> DoctorRow:
  try:
    from . import interceptor
    if hasattr(interceptor, "_send_with_antigravity_retry") and hasattr(interceptor, "_clone_request_for_retry"):
      return _row(
        "PASS",
        "automatic retry",
        "enabled for replayable non-streaming 401/403/429 responses; streaming responses cannot be replayed automatically",
        "If a streaming request fails after token refresh or account rotation, retry the user request manually.",
      )
    return _row(
      "FAIL",
      "automatic retry",
      "retry wrapper symbols are missing",
      "Reinstall hermes-antigravity-auth or upgrade to a build with bounded retry support.",
    )
  except Exception as exc:
    return _row("FAIL", "automatic retry", f"could not inspect retry wrapper: {exc}", "Reinstall hermes-antigravity-auth and rerun doctor.")


def _check_account_store_locking() -> DoctorRow:
  backend, detail = _probe_process_file_lock()
  if backend in ("fcntl", "msvcrt"):
    return _row("PASS", "account store locking", detail)
  return _row(
    "WARN",
    "account store locking",
    detail or "no inter-process file locking backend is available; transactional updates are only thread-safe inside this process",
    "Run one Hermes Antigravity process at a time or use a Python/platform with fcntl.flock or msvcrt.locking support.",
  )


def _check_account_store() -> DoctorRow:
  path = get_accounts_json_path()
  if not path.exists():
    return _row("WARN", "account store", f"{path} does not exist", "Run hermes antigravity login.")
  mode = _path_mode(path)
  if mode is not None and mode & 0o077:
    return _row("WARN", "account store", f"{path} permissions are {oct(mode)}", f"Run chmod 600 {path}.")
  try:
    data = load_accounts()
    accounts = data.get("accounts", [])
    if not isinstance(accounts, list):
      return _row("FAIL", "account store", "accounts field is not a list", "Back up and recreate antigravity-accounts.json with hermes antigravity login.")
    return _row("PASS", "account store", f"{len(accounts)} account(s), permissions {oct(mode) if mode is not None else 'unknown'}")
  except Exception as exc:
    return _row("FAIL", "account store", f"could not parse account store: {exc}", "Back up and recreate antigravity-accounts.json with hermes antigravity login.")


def _check_auth_files() -> list[DoctorRow]:
  rows: list[DoctorRow] = []
  auth_json = get_auth_json_path()
  google_oauth = get_hermes_home() / "auth" / "google_oauth.json"
  for label, path in (("auth.json", auth_json), ("auth/google_oauth.json", google_oauth)):
    if not path.exists():
      rows.append(_row("WARN", label, f"{path} is missing", "Run hermes antigravity login or switch to an existing Antigravity account."))
      continue
    mode = _path_mode(path)
    if mode is not None and mode & 0o077:
      rows.append(_row("WARN", label, f"{path} permissions are {oct(mode)}", f"Run chmod 600 {path}."))
    else:
      rows.append(_row("PASS", label, f"present with permissions {oct(mode) if mode is not None else 'unknown'}"))
  return rows


def _check_config() -> list[DoctorRow]:
  rows: list[DoctorRow] = []
  config_path = get_hermes_home() / "config.yaml"
  if config_path.exists():
    try:
      import yaml  # type: ignore
      rows.append(_row("PASS", "PyYAML", "PyYAML is available for config.yaml"))
      try:
        with open(config_path, "r", encoding="utf-8") as f:
          parsed = yaml.safe_load(f)
        if parsed is None or isinstance(parsed, dict):
          rows.append(_row("PASS", "config.yaml", "parsed successfully"))
        else:
          rows.append(_row("FAIL", "config.yaml", "top-level YAML value is not a mapping", "Make ~/.hermes/config.yaml a YAML mapping."))
      except Exception as exc:
        rows.append(_row("FAIL", "config.yaml", f"YAML parse failed: {exc}", "Fix the YAML syntax and rerun doctor."))
    except Exception:
      rows.append(_row("WARN", "PyYAML", f"{config_path} exists but PyYAML is not installed", "Install with pip install 'hermes-antigravity-auth[yaml]' or pip install pyyaml."))
  else:
    rows.append(_row("WARN", "config.yaml", f"{config_path} is missing", "Create config.yaml if you need plugin settings; defaults are usable."))
  try:
    from .config import get_config
    config = get_config(force_reload=True)
    rows.append(_row("PASS", "config validation", f"account_selection_strategy={config.account_selection_strategy}, scheduling_mode={config.scheduling_mode}"))
  except Exception as exc:
    rows.append(_row("FAIL", "config validation", f"could not load config: {exc}", "Fix config.yaml or environment overrides."))
  return rows


def _check_active_refresh() -> DoctorRow:
  try:
    data = load_accounts()
    accounts = data.get("accounts", [])
    if not isinstance(accounts, list) or not accounts:
      return _row("WARN", "active token refresh", "no Antigravity accounts are registered", "Run hermes antigravity login.")
    idx = resolve_active_account_index(data)
    account = accounts[idx]
    if not isinstance(account, dict):
      return _row("FAIL", "active token refresh", "active account entry is not an object", "Recreate the account store with hermes antigravity login.")
    refresh_token = account.get("refreshToken")
    if not refresh_token:
      return _row("FAIL", "active token refresh", "active account has no refresh token", "Run hermes antigravity login for this account again.")
    packed = format_refresh_parts({
      "refreshToken": refresh_token,
      "projectId": account.get("projectId") or "",
      "managedProjectId": account.get("managedProjectId") or "",
    })
    refreshed = refresh_access_token({"refresh": packed, "email": account.get("email")}, persist=False, set_active=False)
    if refreshed.get("access"):
      return _row("PASS", "active token refresh", f"refresh succeeded for {account.get('email') or 'active account'}")
    return _row("FAIL", "active token refresh", "refresh response did not contain an access token", "Run hermes antigravity login again.")
  except Exception as exc:
    return _row("FAIL", "active token refresh", f"refresh failed: {exc}", "Run hermes antigravity login again or remove revoked accounts with hermes antigravity delete.")


def _check_model_registry() -> DoctorRow:
  try:
    from .transform.envelope import MODEL_NAME_MAP
    required = [
      "claude-sonnet-4-6-thinking",
      "claude-opus-4-6-thinking",
      "gemini-3.1-pro",
      "gemini-3.1-pro-high",
      "gemini-3.5-flash",
      "gemini-3.5-flash-high",
      "gemini-3.5-flash-low",
    ]
    missing = [model for model in required if model not in MODEL_NAME_MAP]
    if missing:
      return _row("FAIL", "model registry", "missing model IDs: " + ", ".join(missing), "Update transform/envelope.py model registry and README model table together.")
    return _row("PASS", "model registry", f"{len(MODEL_NAME_MAP)} model aliases registered")
  except Exception as exc:
    return _row("FAIL", "model registry", f"could not import model registry: {exc}", "Reinstall hermes-antigravity-auth.")


def run_doctor() -> list[DoctorRow]:
  rows: list[DoctorRow] = []
  rows.append(_check_entrypoint())
  rows.extend(_check_hermes_adapter())
  rows.append(_check_interceptor())
  rows.append(_check_retry_behavior())
  rows.append(_check_account_store_locking())
  rows.append(_check_account_store())
  rows.extend(_check_auth_files())
  rows.extend(_check_config())
  rows.append(_check_active_refresh())
  rows.append(_check_model_registry())
  redacted_rows = redact_secrets([row.__dict__ for row in rows])
  return [
    DoctorRow(
      status=str(row.get("status", "FAIL")),
      check=str(row.get("check", "unknown")),
      detail=str(row.get("detail", "")),
      fix=str(row.get("fix", "")),
    )
    for row in redacted_rows
    if isinstance(row, dict)
  ]


def format_doctor_rows(rows: list[DoctorRow]) -> str:
  lines = ["Google Antigravity doctor", "==========================="]
  for row in rows:
    line = f"{row.status:<4} {row.check}: {row.detail}"
    lines.append(line)
    if row.status in ("WARN", "FAIL") and row.fix:
      lines.append(f"     fix: {row.fix}")
  return "\n".join(lines)


def print_doctor() -> None:
  print(format_doctor_rows(run_doctor()))
