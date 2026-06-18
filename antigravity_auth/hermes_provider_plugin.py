"""Hermes provider profile for Antigravity aliases."""

from __future__ import annotations

from dataclasses import dataclass, field
import logging
import os
import threading
import time
from typing import Any

from .hermes_compat import (
  detect_hermes_features,
  diagnostics_from_features,
  has_grouping_features,
  has_required_model_picker_features,
)
from .redaction import redact_secret_text


logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())
_PROVIDER_DIAGNOSTICS: list[dict[str, str]] = []
_PROVIDERS_API_AVAILABLE = True


def _record(status: str, check: str, detail: str, fix: str = "") -> None:
  detail = redact_secret_text(detail)
  fix = redact_secret_text(fix)
  item = {"status": status, "check": check, "detail": detail, "fix": fix}
  if item not in _PROVIDER_DIAGNOSTICS:
    _PROVIDER_DIAGNOSTICS.append(item)
  if status == "FAIL":
    logger.error("%s: %s", check, detail)
  elif status == "WARN":
    logger.warning("%s: %s", check, detail)
  else:
    logger.info("%s: %s", check, detail)


def get_provider_diagnostics() -> list[dict[str, str]]:
  """Return provider-load diagnostics recorded in this process."""
  return [dict(item) for item in _PROVIDER_DIAGNOSTICS]


def _record_hermes_feature_diagnostics() -> None:
  for item in diagnostics_from_features(detect_hermes_features()):
    _record(
      str(item.get("status", "WARN")),
      str(item.get("check", "Hermes compatibility")),
      str(item.get("detail", "")),
      str(item.get("fix", "")),
    )


def ensure_provider_loaded() -> bool:
  """Marker used by the CLI plugin after module import side effects run."""
  return True


try:
  from providers import register_provider
  from providers.base import ProviderProfile
except Exception as exc:
  _PROVIDERS_API_AVAILABLE = False
  _record(
    "WARN",
    "provider registration API",
    f"could not import Hermes providers API: {exc}",
    "Load inside Hermes or use a Hermes build that exposes providers.register_provider.",
  )
  @dataclass
  class ProviderProfile:
    name: str
    api_mode: str = "chat_completions"
    aliases: tuple = ()
    display_name: str = ""
    description: str = ""
    signup_url: str = ""
    env_vars: tuple = ()
    base_url: str = ""
    models_url: str = ""
    auth_type: str = "api_key"
    supports_health_check: bool = True
    fallback_models: tuple = ()
    hostname: str = ""
    default_headers: dict[str, str] = field(default_factory=dict)
    fixed_temperature: Any = None
    default_max_tokens: int | None = None
    default_aux_model: str = ""

  def register_provider(profile: ProviderProfile) -> None:
    _record(
      "WARN",
      "provider profile registration",
      f"standalone fallback active; provider profile {profile.name} was not registered with Hermes providers API",
      "Run hermes antigravity doctor inside the Hermes Agent environment.",
    )


# ---------------------------------------------------------------------------
# OAuth client credentials — bridge the antigravity-auth credentials into the
# Hermes google_oauth module which reads HERMES_GEMINI_CLIENT_ID / SECRET.
# ---------------------------------------------------------------------------
def _set_oauth_env_from_credentials() -> None:
  """Load Antigravity OAuth creds and export the env vars that
  agent.google_oauth expects."""
  if os.getenv("HERMES_GEMINI_CLIENT_ID") and os.getenv("HERMES_GEMINI_CLIENT_SECRET"):
    return

  client_id = os.getenv("ANTIGRAVITY_CLIENT_ID", "").strip()
  client_secret = os.getenv("ANTIGRAVITY_CLIENT_SECRET", "").strip()

  if not client_id or not client_secret:
    try:
      from .credentials import resolve_oauth_credentials

      resolved_id, resolved_secret = resolve_oauth_credentials()
      client_id = client_id or resolved_id
      client_secret = client_secret or resolved_secret
    except Exception as exc:
      _record(
        "WARN",
        "OAuth credential bridge",
        f"could not resolve Antigravity OAuth credentials for Hermes env bridge: {exc}",
        "Set ANTIGRAVITY_CLIENT_ID/ANTIGRAVITY_CLIENT_SECRET or run hermes antigravity doctor.",
      )

  if client_id:
    os.environ.setdefault("HERMES_GEMINI_CLIENT_ID", client_id)
  if client_secret:
    os.environ.setdefault("HERMES_GEMINI_CLIENT_SECRET", client_secret)




class AntigravityProfile(ProviderProfile):
  """Antigravity model names routed through Hermes' google-gemini-cli client."""


# User-facing aliases are listed here; transform/envelope.py maps them to the
# Cloud Code IDs Google currently accepts.  Gemini 3.5 Flash is especially
# backend-specific: high routes to gemini-3-flash-agent, while the other 3.5
# Flash aliases route to gemini-3.5-flash-low.
# Claude names are passed through as-is; Antigravity forwards those to the
# Anthropic backend.
ANTIGRAVITY_MODELS = (
  # Claude models
  "claude-opus-4-6-thinking",
  "claude-sonnet-4-6-thinking",
  "claude-sonnet-4-6",
  # Gemini 3.5 Flash aliases
  "gemini-3.5-flash",
  "gemini-3.5-flash-high",
  "gemini-3.5-flash-medium",
  "gemini-3.5-flash-low",
  "gemini-3.5-flash-minimal",
  # Gemini 3.1 Pro aliases
  "gemini-3.1-pro",
  "gemini-3.1-pro-preview",
  "gemini-3.1-pro-high",
  "gemini-3.1-pro-low",
  # Gemini 3.0 (legacy -preview suffix)
  "gemini-3-pro-preview",
  "gemini-3-flash-preview",
  # Gemini 2.5 (legacy)
  "gemini-2.5-pro",
  "gemini-2.5-flash",
  # GPT-OSS
  "gpt-oss-120b-medium",
)


ANTIGRAVITY_ALIASES = ("antigravity", "antigravity-google", "ag", "gemini-cli", "gemini-oauth")


antigravity = AntigravityProfile(
  name="google-gemini-cli",
  aliases=ANTIGRAVITY_ALIASES,
  display_name="Google Antigravity",
  description="Google Antigravity OAuth via Hermes' native Cloud Code transport",
  env_vars=(),
  base_url="cloudcode-pa://google",
  auth_type="oauth_external",
  default_aux_model="gemini-3.5-flash",
  fallback_models=ANTIGRAVITY_MODELS,
  default_headers={},
)

def _patch_hermes_model_picker() -> None:
  """Expose Antigravity branding in Hermes' built-in model pickers.


  Hermes v0.14 only auto-adds simple api-key model-provider plugins to
  `hermes model` and `/model`. OAuth providers need bespoke picker handling,
  so this plugin brands the supported google-gemini-cli picker entry as
  Antigravity while preserving its native Cloud Code runtime.
  """
  _set_oauth_env_from_credentials()
  # Import hermes_cli.models independently
  # be gated on hermes_cli.providers being importable (it pulls in yaml
  # which may not be available in all environments).
  try:
    import hermes_cli.models as models
  except Exception as exc:
    _record(
      "WARN",
      "model picker patch",
      f"could not import hermes_cli.models: {exc}",
      "Standalone provider fallback remains available; picker branding patches are skipped.",
    )
    return

  supported, missing = has_required_model_picker_features(models)
  if not supported:
    _record(
      "WARN",
      "model picker patch",
      "skipped because hermes_cli.models is missing " + ", ".join(missing),
      "Standalone provider fallback remains available; upgrade Hermes for picker branding.",
    )
    return

  label = "Google Antigravity"
  desc = "Google Antigravity (Claude/Gemini via OAuth + Code Assist)"

  def apply_patches() -> bool:
    try:
      models._PROVIDER_MODELS["google-gemini-cli"] = list(ANTIGRAVITY_MODELS)
    except Exception as exc:
      _record(
        "FAIL",
        "model picker provider models",
        f"could not register Antigravity models in hermes_cli.models: {exc}",
        "Upgrade Hermes Agent or use a compatible Hermes build.",
      )
      return False

    # Alias registration — non-fatal, best-effort.
    try:
      models._PROVIDER_LABELS["google-gemini-cli"] = label
      for alias in ANTIGRAVITY_ALIASES:
        models._PROVIDER_ALIASES[alias] = "google-gemini-cli"
    except Exception as exc:
      _record(
        "WARN",
        "model picker aliases",
        f"could not patch hermes_cli.models aliases: {exc}",
        "Antigravity may appear under the native google-gemini-cli provider name.",
      )

    # Provider label overrides and CLI aliases — optional Hermes internals.
    try:
      import hermes_cli.providers as cli_providers

      cli_providers._LABEL_OVERRIDES["google-gemini-cli"] = label
      for alias in ANTIGRAVITY_ALIASES:
        cli_providers.ALIASES[alias] = "google-gemini-cli"
    except Exception as exc:
      _record(
        "WARN",
        "provider aliases",
        f"could not patch hermes_cli.providers aliases: {exc}",
        "Use google-gemini-cli if Antigravity aliases are unavailable.",
      )

    try:
      replacement = models.ProviderEntry("google-gemini-cli", label, desc)
      for index, entry in enumerate(models.CANONICAL_PROVIDERS):
        if entry.slug == "google-gemini-cli":
          models.CANONICAL_PROVIDERS[index] = replacement
          break
    except Exception as exc:
      _record(
        "WARN",
        "canonical provider row",
        f"could not patch Hermes canonical provider row: {exc}",
        "Antigravity may still work but may keep the native Google display label.",
      )

    groups_ready = has_grouping_features(models)
    if groups_ready:
      try:
        group_label, members = models.PROVIDER_GROUPS.get("google", ("Google Gemini", []))
        if "google-gemini-cli" in members:
          remaining = [slug for slug in members if slug != "google-gemini-cli"]
          if remaining:
            models.PROVIDER_GROUPS["google"] = (group_label, remaining)
          else:
            models.PROVIDER_GROUPS.pop("google", None)
        models._SLUG_TO_GROUP.pop("google-gemini-cli", None)
      except Exception as exc:
        _record(
          "WARN",
          "provider grouping",
          f"could not separate Antigravity from the Google provider group: {exc}",
          "Antigravity may appear under the grouped Google picker row.",
        )
    return groups_ready

  if apply_patches():
    _record("PASS", "model picker patch", "Antigravity provider models and picker branding patched")
    return

  _record(
    "WARN",
    "model picker group tables",
    "Hermes provider group tables were not ready; scheduled a short late patch",
    "If Antigravity remains grouped under Google, restart Hermes and rerun doctor.",
  )

  def late_patch() -> None:
    for _ in range(1000):
      if apply_patches():
        _record("PASS", "model picker patch", "Antigravity provider picker branding patched after delayed Hermes initialization")
        return
      time.sleep(0.001)
    _record(
      "WARN",
      "model picker group tables",
      "Hermes provider group tables did not become available during delayed patch",
      "Use google-gemini-cli if the Antigravity picker row is unavailable.",
    )

  threading.Thread(target=late_patch, daemon=True).start()


try:
  register_provider(antigravity)
  if _PROVIDERS_API_AVAILABLE:
    _record("PASS", "provider profile registration", "registered google-gemini-cli Antigravity provider profile")
except Exception as exc:
  _record(
    "FAIL",
    "provider profile registration",
    f"register_provider failed for google-gemini-cli: {exc}",
    "Run hermes antigravity doctor inside Hermes and verify provider plugin compatibility.",
  )
_record_hermes_feature_diagnostics()
_patch_hermes_model_picker()

try:
  from hermes_cli.auth import PROVIDER_REGISTRY, ProviderConfig
  if not hasattr(PROVIDER_REGISTRY, "get") or not callable(ProviderConfig):
    raise TypeError("hermes_cli.auth registry does not expose PROVIDER_REGISTRY.get and callable ProviderConfig")

  target = PROVIDER_REGISTRY.get("google-gemini-cli")
  if target is None:
    target = ProviderConfig(
      id="google-gemini-cli",
      name="Google Antigravity",
      auth_type="oauth_external",
      inference_base_url="cloudcode-pa://google",
    )
    PROVIDER_REGISTRY["google-gemini-cli"] = target
  else:
    # Patch the existing entry's display name for the /model picker
    target.name = "Google Antigravity"
  for _alias in antigravity.aliases:
    PROVIDER_REGISTRY[_alias] = target
  _record("PASS", "provider auth registry", "patched Hermes auth provider registry aliases")
except Exception as exc:
  _record(
    "WARN",
    "provider auth registry",
    f"could not patch hermes_cli.auth provider registry: {exc}",
    "Hermes may still route through provider profile registration; rerun doctor inside Hermes.",
  )

_interceptor_installed = False

try:
  from .interceptor import install as _install_interceptor

  _interceptor_installed = _install_interceptor()
  if _interceptor_installed:
    _record("PASS", "provider interceptor", "HTTP interceptor installed from provider plugin")
  else:
    _record(
      "WARN",
      "provider interceptor",
      "interceptor install returned false from provider plugin",
      "Run hermes antigravity status to diagnose Claude routing.",
    )
except Exception as _exc:
  logger.error(
    "Antigravity HTTP interceptor failed to install. "
    "Claude models will not work through Antigravity. "
    "Error: %s",
    _exc,
    exc_info=True,
  )
  _record(
    "FAIL",
    "provider interceptor",
    f"HTTP interceptor failed to install: {_exc}",
    "Run hermes antigravity status to diagnose Claude routing.",
  )

# Warn loudly if interceptor failed to install — Claude models require it.
if not _interceptor_installed:
  logger.warning(
    "Antigravity HTTP interceptor is NOT installed. "
    "Gemini models may work via Code Assist, but Claude models require the "
    "interceptor for Antigravity header/response transformation. "
    "Run 'hermes antigravity status' to diagnose."
  )
