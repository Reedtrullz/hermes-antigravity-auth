"""Feature detection for private Hermes internals patched by Antigravity."""

from __future__ import annotations

import importlib
import importlib.metadata
from dataclasses import dataclass
from types import ModuleType
from typing import Any


NATIVE_ADAPTER_SYMBOLS = (
  "build_gemini_request",
  "translate_gemini_response",
  "translate_stream_event",
  "_iter_sse_events",
  "gemini_http_error",
)

MODERN_RUNTIME_REQUIREMENTS = (
  ("agent.agent_runtime_helpers", ("create_openai_client",)),
  ("hermes_cli.runtime_provider", ("resolve_runtime_provider",)),
  ("agent.auxiliary_client", ("resolve_provider_client",)),
  ("agent.gemini_native_adapter", NATIVE_ADAPTER_SYMBOLS),
)


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


def modern_runtime_feature_gaps() -> list[str]:
  gaps: list[str] = []
  for module_name, names in MODERN_RUNTIME_REQUIREMENTS:
    module, error = _import_module(module_name)
    if module is None:
      gaps.append(f"{module_name}: {error}")
      continue
    gaps.extend(f"{module_name}.{name}" for name in _missing(module, names))
  return gaps


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

  runtime_ok = False
  runtime_missing = modern_runtime_feature_gaps()
  if runtime_missing:
    rows.append(HermesFeature(
      "WARN",
      "Hermes runtime factory internals",
      "missing or unavailable " + "; ".join(runtime_missing),
      "Use a Hermes build that exposes runtime provider factory hooks and agent.gemini_native_adapter.",
    ))
  else:
    runtime_ok = True
    rows.append(HermesFeature(
      "PASS",
      "Hermes runtime factory internals",
      "runtime provider factory hooks and Gemini native adapter symbols are available",
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
  return not modern_runtime_feature_gaps()
