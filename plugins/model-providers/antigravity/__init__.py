"""Google Antigravity provider aliases for Hermes' Cloud Code runtime."""

# This import * is intentional — hermes_provider_plugin relies on
# module-level side effects (register_provider, _patch_hermes_model_picker,
# _set_oauth_env_from_credentials). Explicit named imports would trigger
# the same side effects but add fragility. DO NOT refactor without
# understanding the side-effect-driven registration pattern.
from antigravity_auth.hermes_provider_plugin import *  # noqa: F401,F403
