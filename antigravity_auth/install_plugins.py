"""Install Hermes plugin wrappers for hermes-antigravity-auth."""

from __future__ import annotations

from pathlib import Path

from .storage import get_hermes_home


CLI_INIT = '''"""Google Antigravity CLI plugin."""

from antigravity_auth.hermes_plugin import register
'''

CLI_YAML = """name: antigravity-cli
kind: standalone
version: 1.6.0
description: Google Antigravity CLI utilities - login, quotas, account management
author: NoeFabris & Reedtrullz
"""

PROVIDER_INIT = '''"""Google Antigravity provider aliases for Hermes' Cloud Code runtime."""

from antigravity_auth.hermes_provider_plugin import *  # noqa: F401,F403
'''

PROVIDER_YAML = """name: antigravity
kind: model-provider
version: 1.6.0
description: Google Antigravity OAuth via Hermes Cloud Code transport
author: NoeFabris & Reedtrullz
"""


def _write_file(path: Path, content: str) -> None:
  path.parent.mkdir(parents=True, exist_ok=True)
  path.write_text(content, encoding="utf-8")


def install_plugins(home: Path | None = None) -> list[Path]:
  hermes_home = home or get_hermes_home()
  cli_dir = hermes_home / "plugins" / "antigravity-cli"
  provider_dir = hermes_home / "plugins" / "model-providers" / "antigravity"

  _write_file(cli_dir / "__init__.py", CLI_INIT)
  _write_file(cli_dir / "plugin.yaml", CLI_YAML)
  _write_file(provider_dir / "__init__.py", PROVIDER_INIT)
  _write_file(provider_dir / "plugin.yaml", PROVIDER_YAML)

  return [cli_dir, provider_dir]


def main() -> None:
  paths = install_plugins()
  for path in paths:
    print(f"Installed {path}")


if __name__ == "__main__":
  main()
