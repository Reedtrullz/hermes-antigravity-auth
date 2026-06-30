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


_UNKNOWN_PROJECT_NAMES = {"", "UNKNOWN"}
_UNKNOWN_PROJECT_VERSIONS = {"", "0.0.0"}


def _assert_project_metadata_loaded(distribution, command_name: str) -> None:
  """Reject legacy setup.py builds before they emit UNKNOWN-0.0.0 artifacts."""
  name = distribution.get_name()
  version = distribution.get_version()
  if name not in _UNKNOWN_PROJECT_NAMES and version not in _UNKNOWN_PROJECT_VERSIONS:
    return
  raise RuntimeError(
    f"Refusing legacy setup.py {command_name}: project metadata resolved to "
    f"{name}-{version}, which is an UNKNOWN-0.0.0-style release trap. "
    "Use the PEP 517 build path instead, for example "
    "`python -m build --sdist --wheel` from a clean source archive."
  )


def _is_colocated_test_module(module_name: str) -> bool:
  return module_name.startswith("test_")


class build_py(_build_py):
  """Build Python modules after checking for local credentials."""

  def find_package_modules(self, package, package_dir):
    modules = super().find_package_modules(package, package_dir)
    return [
      (pkg, module, module_file)
      for pkg, module, module_file in modules
      if not _is_colocated_test_module(module)
    ]

  def run(self) -> None:
    _assert_project_metadata_loaded(self.distribution, "build_py")
    assert_no_local_credentials_module(Path(__file__).parent)
    super().run()


class sdist(_sdist):
  """Build source distributions after checking for local credentials."""

  def run(self) -> None:
    _assert_project_metadata_loaded(self.distribution, "sdist")
    assert_no_local_credentials_module(Path(__file__).parent)
    super().run()


setup(cmdclass={"build_py": build_py, "sdist": sdist})
