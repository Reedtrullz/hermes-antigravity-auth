"""Feature detection for private Hermes internals patched by Antigravity."""

from __future__ import annotations

import importlib
import importlib.metadata
from dataclasses import dataclass
from types import ModuleType
from typing import Any


@dataclass(frozen=True)
class HermesFeature:
  status: str
  check: str
  detail: str
  fix: str = ""


def _version(package: str) -> str:
  try:
    return importlib.metadata.version(package)
  except Exception:
    return "unknown"


def _import_module(name: str) -> tuple[ModuleType | None, Exception | None]:
  try:
    return importlib.import_module(name), None
  except Exception as exc:
    return None, exc


def _missing(module: Any, names: tuple[str, ...]) -> list[str]:
  return [name for name in names if not hasattr(module, name)]


def detect_hermes_features() -> list[HermesFeature]:
  """Inspect Hermes internals this package patches.

  The checks are deliberately structural. Hermes does not promise these module
  globals as a public API, so we verify the exact attributes before patching.
  """
  rows: list[HermesFeature] = [
    HermesFeature("INFO", "Hermes package version", f"hermes-agent={_version('hermes-agent')}, hermes-cli={_version('hermes-cli')}"),
  ]

  models, models_error = _import_module("hermes_cli.models")
  if models is None:
    rows.append(HermesFeature(
      "WARN",
      "Hermes model picker internals",
      f"hermes_cli.models unavailable: {models_error}",
      "Standalone provider fallback remains available; picker branding patches are skipped.",
    ))
  else:
    required = ("_PROVIDER_MODELS", "_PROVIDER_LABELS", "_PROVIDER_ALIASES", "ProviderEntry", "CANONICAL_PROVIDERS")
    missing = _missing(models, required)
    if missing:
      rows.append(HermesFeature(
        "WARN",
        "Hermes model picker internals",
        "missing " + ", ".join(missing),
        "Use the standalone provider fallback or upgrade Hermes.",
      ))
    else:
      rows.append(HermesFeature("PASS", "Hermes model picker internals", "required model picker symbols are available"))

    group_missing = _missing(models, ("PROVIDER_GROUPS", "_SLUG_TO_GROUP"))
    if group_missing:
      rows.append(HermesFeature(
        "WARN",
        "Hermes provider grouping internals",
        "missing " + ", ".join(group_missing),
        "Antigravity can still register, but picker de-grouping is unavailable.",
      ))
    else:
      rows.append(HermesFeature("PASS", "Hermes provider grouping internals", "provider grouping tables are available"))

  providers, providers_error = _import_module("hermes_cli.providers")
  if providers is None:
    rows.append(HermesFeature(
      "WARN",
      "Hermes provider alias internals",
      f"hermes_cli.providers unavailable: {providers_error}",
      "Use google-gemini-cli if Antigravity aliases are not shown.",
    ))
  else:
    missing = _missing(providers, ("_LABEL_OVERRIDES", "ALIASES"))
    if missing:
      rows.append(HermesFeature(
        "WARN",
        "Hermes provider alias internals",
        "missing " + ", ".join(missing),
        "Use google-gemini-cli if Antigravity aliases are not shown.",
      ))
    else:
      rows.append(HermesFeature("PASS", "Hermes provider alias internals", "provider aliases can be patched"))

  auth, auth_error = _import_module("hermes_cli.auth")
  if auth is None:
    rows.append(HermesFeature(
      "WARN",
      "Hermes auth registry internals",
      f"hermes_cli.auth unavailable: {auth_error}",
      "Provider profile registration or standalone fallback will be used.",
    ))
  else:
    missing = _missing(auth, ("PROVIDER_REGISTRY", "ProviderConfig"))
    if missing:
      rows.append(HermesFeature(
        "WARN",
        "Hermes auth registry internals",
        "missing " + ", ".join(missing),
        "Provider profile registration or standalone fallback will be used.",
      ))
    else:
      rows.append(HermesFeature("PASS", "Hermes auth registry internals", "auth provider registry can be patched"))

  runtime_helpers, runtime_helpers_error = _import_module("agent.agent_runtime_helpers")
  runtime_provider, runtime_provider_error = _import_module("hermes_cli.runtime_provider")
  auxiliary_client, auxiliary_client_error = _import_module("agent.auxiliary_client")
  runtime_missing: list[str] = []
  runtime_errors: list[str] = []
  if runtime_helpers is None:
    runtime_errors.append(f"agent.agent_runtime_helpers unavailable: {runtime_helpers_error}")
  else:
    runtime_missing.extend(
      f"agent.agent_runtime_helpers.{name}"
      for name in _missing(runtime_helpers, ("create_openai_client",))
    )
  if runtime_provider is None:
    runtime_errors.append(f"hermes_cli.runtime_provider unavailable: {runtime_provider_error}")
  else:
    runtime_missing.extend(
      f"hermes_cli.runtime_provider.{name}"
      for name in _missing(runtime_provider, ("resolve_runtime_provider",))
    )
  if auxiliary_client is None:
    runtime_errors.append(f"agent.auxiliary_client unavailable: {auxiliary_client_error}")
  else:
    runtime_missing.extend(
      f"agent.auxiliary_client.{name}"
      for name in _missing(auxiliary_client, ("resolve_provider_client",))
    )
  runtime_ok = False
  if runtime_errors:
    rows.append(HermesFeature(
      "WARN",
      "Hermes runtime factory internals",
      "; ".join(runtime_errors),
      "Standalone provider fallback remains available; runtime factory patching is skipped.",
    ))
  elif runtime_missing:
    rows.append(HermesFeature(
      "WARN",
      "Hermes runtime factory internals",
      "missing " + ", ".join(runtime_missing),
      "Use a Hermes build that exposes runtime provider factory hooks.",
    ))
  else:
    runtime_ok = True
    rows.append(HermesFeature(
      "PASS",
      "Hermes runtime factory internals",
      "runtime provider factory hooks are available",
    ))

  adapter, adapter_error = _import_module("agent.gemini_cloudcode_adapter")
  if adapter is None:
    rows.append(HermesFeature(
      "INFO" if runtime_ok else "WARN",
      "Hermes Cloud Code adapter internals",
      f"agent.gemini_cloudcode_adapter unavailable: {adapter_error}",
      "Modern runtime factory hooks will be used." if runtime_ok else "Standalone provider fallback remains available; HTTP interceptor cannot install outside Hermes.",
    ))
  else:
    missing = _missing(adapter, ("GeminiCloudCodeClient", "wrap_code_assist_request"))
    if missing:
      rows.append(HermesFeature(
        "FAIL",
        "Hermes Cloud Code adapter internals",
        "missing " + ", ".join(missing),
        "Use a Hermes build with google-gemini-cli Cloud Code support.",
      ))
    else:
      client = getattr(adapter, "GeminiCloudCodeClient")
      optional = "project context hook available" if hasattr(client, "_ensure_project_context") else "project context hook unavailable"
      rows.append(HermesFeature("PASS", "Hermes Cloud Code adapter internals", f"required adapter symbols are available; {optional}"))

  return rows


def diagnostics_from_features(features: list[HermesFeature]) -> list[dict[str, str]]:
  """Convert feature rows to provider/doctor diagnostic dictionaries."""
  return [feature.__dict__.copy() for feature in features]


def has_required_model_picker_features(models: Any) -> tuple[bool, list[str]]:
  required = ("_PROVIDER_MODELS", "_PROVIDER_LABELS", "_PROVIDER_ALIASES", "ProviderEntry", "CANONICAL_PROVIDERS")
  missing = _missing(models, required)
  return not missing, missing


def has_grouping_features(models: Any) -> bool:
  return not _missing(models, ("PROVIDER_GROUPS", "_SLUG_TO_GROUP"))


def has_modern_runtime_features() -> bool:
  helpers, _ = _import_module("agent.agent_runtime_helpers")
  runtime_provider, _ = _import_module("hermes_cli.runtime_provider")
  auxiliary_client, _ = _import_module("agent.auxiliary_client")
  return (
    helpers is not None
    and runtime_provider is not None
    and auxiliary_client is not None
    and hasattr(helpers, "create_openai_client")
    and hasattr(runtime_provider, "resolve_runtime_provider")
    and hasattr(auxiliary_client, "resolve_provider_client")
  )
