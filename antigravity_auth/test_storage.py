import json
import os
import tempfile
import unittest
from pathlib import Path

from antigravity_auth.storage import (
    get_hermes_home,
    get_auth_json_path,
    get_accounts_json_path,
    load_accounts,
    save_accounts,
    sync_token_to_auth_json,
    get_active_token_from_auth_json,
)


class TestStorage(unittest.TestCase):
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

    def test_get_hermes_home_creates_dir(self):
        home_path = get_hermes_home()
        self.assertTrue(home_path.exists())
        self.assertTrue(home_path.is_dir())
        self.assertEqual(home_path, Path(self.temp_dir.name).resolve())

    def test_paths(self):
        auth_path = get_auth_json_path()
        accounts_path = get_accounts_json_path()
        self.assertEqual(auth_path.name, "auth.json")
        self.assertEqual(accounts_path.name, "antigravity-accounts.json")
        self.assertEqual(auth_path.parent, get_hermes_home())

    def test_load_accounts_default(self):
        data = load_accounts()
        self.assertEqual(data["version"], 4)
        self.assertEqual(data["accounts"], [])
        self.assertEqual(data["activeIndex"], 0)
        self.assertEqual(data["activeIndexByFamily"], {"claude": 0, "gemini": 0})

    def test_save_and_load_accounts(self):
        test_data = {
            "version": 4,
            "accounts": [
                {
                    "email": "test@example.com",
                    "refreshToken": "refresh_123",
                    "projectId": "project_abc",
                }
            ],
            "activeIndex": 0,
            "activeIndexByFamily": {"claude": 0, "gemini": 0},
        }
        save_accounts(test_data)
        loaded = load_accounts()
        self.assertEqual(loaded["version"], 4)
        self.assertEqual(len(loaded["accounts"]), 1)
        self.assertEqual(loaded["accounts"][0]["email"], "test@example.com")
        self.assertEqual(loaded["accounts"][0]["refreshToken"], "refresh_123")

    def test_sync_token_to_auth_json_new_and_existing(self):
        sync_token_to_auth_json(
            access_token="acc_111",
            refresh_token="ref_222",
            project_id="proj_333",
            email="user@example.com",
            set_active=True,
        )

        active = get_active_token_from_auth_json()
        self.assertEqual(active["access_token"], "acc_111")
        self.assertEqual(active["refresh_token"], "ref_222")
        self.assertEqual(active["project_id"], "proj_333")

        sync_token_to_auth_json(
            access_token="acc_updated",
            refresh_token="ref_updated",
            project_id="proj_updated",
            email="user@example.com",
            set_active=False,
        )

        active_updated = get_active_token_from_auth_json()
        self.assertEqual(active_updated["access_token"], "acc_updated")
        self.assertEqual(active_updated["refresh_token"], "ref_updated")
        self.assertEqual(active_updated["project_id"], "proj_updated")

    def test_sync_token_to_auth_json_sets_canonical_runtime_active_provider(self):
        sync_token_to_auth_json(
            access_token="acc_111",
            refresh_token="ref_222|proj_333",
            project_id="proj_333",
            email="user@example.com",
            set_active=True,
        )

        with open(get_auth_json_path(), "r", encoding="utf-8") as f:
            data = json.load(f)

        self.assertEqual(data["active_provider"], "google-gemini-cli")
        self.assertIn("antigravity", data["providers"])
        self.assertIn("google-gemini-cli", data["providers"])
        self.assertEqual(
            data["providers"]["google-gemini-cli"],
            data["providers"]["antigravity"],
        )


if __name__ == "__main__":
    unittest.main()
