"""Google Antigravity provider profile."""

from providers import register_provider
from providers.base import ProviderProfile


class AntigravityProfile(ProviderProfile):
    """Google Antigravity OAuth — Claude, Gemini, GPT-OSS via Google credentials."""


antigravity = AntigravityProfile(
    name="antigravity",
    aliases=("antigravity-google", "ag"),
    display_name="Google Antigravity",
    description="Google Antigravity OAuth — Claude, Gemini, GPT-OSS via Google credentials",
    env_vars=("ANTIGRAVITY_REFRESH_TOKEN", "ANTIGRAVITY_BASE_URL"),
    base_url="https://cloudcode-pa.googleapis.com",
    auth_type="oauth_external",
    default_aux_model="gemini-3-flash",
    fallback_models=(
        "antigravity-claude-opus-4-6-thinking",
        "antigravity-claude-sonnet-4-6",
        "antigravity-gemini-3.1-pro",
        "antigravity-gemini-3-pro",
        "antigravity-gemini-3-flash",
        "gemini-2.5-pro",
        "gemini-2.5-flash",
        "gemini-3-pro-preview",
        "gemini-3-flash-preview",
    ),
    default_headers={
        "X-Goog-Api-Client": "google-cloud-sdk vscode_cloudshelleditor/0.1"
    },
)

register_provider(antigravity)

# Self-register in Hermes' PROVIDER_REGISTRY so resolve_provider() can find it.
# oauth_external providers are not auto-discovered by PROVIDER_REGISTRY's
# extension logic (which only picks up api_key providers), so we add ourselves.
try:
    from hermes_cli.auth import PROVIDER_REGISTRY, ProviderConfig
    if "antigravity" not in PROVIDER_REGISTRY:
        PROVIDER_REGISTRY["antigravity"] = ProviderConfig(
            id="antigravity",
            name="Google Antigravity",
            auth_type="oauth_external",
            inference_base_url="https://cloudcode-pa.googleapis.com",
        )
        for _alias in antigravity.aliases:
            if _alias not in PROVIDER_REGISTRY:
                PROVIDER_REGISTRY[_alias] = PROVIDER_REGISTRY["antigravity"]
except Exception:
    pass
