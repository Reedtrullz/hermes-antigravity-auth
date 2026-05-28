import json
import tempfile
import unittest
from unittest.mock import patch

from antigravity_auth.credentials import resolve_oauth_credentials


class TestCredentials(unittest.TestCase):
  def test_env_values_win(self):
    with patch.dict("os.environ", {
      "ANTIGRAVITY_CLIENT_ID": "env-id",
      "ANTIGRAVITY_CLIENT_SECRET": "env-secret",
    }, clear=True):
      self.assertEqual(resolve_oauth_credentials(), ("env-id", "env-secret"))

  def test_external_file_fills_missing_env_secret(self):
    with tempfile.NamedTemporaryFile("w", encoding="utf-8") as creds_file:
      json.dump({"client_id": "file-id", "client_secret": "file-secret"}, creds_file)
      creds_file.flush()

      with patch.dict("os.environ", {
        "ANTIGRAVITY_CLIENT_ID": "env-id",
        "HERMES_ANTIGRAVITY_CREDENTIALS_FILE": creds_file.name,
      }, clear=True):
        self.assertEqual(resolve_oauth_credentials(), ("env-id", "file-secret"))

  def test_external_file_fills_missing_env_client_id(self):
    with tempfile.NamedTemporaryFile("w", encoding="utf-8") as creds_file:
      json.dump({"client_id": "file-id", "client_secret": "file-secret"}, creds_file)
      creds_file.flush()

      with patch.dict("os.environ", {
        "ANTIGRAVITY_CLIENT_SECRET": "env-secret",
        "HERMES_ANTIGRAVITY_CREDENTIALS_FILE": creds_file.name,
      }, clear=True):
        self.assertEqual(resolve_oauth_credentials(), ("file-id", "env-secret"))

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

  def test_malformed_credentials_file_returns_empty_strings(self):
    with tempfile.NamedTemporaryFile("w", encoding="utf-8") as creds_file:
      creds_file.write("{not valid json")
      creds_file.flush()

      with patch.dict("os.environ", {
        "HERMES_ANTIGRAVITY_CREDENTIALS_FILE": creds_file.name,
      }, clear=True):
        self.assertEqual(resolve_oauth_credentials(), ("", ""))

  def test_non_dict_credentials_file_returns_empty_strings(self):
    with tempfile.NamedTemporaryFile("w", encoding="utf-8") as creds_file:
      json.dump(["client_id", "client_secret"], creds_file)
      creds_file.flush()

      with patch.dict("os.environ", {
        "HERMES_ANTIGRAVITY_CREDENTIALS_FILE": creds_file.name,
      }, clear=True):
        self.assertEqual(resolve_oauth_credentials(), ("", ""))

  def test_missing_returns_empty_strings(self):
    with tempfile.TemporaryDirectory() as tmpdir:
      with patch.dict("os.environ", {"HERMES_HOME": tmpdir}, clear=True):
        self.assertEqual(resolve_oauth_credentials(), ("", ""))
