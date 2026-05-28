"""Tests for package build release-safety checks."""

import pytest

from antigravity_auth.packaging_guard import assert_no_local_credentials_module


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
