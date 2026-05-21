"""Hermes provider profile for Antigravity aliases."""

from __future__ import annotations

from providers import register_provider
from providers.base import ProviderProfile


class AntigravityProfile(ProviderProfile):
  """Antigravity model names routed through Hermes' google-gemini-cli client."""


antigravity = AntigravityProfile(
  name="google-gemini-cli",
  aliases=("antigravity", "antigravity-google", "ag", "gemini-cli", "gemini-oauth"),
  display_name="Google Antigravity",
  description="Google Antigravity OAuth via Hermes' native Cloud Code transport",
  env_vars=(),
  base_url="cloudcode-pa://google",
  auth_type="oauth_external",
  default_aux_model="gemini-3-flash",
  fallback_models=(
    "claude-opus-4-6-thinking",
    "claude-sonnet-4-6",
    "gemini-3.1-pro",
    "gemini-3-pro",
    "gemini-3-flash",
    "gemini-2.5-pro",
    "gemini-2.5-flash",
    "gemini-3-pro-preview",
    "gemini-3-flash-preview",
  ),
  default_headers={},
)


register_provider(antigravity)

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
