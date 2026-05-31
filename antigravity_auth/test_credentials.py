import json
import tempfile
import unittest
from unittest.mock import patch

from antigravity_auth.credentials import resolve_oauth_credentials


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

  def test_file_takes_precedence_over_bundled(self):
    with tempfile.NamedTemporaryFile("w", encoding="utf-8") as creds_file:
      json.dump({"client_id": "file-id", "client_secret": "file-secret"}, creds_file)
      creds_file.flush()

      with patch.dict("os.environ", {
        "HERMES_ANTIGRAVITY_CREDENTIALS_FILE": creds_file.name,
      }, clear=True):
        self.assertEqual(resolve_oauth_credentials(), ("file-id", "file-secret"))

  def test_env_non_exhaustive_falls_through_to_file(self):
    """When only one env var is set, env source is skipped — file wins."""
    with tempfile.NamedTemporaryFile("w", encoding="utf-8") as creds_file:
      json.dump({"client_id": "file-id", "client_secret": "file-secret"}, creds_file)
      creds_file.flush()

      with patch.dict("os.environ", {
        "ANTIGRAVITY_CLIENT_ID": "env-id",  # only one set
        "HERMES_ANTIGRAVITY_CREDENTIALS_FILE": creds_file.name,
      }, clear=True):
        self.assertEqual(resolve_oauth_credentials(), ("file-id", "file-secret"))

  def test_external_file_supports_antigravity_json_keys(self):
    with tempfile.NamedTemporaryFile("w", encoding="utf-8") as creds_file:
      json.dump({
        "ANTIGRAVITY_CLIENT_ID": "file-id",
        "ANTIGRAVITY_CLIENT_SECRET": "file-secret",
      }, creds_file)
      creds_file.flush()

      with patch.dict("os.environ", {
        "HERMES_ANTIGRAVITY_CREDENTIALS_FILE": creds_file.name,
      }, clear=True):
        self.assertEqual(resolve_oauth_credentials(), ("file-id", "file-secret"))

  def test_malformed_credentials_file_falls_to_bundled(self):
    with tempfile.NamedTemporaryFile("w", encoding="utf-8") as creds_file:
      creds_file.write("{not valid json")
      creds_file.flush()

      with patch.dict("os.environ", {
        "HERMES_ANTIGRAVITY_CREDENTIALS_FILE": creds_file.name,
      }, clear=True):
        cid, csec = resolve_oauth_credentials()
        self.assertTrue(cid)
        self.assertTrue(csec)

  def test_non_dict_credentials_file_falls_to_bundled(self):
    with tempfile.NamedTemporaryFile("w", encoding="utf-8") as creds_file:
      json.dump(["client_id", "client_secret"], creds_file)
      creds_file.flush()

      with patch.dict("os.environ", {
        "HERMES_ANTIGRAVITY_CREDENTIALS_FILE": creds_file.name,
      }, clear=True):
        cid, csec = resolve_oauth_credentials()
        self.assertTrue(cid)
        self.assertTrue(csec)

  def test_missing_both_env_and_file_returns_bundled(self):
    with tempfile.TemporaryDirectory() as tmpdir:
      with patch.dict("os.environ", {"HERMES_HOME": tmpdir}, clear=True):
        cid, csec = resolve_oauth_credentials()
        self.assertTrue(cid)
        self.assertTrue(csec)
