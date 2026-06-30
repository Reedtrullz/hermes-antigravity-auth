"""Feature detection for private Hermes internals patched by Antigravity."""

from __future__ import annotations

import importlib
import importlib.metadata
import inspect
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


def _accepts_call(callable_obj: Any, *args: Any, **kwargs: Any) -> tuple[bool, str]:
  try:
    signature = inspect.signature(callable_obj)
  except (TypeError, ValueError):
    return True, "signature unavailable; treating callable as compatible"
  try:
    signature.bind_partial(*args, **kwargs)
  except TypeError as exc:
    return False, f"{signature}: {exc}"
  return True, str(signature)


def _runtime_contract_rows() -> list[HermesFeature]:
  rows: list[HermesFeature] = []

  runtime_provider, runtime_error = _import_module("hermes_cli.runtime_provider")
  if runtime_provider is None:
    rows.append(HermesFeature(
      "INFO",
      "Hermes runtime provider contract",
      f"hermes_cli.runtime_provider unavailable: {runtime_error}",
      "Expected outside a Hermes Agent environment.",
    ))
  else:
    resolver = getattr(runtime_provider, "resolve_runtime_provider", None)
    if not callable(resolver):
      rows.append(HermesFeature(
        "WARN",
        "Hermes runtime provider contract",
        "resolve_runtime_provider is missing or not callable",
        "Upgrade Hermes or keep using the standalone provider fallback.",
      ))
    else:
      ok, detail = _accepts_call(
        resolver,
        requested="antigravity",
        explicit_api_key=None,
        explicit_base_url="cloudcode-pa://google",
        target_model="gemini-3.5-flash",
      )
      rows.append(HermesFeature(
        "PASS" if ok else "WARN",
        "Hermes runtime provider contract",
        "resolve_runtime_provider accepts Antigravity call shape: " + detail if ok else "resolve_runtime_provider signature drift: " + detail,
        "" if ok else "Update the Antigravity runtime provider wrapper for this Hermes build.",
      ))

  auxiliary_client, auxiliary_error = _import_module("agent.auxiliary_client")
  if auxiliary_client is None:
    rows.append(HermesFeature(
      "INFO",
      "Hermes auxiliary client contract",
      f"agent.auxiliary_client unavailable: {auxiliary_error}",
      "Expected outside a Hermes Agent environment.",
    ))
  else:
    resolver = getattr(auxiliary_client, "resolve_provider_client", None)
    if not callable(resolver):
      rows.append(HermesFeature(
        "WARN",
        "Hermes auxiliary client contract",
        "resolve_provider_client is missing or not callable",
        "Upgrade Hermes or avoid auxiliary Antigravity client routing.",
      ))
    else:
      ok, detail = _accepts_call(
        resolver,
        "google-gemini-cli",
        model="gemini-3.5-flash",
        async_mode=False,
        raw_codex=False,
        explicit_base_url="cloudcode-pa://google",
        explicit_api_key="antigravity-oauth",
        api_mode=None,
        main_runtime={"provider": "google-gemini-cli", "base_url": "cloudcode-pa://google"},
        is_vision=False,
        task=None,
      )
      rows.append(HermesFeature(
        "PASS" if ok else "WARN",
        "Hermes auxiliary client contract",
        "resolve_provider_client accepts Antigravity call shape: " + detail if ok else "resolve_provider_client signature drift: " + detail,
        "" if ok else "Update the Antigravity auxiliary client wrapper for this Hermes build.",
      ))

  runtime_helpers, helper_error = _import_module("agent.agent_runtime_helpers")
  if runtime_helpers is None:
    rows.append(HermesFeature(
      "INFO",
      "Hermes client factory contract",
      f"agent.agent_runtime_helpers unavailable: {helper_error}",
      "Expected outside a Hermes Agent environment.",
    ))
  else:
    factory = getattr(runtime_helpers, "create_openai_client", None)
    if not callable(factory):
      rows.append(HermesFeature(
        "WARN",
        "Hermes client factory contract",
        "create_openai_client is missing or not callable",
        "Upgrade Hermes or use a build with the Hermes 0.17 client factory.",
      ))
    else:
      ok, detail = _accepts_call(factory, object(), {}, reason="antigravity-contract-check", shared=False)
      rows.append(HermesFeature(
        "PASS" if ok else "WARN",
        "Hermes client factory contract",
        "create_openai_client accepts Antigravity call shape: " + detail if ok else "create_openai_client signature drift: " + detail,
        "" if ok else "Update the Antigravity client-factory wrapper for this Hermes build.",
      ))

  return rows


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

  adapter, adapter_error = _import_module("agent.gemini_cloudcode_adapter")
  if adapter is None:
    rows.append(HermesFeature(
      "INFO",
      "Hermes Cloud Code adapter internals",
      f"agent.gemini_cloudcode_adapter unavailable: {adapter_error}",
      "Expected on Hermes 0.17+ when the Antigravity client-factory transport is available.",
    ))
    native_adapter, native_adapter_error = _import_module("agent.gemini_native_adapter")
    if native_adapter is not None:
      missing = _missing(native_adapter, ("GeminiNativeClient", "build_gemini_request"))
      if missing:
        rows.append(HermesFeature(
          "WARN",
          "Hermes native Gemini adapter internals",
          "agent.gemini_native_adapter is available but missing " + ", ".join(missing),
          "This is not a compatible replacement for the Cloud Code adapter.",
        ))
      else:
        runtime_helpers, _ = _import_module("agent.agent_runtime_helpers")
        try:
          from .cloudcode_client import AntigravityCloudCodeClient
          local_transport_ok = callable(AntigravityCloudCodeClient)
        except Exception:
          local_transport_ok = False
        if (
          runtime_helpers is not None
          and hasattr(runtime_helpers, "create_openai_client")
          and local_transport_ok
        ):
          rows.append(HermesFeature(
            "PASS",
            "Hermes 0.17 Antigravity transport internals",
            "client factory and local Antigravity Cloud Code client are available",
          ))
        else:
          rows.append(HermesFeature(
            "WARN",
            "Hermes native Gemini adapter internals",
            "native Gemini adapter is available, but it is not the Cloud Code transport Antigravity patches",
            "Enable the Hermes 0.17 Antigravity client-factory transport or use a Hermes build with Cloud Code support.",
          ))
    elif native_adapter_error is not None:
      rows.append(HermesFeature(
        "INFO",
        "Hermes native Gemini adapter internals",
        f"agent.gemini_native_adapter unavailable: {native_adapter_error}",
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

  rows.extend(_runtime_contract_rows())
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
