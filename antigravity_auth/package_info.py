"""Shared package metadata and canonical install commands."""

from __future__ import annotations

import shlex

from ._version import __version__

PACKAGE_NAME = "hermes-antigravity-auth"
PACKAGE_EXTRA = "yaml"
PACKAGE_SPEC = f"{PACKAGE_NAME}[{PACKAGE_EXTRA}]"
GITHUB_URL = "https://github.com/Reedtrullz/hermes-antigravity-auth.git"
GIT_PACKAGE_SPEC = f"{PACKAGE_SPEC} @ git+{GITHUB_URL}"
INSTALL_COMMAND = "hermes-antigravity-install"
CANONICAL_INSTALL_COMMAND = f'python3 -m pip install --upgrade "{PACKAGE_SPEC}" && {INSTALL_COMMAND}'


def python_install_command(python: str) -> str:
  """Return a Hermes-Python-specific package repair command."""
  return f"{shlex.quote(python)} -m pip install --upgrade {shlex.quote(PACKAGE_SPEC)}"
