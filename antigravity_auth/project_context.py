"""Antigravity-aware Code Assist project context resolution."""

from __future__ import annotations

import gzip
import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from .constants import ANTIGRAVITY_ENDPOINT_PROD, get_antigravity_headers

FREE_TIER_ID = "free-tier"
LEGACY_TIER_ID = "legacy-tier"
STANDARD_TIER_ID = "standard-tier"
ANTIGRAVITY_METADATA = {
  "ideType": "ANTIGRAVITY",
  "platform": "PLATFORM_UNSPECIFIED",
  "pluginType": "GEMINI",
}
ONBOARDING_POLL_ATTEMPTS = 12
ONBOARDING_POLL_INTERVAL_SECONDS = 5


try:
  from agent.google_code_assist import CodeAssistError, ProjectContext, ProjectIdRequiredError
except Exception:
  class CodeAssistError(RuntimeError):
    def __init__(self, message: str, *, code: str = "code_assist_error") -> None:
      super().__init__(message)
      self.code = code

  class ProjectIdRequiredError(CodeAssistError):
    def __init__(self, message: str = "GCP project id required for Antigravity standard tier") -> None:
      super().__init__(message, code="code_assist_project_id_required")

  @dataclass
  class ProjectContext:
    project_id: str = ""
    managed_project_id: str = ""
    tier_id: str = ""
    source: str = ""


def _decode_response(response: Any, body: bytes) -> str:
  try:
    if "gzip" in (response.headers.get("Content-Encoding") or ""):
      body = gzip.decompress(body)
  except Exception:
    pass
  return body.decode("utf-8", errors="replace")


def _headers(access_token: str) -> dict[str, str]:
  antigravity_headers = get_antigravity_headers()
  return {
    "Authorization": f"Bearer {access_token}",
    "Content-Type": "application/json",
    "Accept": "application/json",
    "User-Agent": antigravity_headers["User-Agent"],
    "X-Goog-Api-Client": antigravity_headers["X-Goog-Api-Client"],
    "Client-Metadata": json.dumps(ANTIGRAVITY_METADATA),
  }


def _post_url(url: str, body: dict[str, Any], access_token: str, *, timeout: int = 30) -> dict[str, Any]:
  data = json.dumps(body).encode("utf-8")
  request = urllib.request.Request(url, data=data, method="POST", headers=_headers(access_token))
  try:
    with urllib.request.urlopen(request, timeout=timeout) as response:
      raw = _decode_response(response, response.read())
      return json.loads(raw) if raw else {}
  except urllib.error.HTTPError as exc:
    raw = _decode_response(exc, exc.read())
    raise CodeAssistError(
      f"Antigravity project context HTTP {exc.code}: {raw or exc.reason}",
      code=f"antigravity_project_context_http_{exc.code}",
    ) from exc
  except urllib.error.URLError as exc:
    raise CodeAssistError(
      f"Antigravity project context request failed: {exc}",
      code="antigravity_project_context_network_error",
    ) from exc


def _post_json(path: str, body: dict[str, Any], access_token: str, *, timeout: int = 30) -> dict[str, Any]:
  return _post_url(f"{ANTIGRAVITY_ENDPOINT_PROD}/v1internal:{path}", body, access_token, timeout=timeout)


def _poll_operation(operation_name: str, access_token: str) -> dict[str, Any]:
  if not operation_name:
    return {}
  poll_url = f"{ANTIGRAVITY_ENDPOINT_PROD}/v1internal/{operation_name}"
  last_response: dict[str, Any] = {}
  for _ in range(ONBOARDING_POLL_ATTEMPTS):
    time.sleep(ONBOARDING_POLL_INTERVAL_SECONDS)
    last_response = _post_url(poll_url, {}, access_token)
    if last_response.get("done"):
      return last_response
  return last_response


def _project_id_from_value(value: Any) -> str:
  if isinstance(value, str):
    return value
  if isinstance(value, dict):
    return str(value.get("id") or value.get("projectId") or "")
  return ""


def _tier_ids(items: Any) -> list[str]:
  if not isinstance(items, list):
    return []
  ids: list[str] = []
  for item in items:
    if isinstance(item, dict):
      tier_id = str(item.get("id") or item.get("tierId") or "")
      if tier_id:
        ids.append(tier_id)
  return ids


def load_antigravity_code_assist(access_token: str, *, project_id: str = "") -> dict[str, Any]:
  body: dict[str, Any] = {
    "metadata": {
      "duetProject": project_id,
      **ANTIGRAVITY_METADATA,
    },
  }
  if project_id:
    body["cloudaicompanionProject"] = project_id
  return _post_json("loadCodeAssist", body, access_token)


def onboard_antigravity_user(access_token: str, *, tier_id: str, project_id: str = "") -> dict[str, Any]:
  if tier_id not in (FREE_TIER_ID, LEGACY_TIER_ID) and not project_id:
    raise ProjectIdRequiredError(_standard_tier_project_required_message())
  body: dict[str, Any] = {
    "tierId": tier_id,
    "metadata": ANTIGRAVITY_METADATA,
  }
  if project_id:
    body["cloudaicompanionProject"] = project_id
  response = _post_json("onboardUser", body, access_token)
  if response.get("done") is False and response.get("name"):
    return _poll_operation(str(response.get("name") or ""), access_token)
  return response


def _standard_tier_project_required_message() -> str:
  return (
    "Google reports this account is not eligible for Antigravity free tier, "
    "but standard-tier Antigravity is available and requires a GCP project ID. "
    "Run `hermes antigravity set-project <email_or_index> <project_id>` or set "
    "HERMES_GEMINI_PROJECT_ID / GOOGLE_CLOUD_PROJECT, then retry."
  )


def resolve_antigravity_project_context(
    access_token: str,
    *,
    configured_project_id: str = "",
    env_project_id: str = "",
    stored_project_id: str = "",
    managed_project_id: str = "",
) -> ProjectContext:
  project_id = configured_project_id or env_project_id or stored_project_id
  info = load_antigravity_code_assist(access_token, project_id=project_id)

  current_tier = info.get("currentTier") if isinstance(info.get("currentTier"), dict) else {}
  tier_id = str(current_tier.get("id") or "")
  discovered_project = _project_id_from_value(info.get("cloudaicompanionProject"))
  allowed_tiers = _tier_ids(info.get("allowedTiers"))
  ineligible_tiers = _tier_ids(info.get("ineligibleTiers"))
  effective_project = project_id or discovered_project

  if tier_id:
    return ProjectContext(
      project_id=effective_project,
      managed_project_id=managed_project_id or (effective_project if tier_id == FREE_TIER_ID else ""),
      tier_id=tier_id,
      source="antigravity-discovered",
    )

  if effective_project and STANDARD_TIER_ID in allowed_tiers:
    onboard_antigravity_user(access_token, tier_id=STANDARD_TIER_ID, project_id=effective_project)
    return ProjectContext(
      project_id=effective_project,
      managed_project_id=managed_project_id,
      tier_id=STANDARD_TIER_ID,
      source="antigravity-standard-tier",
    )

  if FREE_TIER_ID in allowed_tiers and FREE_TIER_ID not in ineligible_tiers:
    onboard_response = onboard_antigravity_user(access_token, tier_id=FREE_TIER_ID, project_id="")
    response_body = onboard_response.get("response") if isinstance(onboard_response.get("response"), dict) else {}
    onboard_project = _project_id_from_value(response_body.get("cloudaicompanionProject"))
    return ProjectContext(
      project_id=onboard_project or discovered_project,
      managed_project_id=onboard_project or discovered_project,
      tier_id=FREE_TIER_ID,
      source="antigravity-free-tier",
    )

  if STANDARD_TIER_ID in allowed_tiers:
    raise ProjectIdRequiredError(_standard_tier_project_required_message())

  raise CodeAssistError(
    "Google did not report an eligible Antigravity tier for this account.",
    code="antigravity_no_eligible_tier",
  )
