"""Google Antigravity CLI file-plugin wrapper."""

import sys
from pathlib import Path

try:
  from antigravity_auth.plugin_contract import load_cli_register
except Exception as exc:
  raise RuntimeError(
    "Hermes Antigravity file-plugin wrapper failed to load.\n"
    f"Wrapper: {Path(__file__).expanduser()}\n"
    f"Python: {Path(sys.executable).expanduser()}\n"
    "Cause: antigravity_auth.plugin_contract could not be imported.\n\n"
    "Fix: run `hermes-antigravity-install` from the hermes-antigravity-auth "
    "checkout so the package is installed into Hermes' Python."
  ) from exc

register = load_cli_register(__file__)
