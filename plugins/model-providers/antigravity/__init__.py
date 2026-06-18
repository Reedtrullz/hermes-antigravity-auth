"""Google Antigravity provider file-plugin wrapper."""

import sys
from pathlib import Path

try:
  from antigravity_auth.plugin_contract import load_provider_namespace
except Exception as exc:
  raise RuntimeError(
    "Hermes Antigravity file-plugin wrapper failed to load.\n"
    f"Wrapper: {Path(__file__).expanduser()}\n"
    f"Python: {Path(sys.executable).expanduser()}\n"
    "Target: antigravity_auth.plugin_contract\n"
    f"Cause: {type(exc).__name__}: {exc}\n\n"
    "Fix: run `hermes-antigravity-install` from the hermes-antigravity-auth "
    "checkout so the package is installed into Hermes' Python."
  ) from exc

globals().update(load_provider_namespace(__file__))
