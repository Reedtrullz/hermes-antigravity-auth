import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from antigravity_auth.credentials import resolve_oauth_credentials, write_oauth_credentials


class TestCredentials(unittest.TestCase):
  def test_env_values_win(self):
    with tempfile.NamedTemporaryFile("w", encoding="utf-8") as creds_file:
      json.dump({"client_id": "file-id", "client_secret": "file-secret"}, creds_file)
      creds_file.flush()

      with patch.dict("os.environ", {
        "ANTIGRAVITY_CLIENT_ID": "env-id",
        "ANTIGRAVITY_CLIENT_SECRET": "env-secret",
        "HERMES_ANTIGRAVITY_CREDENTIALS_FILE": creds_file.name,
      }, clear=True):
        self.assertEqual(resolve_oauth_credentials(), ("env-id", "env-secret"))

  def test_file_values_load(self):
    with tempfile.TemporaryDirectory() as tmpdir:
      creds_file = Path(tmpdir) / "antigravity-credentials.json"
      creds_file.write_text(json.dumps({
        "client_id": "file-id",
        "client_secret": "file-secret",
      }), encoding="utf-8")

      with patch.dict("os.environ", {
        "HERMES_HOME": tmpdir,
      }, clear=True):
        self.assertEqual(resolve_oauth_credentials(), ("file-id", "file-secret"))

  def test_env_non_exhaustive_falls_through_to_file(self):
    """When only one env var is set, env source is skipped — file wins."""
    with tempfile.TemporaryDirectory() as tmpdir:
      creds_file = Path(tmpdir) / "antigravity-credentials.json"
      creds_file.write_text(json.dumps({
        "client_id": "file-id",
        "client_secret": "file-secret",
      }), encoding="utf-8")

      with patch.dict("os.environ", {
        "ANTIGRAVITY_CLIENT_ID": "env-id",  # only one set
        "HERMES_HOME": tmpdir,
      }, clear=True):
        self.assertEqual(resolve_oauth_credentials(), ("file-id", "file-secret"))

  def test_external_file_supports_antigravity_json_keys(self):
    with tempfile.TemporaryDirectory() as tmpdir:
      creds_file = Path(tmpdir) / "antigravity-credentials.json"
      creds_file.write_text(json.dumps({
        "ANTIGRAVITY_CLIENT_ID": "file-id",
        "ANTIGRAVITY_CLIENT_SECRET": "file-secret",
      }), encoding="utf-8")

      with patch.dict("os.environ", {
        "HERMES_HOME": tmpdir,
      }, clear=True):
        self.assertEqual(resolve_oauth_credentials(), ("file-id", "file-secret"))

  def test_malformed_credentials_file_returns_empty(self):
    with tempfile.TemporaryDirectory() as tmpdir:
      creds_file = Path(tmpdir) / "antigravity-credentials.json"
      creds_file.write_text("{not valid json", encoding="utf-8")

      with patch.dict("os.environ", {
        "HERMES_HOME": tmpdir,
      }, clear=True):
        self.assertEqual(resolve_oauth_credentials(), ("", ""))

  def test_non_dict_credentials_file_returns_empty(self):
    with tempfile.TemporaryDirectory() as tmpdir:
      creds_file = Path(tmpdir) / "antigravity-credentials.json"
      creds_file.write_text(json.dumps(["client_id", "client_secret"]), encoding="utf-8")

      with patch.dict("os.environ", {
        "HERMES_HOME": tmpdir,
      }, clear=True):
        self.assertEqual(resolve_oauth_credentials(), ("", ""))

  def test_arbitrary_credentials_file_env_is_ignored(self):
    with tempfile.NamedTemporaryFile("w", encoding="utf-8") as creds_file, tempfile.TemporaryDirectory() as tmpdir:
      json.dump({"client_id": "file-id", "client_secret": "file-secret"}, creds_file)
      creds_file.flush()

      with patch.dict("os.environ", {
        "HERMES_HOME": tmpdir,
        "HERMES_ANTIGRAVITY_CREDENTIALS_FILE": creds_file.name,
      }, clear=True):
        self.assertEqual(resolve_oauth_credentials(), ("", ""))

  def test_missing_both_env_and_file_returns_empty(self):
    with tempfile.TemporaryDirectory() as tmpdir:
      with patch.dict("os.environ", {"HERMES_HOME": tmpdir}, clear=True):
        self.assertEqual(resolve_oauth_credentials(), ("", ""))

  def test_write_oauth_credentials_uses_private_permissions(self):
    with tempfile.TemporaryDirectory() as tmpdir:
      path = Path(tmpdir) / "nested" / "antigravity-credentials.json"
      saved = write_oauth_credentials("client-id", "client-secret", path=path)

      self.assertEqual(saved, path)
      data = json.loads(path.read_text(encoding="utf-8"))
      self.assertEqual(data["client_id"], "client-id")
      self.assertEqual(data["client_secret"], "client-secret")
      self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
      self.assertEqual(stat.S_IMODE(os.stat(path.parent).st_mode), 0o700)

  def test_write_oauth_credentials_cleans_private_temp_file_on_replace_failure(self):
    with tempfile.TemporaryDirectory() as tmpdir:
      path = Path(tmpdir) / "nested" / "antigravity-credentials.json"

      with patch("antigravity_auth.credentials.os.replace", side_effect=RuntimeError("replace failed")):
        with self.assertRaises(RuntimeError):
          write_oauth_credentials("client-id", "client-secret", path=path)

      self.assertFalse(path.exists())
      self.assertEqual(list(path.parent.glob("antigravity-credentials.json.*.tmp")), [])
