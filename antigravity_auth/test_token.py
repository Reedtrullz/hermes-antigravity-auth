import os
import tempfile
import unittest
import json
import time
import gzip
from urllib.error import HTTPError
from unittest.mock import patch, MagicMock

from .token import (
    parse_refresh_parts,
    format_refresh_parts,
    is_access_token_expired,
    refresh_access_token,
    AntigravityTokenRefreshError,
)
from .storage import (
    save_accounts,
    load_accounts,
    sync_token_to_auth_json,
    get_active_token_from_auth_json,
)


class TestToken(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_hermes_home = os.environ.get("HERMES_HOME")
        os.environ["HERMES_HOME"] = self.temp_dir.name

    def tearDown(self):
        if self.original_hermes_home is not None:
            os.environ["HERMES_HOME"] = self.original_hermes_home
        else:
            os.environ.pop("HERMES_HOME", None)
        self.temp_dir.cleanup()

    def _mock_invalid_grant(self, mock_urlopen):
        mock_response = MagicMock()
        mock_response.status = 400
        mock_response.reason = "Bad Request"
        mock_response.read.return_value = json.dumps({
            "error": "invalid_grant",
            "error_description": "Token has been expired or revoked."
        }).encode("utf-8")

        mock_http_error = HTTPError(
            url="https://oauth2.googleapis.com/token",
            code=400,
            msg="Bad Request",
            hdrs={},
            fp=None
        )
        mock_http_error.read = MagicMock(return_value=mock_response.read.return_value)
        mock_urlopen.side_effect = mock_http_error

    def test_parse_refresh_parts(self):
        res = parse_refresh_parts("refresh_123|project_abc|managed_xyz")
        self.assertEqual(res["refreshToken"], "refresh_123")
        self.assertEqual(res["projectId"], "project_abc")
        self.assertEqual(res["managedProjectId"], "managed_xyz")

        res_empty = parse_refresh_parts("")
        self.assertEqual(res_empty["refreshToken"], "")
        self.assertIsNone(res_empty["projectId"])
        self.assertIsNone(res_empty["managedProjectId"])

        res_partial = parse_refresh_parts("refresh_123")
        self.assertEqual(res_partial["refreshToken"], "refresh_123")
        self.assertIsNone(res_partial["projectId"])
        self.assertIsNone(res_partial["managedProjectId"])

        res_middle = parse_refresh_parts("refresh_123||managed_xyz")
        self.assertEqual(res_middle["refreshToken"], "refresh_123")
        self.assertIsNone(res_middle["projectId"])
        self.assertEqual(res_middle["managedProjectId"], "managed_xyz")

    def test_format_refresh_parts(self):
        parts = {
            "refreshToken": "refresh_123",
            "projectId": "project_abc",
            "managedProjectId": "managed_xyz",
        }
        self.assertEqual(format_refresh_parts(parts), "refresh_123|project_abc|managed_xyz")

        parts_no_managed = {
            "refreshToken": "refresh_123",
            "projectId": "project_abc",
        }
        self.assertEqual(format_refresh_parts(parts_no_managed), "refresh_123|project_abc")

        parts_no_project = {
            "refreshToken": "refresh_123",
            "managedProjectId": "managed_xyz",
        }
        self.assertEqual(format_refresh_parts(parts_no_project), "refresh_123||managed_xyz")

    def test_is_access_token_expired(self):
        self.assertTrue(is_access_token_expired({}))
        self.assertTrue(is_access_token_expired({"access": ""}))
        
        current_time_ms = int(time.time() * 1000)
        
        auth_expired = {"access": "token", "expires": current_time_ms}
        self.assertTrue(is_access_token_expired(auth_expired))

        auth_close = {"access": "token", "expires": current_time_ms + 30000}
        self.assertTrue(is_access_token_expired(auth_close))

        auth_valid = {"access": "token", "expires": current_time_ms + 120000}
        self.assertFalse(is_access_token_expired(auth_valid))

    @patch("urllib.request.urlopen")
    def test_refresh_access_token_does_not_set_active_provider_by_default(self, mock_urlopen):
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.read.return_value = json.dumps({
            "access_token": "new_access",
            "expires_in": 3600,
            "refresh_token": "new_refresh",
        }).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_response

        sync_token_to_auth_json("old_access", "old_refresh|proj", "proj", "old@example.com")

        updated = refresh_access_token({
            "refresh": "old_refresh|proj",
            "access": "old_access",
            "expires": 0,
            "email": "user@example.com",
        })

        self.assertEqual(updated["access"], "new_access")
        self.assertEqual(updated["refresh"], "new_refresh|proj")
        active = get_active_token_from_auth_json()
        self.assertEqual(active["access_token"], "old_access")
        self.assertEqual(active["refresh_token"], "old_refresh|proj")

    @patch("urllib.request.urlopen")
    def test_refresh_access_token_success(self, mock_urlopen):
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.read.return_value = json.dumps({
            "access_token": "new_access_token_abc",
            "expires_in": 3600,
            "refresh_token": "new_rotated_refresh_token_xyz"
        }).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_response

        accounts_data = {
            "version": 4,
            "accounts": [
                {
                    "email": "test@example.com",
                    "refreshToken": "old_refresh_123",
                    "projectId": "proj_abc",
                }
            ]
        }
        save_accounts(accounts_data)

        auth = {
            "refresh": "old_refresh_123|proj_abc",
            "access": "old_access",
            "expires": 0,
            "email": "test@example.com",
        }

        updated_auth = refresh_access_token(auth, persist=True, set_active=True)
        self.assertEqual(updated_auth["access"], "new_access_token_abc")
        self.assertEqual(updated_auth["refresh"], "new_rotated_refresh_token_xyz|proj_abc")
        self.assertTrue(updated_auth["expires"] > int(time.time() * 1000))

        loaded = load_accounts()
        self.assertEqual(loaded["accounts"][0]["refreshToken"], "new_rotated_refresh_token_xyz")

        active = get_active_token_from_auth_json()
        self.assertEqual(active["access_token"], "new_access_token_abc")
        self.assertEqual(active["refresh_token"], "new_rotated_refresh_token_xyz|proj_abc")

    @patch("urllib.request.urlopen")
    def test_refresh_access_token_revoked(self, mock_urlopen):
        self._mock_invalid_grant(mock_urlopen)

        accounts_data = {
            "version": 4,
            "accounts": [
                {
                    "email": "test@example.com",
                    "refreshToken": "old_refresh_123",
                    "projectId": "proj_abc",
                }
            ]
        }
        save_accounts(accounts_data)

        sync_token_to_auth_json("old_access", "old_refresh_123|proj_abc", "proj_abc", "test@example.com")

        auth = {
            "refresh": "old_refresh_123|proj_abc",
            "access": "old_access",
            "expires": 0,
            "email": "test@example.com",
        }

        with self.assertRaises(AntigravityTokenRefreshError) as context:
            refresh_access_token(auth, persist=True, set_active=True)

        self.assertEqual(context.exception.code, "invalid_grant")
        self.assertEqual(context.exception.status, 400)

        loaded = load_accounts()
        self.assertEqual(len(loaded["accounts"]), 0)

        active = get_active_token_from_auth_json()
        self.assertEqual(active["access_token"], "")
        self.assertEqual(active["refresh_token"], "")

    @patch("urllib.request.urlopen")
    def test_invalid_grant_without_persist_does_not_mutate_accounts_or_auth(self, mock_urlopen):
        self._mock_invalid_grant(mock_urlopen)

        accounts_data = {
            "version": 4,
            "accounts": [
                {
                    "email": "test@example.com",
                    "refreshToken": "old_refresh_123",
                    "projectId": "proj_abc",
                }
            ],
            "activeIndex": 0,
            "cursor": 0,
            "activeIndexByFamily": {"claude": 0, "gemini": 0},
        }
        save_accounts(accounts_data)
        sync_token_to_auth_json("old_access", "old_refresh_123|proj_abc", "proj_abc", "test@example.com")

        auth = {
            "refresh": "old_refresh_123|proj_abc",
            "access": "old_access",
            "expires": 0,
            "email": "test@example.com",
        }

        with self.assertRaises(AntigravityTokenRefreshError) as context:
            refresh_access_token(auth)

        self.assertEqual(context.exception.code, "invalid_grant")
        self.assertEqual(load_accounts()["accounts"][0]["refreshToken"], "old_refresh_123")
        active = get_active_token_from_auth_json()
        self.assertEqual(active["access_token"], "old_access")
        self.assertEqual(active["refresh_token"], "old_refresh_123|proj_abc")

    @patch("urllib.request.urlopen")
    def test_invalid_grant_persist_rehomes_active_auth_to_remaining_account(self, mock_urlopen):
        self._mock_invalid_grant(mock_urlopen)

        accounts_data = {
            "version": 4,
            "accounts": [
                {
                    "email": "keep@example.com",
                    "refreshToken": "keep_refresh",
                    "projectId": "keep_project",
                    "managedProjectId": "keep_managed",
                },
                {
                    "email": "revoked@example.com",
                    "refreshToken": "revoked_refresh",
                    "projectId": "revoked_project",
                    "managedProjectId": "revoked_managed",
                },
            ],
            "activeIndex": 1,
            "cursor": 1,
            "activeIndexByFamily": {"claude": 1, "gemini": 1},
        }
        save_accounts(accounts_data)
        sync_token_to_auth_json(
            "revoked_access",
            "revoked_refresh|revoked_project|revoked_managed",
            "revoked_project",
            "revoked@example.com",
        )

        auth = {
            "refresh": "revoked_refresh|revoked_project|revoked_managed",
            "access": "revoked_access",
            "expires": 0,
            "email": "revoked@example.com",
        }

        with self.assertRaises(AntigravityTokenRefreshError) as context:
            refresh_access_token(auth, persist=True, set_active=True)

        self.assertEqual(context.exception.code, "invalid_grant")
        loaded = load_accounts()
        self.assertEqual(len(loaded["accounts"]), 1)
        self.assertEqual(loaded["accounts"][0]["email"], "keep@example.com")
        self.assertEqual(loaded["activeIndex"], 0)
        self.assertEqual(loaded["activeIndexByFamily"], {"claude": 0, "gemini": 0})
        self.assertEqual(loaded["cursor"], 0)

        active = get_active_token_from_auth_json()
        self.assertEqual(active["access_token"], "")
        self.assertEqual(active["refresh_token"], "keep_refresh|keep_project|keep_managed")
        self.assertEqual(active["project_id"], "keep_project")

        with open(os.path.join(self.temp_dir.name, "auth.json"), "r", encoding="utf-8") as f:
            auth_json = json.load(f)
        self.assertEqual(auth_json["providers"]["antigravity"]["email"], "keep@example.com")

    @patch("urllib.request.urlopen")
    def test_refresh_access_token_gzipped_response(self, mock_urlopen):
        # Create gzipped response body
        response_data = {
            "access_token": "gzipped_access_token",
            "expires_in": 3600,
            "refresh_token": "gzipped_refresh_token"
        }
        json_bytes = json.dumps(response_data).encode("utf-8")
        gzipped_body = gzip.compress(json_bytes)

        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.read.return_value = gzipped_body
        mock_response.headers = {"Content-Encoding": "gzip"}
        mock_urlopen.return_value.__enter__.return_value = mock_response

        accounts_data = {
            "version": 4,
            "accounts": [
                {
                    "email": "test@example.com",
                    "refreshToken": "old_refresh_123",
                    "projectId": "proj_abc",
                }
            ]
        }
        save_accounts(accounts_data)

        auth = {
            "refresh": "old_refresh_123|proj_abc",
            "access": "old_access",
            "expires": 0,
            "email": "test@example.com",
        }

        updated_auth = refresh_access_token(auth, persist=True, set_active=True)
        self.assertEqual(updated_auth["access"], "gzipped_access_token")
        self.assertEqual(updated_auth["refresh"], "gzipped_refresh_token|proj_abc")
        self.assertTrue(updated_auth["expires"] > int(time.time() * 1000))

        loaded = load_accounts()
        self.assertEqual(loaded["accounts"][0]["refreshToken"], "gzipped_refresh_token")

        active = get_active_token_from_auth_json()
        self.assertEqual(active["access_token"], "gzipped_access_token")
        self.assertEqual(active["refresh_token"], "gzipped_refresh_token|proj_abc")


if __name__ == "__main__":
    unittest.main()
