"""Tests for package build release-safety checks."""

from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from antigravity_auth.packaging_guard import assert_no_local_credentials_module


def _repo_root() -> Path:
  return Path(__file__).resolve().parents[1]


def _copy_minimal_setup_tree(tmp_path: Path) -> Path:
  root = _repo_root()
  shutil.copy2(root / "setup.py", tmp_path / "setup.py")
  package_dir = tmp_path / "antigravity_auth"
  package_dir.mkdir()
  shutil.copy2(root / "antigravity_auth" / "packaging_guard.py", package_dir / "packaging_guard.py")
  (package_dir / "__init__.py").write_text("", encoding="utf-8")
  return tmp_path


def _run_setup(tmp_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
  return subprocess.run(
    [sys.executable, "setup.py", *args],
    cwd=tmp_path,
    text=True,
    capture_output=True,
    check=False,
  )


def test_allows_tree_without_local_credentials_module(tmp_path):
  package_dir = tmp_path / "antigravity_auth"
  package_dir.mkdir()

  assert_no_local_credentials_module(tmp_path)


def test_rejects_tree_with_local_credentials_module(tmp_path):
  package_dir = tmp_path / "antigravity_auth"
  package_dir.mkdir()
  (package_dir / "_credentials.py").write_text("DUMMY = 'not-a-secret'\n")

  with pytest.raises(RuntimeError, match="Refusing to build"):
    assert_no_local_credentials_module(tmp_path)


def test_rejects_tree_with_local_credentials_bytecode(tmp_path):
  package_dir = tmp_path / "antigravity_auth"
  pycache_dir = package_dir / "__pycache__"
  pycache_dir.mkdir(parents=True)
  (pycache_dir / "_credentials.cpython-311.pyc").write_bytes(b"dummy-bytecode")

  with pytest.raises(RuntimeError, match="_credentials\\*\\.pyc"):
    assert_no_local_credentials_module(tmp_path)


@pytest.mark.parametrize("command", ["build_py", "sdist"])
def test_direct_setup_build_commands_refuse_unknown_metadata(tmp_path, command):
  checkout = _copy_minimal_setup_tree(tmp_path)

  result = _run_setup(checkout, command)

  output = result.stdout + result.stderr
  assert result.returncode != 0
  assert f"Refusing legacy setup.py {command}" in output
  assert "UNKNOWN-0.0.0" in output
