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
  """Load antigravity OAuth creds from _credentials.py and export them as
  the env vars that agent.google_oauth expects."""
  if os.getenv("HERMES_GEMINI_CLIENT_ID") and os.getenv("HERMES_GEMINI_CLIENT_SECRET"):
    return  # user already set explicit overrides — don't clobber
  try:
    from ._credentials import ANTIGRAVITY_CLIENT_ID, ANTIGRAVITY_CLIENT_SECRET
  except ImportError:
    return
  if ANTIGRAVITY_CLIENT_ID:
    os.environ.setdefault("HERMES_GEMINI_CLIENT_ID", ANTIGRAVITY_CLIENT_ID)
  if ANTIGRAVITY_CLIENT_SECRET:
    os.environ.setdefault("HERMES_GEMINI_CLIENT_SECRET", ANTIGRAVITY_CLIENT_SECRET)


_set_oauth_env_from_credentials()


class AntigravityProfile(ProviderProfile):
  """Antigravity model names routed through Hermes' google-gemini-cli client."""


# Model names MUST match what the Cloud Code API (cloudcode-pa.googleapis.com)
# actually recognises.  Non-preview Gemini names ("gemini-3-flash") return 404;
# only the -preview suffixed names work there.  Claude names are passed through
# as-is — Antigravity forwards those to the Anthropic backend.
ANTIGRAVITY_MODELS = (
  "claude-opus-4-6-thinking",
  "claude-sonnet-4-6",
  "gemini-3.1-pro-preview",
  "gemini-3-pro-preview",
  "gemini-3-flash-preview",
  "gemini-2.5-pro",
  "gemini-2.5-flash",
)


antigravity = AntigravityProfile(
  name="google-gemini-cli",
  aliases=("antigravity", "antigravity-google", "ag", "gemini-cli", "gemini-oauth"),
  display_name="Google Antigravity",
  description="Google Antigravity OAuth via Hermes' native Cloud Code transport",
  env_vars=(),
  base_url="cloudcode-pa://google",
  auth_type="oauth_external",
  default_aux_model="gemini-3-flash-preview",
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
  # Import hermes_cli.models independently — the model patching must not
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
    for alias in ("antigravity", "antigravity-google", "ag"):
      models._PROVIDER_ALIASES[alias] = "google-gemini-cli"
  except Exception:
    pass

  # Provider label overrides and CLI aliases — optional Hermes internals.
  try:
    import hermes_cli.providers as cli_providers

    cli_providers._LABEL_OVERRIDES["google-gemini-cli"] = label
    for alias in ("antigravity", "antigravity-google", "ag"):
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
  for _alias in antigravity.aliases:
    PROVIDER_REGISTRY[_alias] = target
except Exception:
  pass
