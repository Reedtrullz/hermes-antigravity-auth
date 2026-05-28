"""Hermes provider profile for Antigravity aliases."""

from __future__ import annotations

import os

from providers import register_provider
from providers.base import ProviderProfile


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
    except Exception:
      pass

  if client_id:
    os.environ.setdefault("HERMES_GEMINI_CLIENT_ID", client_id)
  if client_secret:
    os.environ.setdefault("HERMES_GEMINI_CLIENT_SECRET", client_secret)




class AntigravityProfile(ProviderProfile):
  """Antigravity model names routed through Hermes' google-gemini-cli client."""


# Model names MUST match what the Cloud Code API (cloudcode-pa.googleapis.com)
# actually recognises.  Antigravity 2.0 (May 2026) uses bare names without the
# -preview suffix for 3.1+ models.  Gemini 3.0 keeps the legacy -preview suffix.
# Claude names are passed through as-is — Antigravity forwards those to the
# Anthropic backend.
ANTIGRAVITY_MODELS = (
  # Claude models
  "claude-opus-4-6-thinking",
  "claude-sonnet-4-6-thinking",
  "claude-sonnet-4-6",
  # Gemini 3.5 (latest)
  "gemini-3.5-flash-high",
  "gemini-3.5-flash-medium",
  # Gemini 3.1
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
  default_aux_model="gemini-3.5-flash-medium",
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
  except Exception:
    return

  models._PROVIDER_MODELS["google-gemini-cli"] = list(ANTIGRAVITY_MODELS)

  label = "Google Antigravity"
  desc = "Google Antigravity (Claude/Gemini via OAuth + Code Assist)"

  # Alias registration — non-fatal, best-effort.
  try:
    models._PROVIDER_LABELS["google-gemini-cli"] = label
    for alias in ANTIGRAVITY_ALIASES:
      models._PROVIDER_ALIASES[alias] = "google-gemini-cli"
  except Exception:
    pass

  # Provider label overrides and CLI aliases — optional Hermes internals.
  try:
    import hermes_cli.providers as cli_providers

    cli_providers._LABEL_OVERRIDES["google-gemini-cli"] = label
    for alias in ANTIGRAVITY_ALIASES:
      cli_providers.ALIASES[alias] = "google-gemini-cli"
  except Exception:
    pass

  try:
    replacement = models.ProviderEntry("google-gemini-cli", label, desc)
    for index, entry in enumerate(models.CANONICAL_PROVIDERS):
      if entry.slug == "google-gemini-cli":
        models.CANONICAL_PROVIDERS[index] = replacement
        break
  except Exception:
    pass


register_provider(antigravity)
_patch_hermes_model_picker()

try:
  from hermes_cli.auth import PROVIDER_REGISTRY, ProviderConfig

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
except Exception:
  pass

try:
  from .interceptor import install as _install_interceptor

  _install_interceptor()
except Exception:
  pass
