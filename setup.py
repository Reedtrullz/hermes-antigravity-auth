"""Setuptools build hooks for release-safety checks."""
from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

from setuptools import setup
from setuptools.command.build_py import build_py as _build_py
from setuptools.command.sdist import sdist as _sdist


def _load_packaging_guard() -> ModuleType:
  guard_path = Path(__file__).parent / "antigravity_auth" / "packaging_guard.py"
  spec = importlib.util.spec_from_file_location("_antigravity_auth_packaging_guard", guard_path)
  if spec is None or spec.loader is None:
    raise RuntimeError("Unable to load antigravity_auth/packaging_guard.py")
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)
  return module


assert_no_local_credentials_module = _load_packaging_guard().assert_no_local_credentials_module


class build_py(_build_py):
  """Build Python modules after checking for local credentials."""

  def run(self) -> None:
    assert_no_local_credentials_module(Path(__file__).parent)
    super().run()


class sdist(_sdist):
  """Build source distributions after checking for local credentials."""

  def run(self) -> None:
    assert_no_local_credentials_module(Path(__file__).parent)
    super().run()


setup(cmdclass={"build_py": build_py, "sdist": sdist})
