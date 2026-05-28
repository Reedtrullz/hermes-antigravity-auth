import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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

    def test_process_lock_creates_private_lock_file_and_can_reacquire(self):
        import os
        import stat
        import tempfile
        from pathlib import Path
        from antigravity_auth.storage import _process_file_lock

        with tempfile.TemporaryDirectory() as tmp:
            lock_path = Path(tmp) / "store.lock"
            with _process_file_lock(lock_path):
                self.assertTrue(lock_path.exists())
                self.assertEqual(stat.S_IMODE(os.stat(lock_path).st_mode), 0o600)
            with _process_file_lock(lock_path):
                self.assertTrue(lock_path.exists())
                self.assertEqual(stat.S_IMODE(os.stat(lock_path).st_mode), 0o600)

    def test_save_accounts_creates_private_process_lock_file(self):
        import stat

        save_accounts({"version": 4, "accounts": []})
        lock_path = get_accounts_json_path().with_suffix(".lock")
        self.assertTrue(lock_path.exists())
        self.assertEqual(stat.S_IMODE(os.stat(lock_path).st_mode), 0o600)

        save_accounts({"version": 4, "accounts": [], "activeIndex": 0})
        self.assertTrue(lock_path.exists())
        self.assertEqual(stat.S_IMODE(os.stat(lock_path).st_mode), 0o600)

    def test_save_accounts_temp_file_is_private_before_replace(self):
        observed_modes = []
        original_replace = os.replace

        def inspect_tmp_before_replace(src, dst):
            observed_modes.append(os.stat(src).st_mode & 0o777)
            return original_replace(src, dst)

        old_umask = os.umask(0)
        try:
            with patch("antigravity_auth.storage.os.replace", side_effect=inspect_tmp_before_replace):
                save_accounts({"version": 4, "accounts": []})
        finally:
            os.umask(old_umask)

        self.assertEqual(observed_modes, [0o600])

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

    def test_sync_token_to_auth_json_creates_private_lock_and_preserves_unrelated_providers(self):
        import stat

        auth_path = get_auth_json_path()
        auth_path.parent.mkdir(parents=True, exist_ok=True)
        with open(auth_path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "providers": {
                        "other": {
                            "tokens": {"access_token": "keep_access"},
                            "project_id": "keep_project",
                        }
                    },
                    "active_provider": "other",
                },
                f,
            )

        sync_token_to_auth_json(
            access_token="acc_111",
            refresh_token="ref_222",
            project_id="proj_333",
            email="user@example.com",
            set_active=False,
        )

        lock_path = auth_path.with_suffix(".lock")
        self.assertTrue(lock_path.exists())
        self.assertEqual(stat.S_IMODE(os.stat(lock_path).st_mode), 0o600)
        with open(auth_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["active_provider"], "other")
        self.assertEqual(data["providers"]["other"]["project_id"], "keep_project")
        self.assertEqual(data["providers"]["antigravity"]["project_id"], "proj_333")
        self.assertEqual(
            data["providers"]["google-gemini-cli"],
            data["providers"]["antigravity"],
        )

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

    def test_sync_token_to_auth_json_temp_file_is_private_before_replace(self):
        observed_modes = []
        original_replace = os.replace

        def inspect_tmp_before_replace(src, dst):
            observed_modes.append(os.stat(src).st_mode & 0o777)
            return original_replace(src, dst)

        old_umask = os.umask(0)
        try:
            with patch("antigravity_auth.storage.os.replace", side_effect=inspect_tmp_before_replace):
                sync_token_to_auth_json(
                    access_token="secret_access",
                    refresh_token="secret_refresh|secret_project",
                    project_id="secret_project",
                    email="user@example.com",
                )
        finally:
            os.umask(old_umask)

        self.assertEqual(observed_modes, [0o600])

    def test_sync_token_to_auth_json_clears_antigravity_active_provider_when_tokens_empty(self):
        sync_token_to_auth_json(
            access_token="acc_111",
            refresh_token="ref_222|proj_333",
            project_id="proj_333",
            email="user@example.com",
            set_active=True,
        )

        sync_token_to_auth_json(
            access_token="",
            refresh_token="",
            project_id="",
            email=None,
            set_active=False,
        )
        with open(get_auth_json_path(), "r", encoding="utf-8") as f:
            data = json.load(f)

        self.assertEqual(data["active_provider"], "")
        self.assertEqual(data["providers"]["antigravity"]["tokens"]["access_token"], "")
        self.assertEqual(data["providers"]["antigravity"]["tokens"]["refresh_token"], "")
        self.assertEqual(
            data["providers"]["google-gemini-cli"],
            data["providers"]["antigravity"],
        )

    def test_sync_token_to_all_auth_stores_reports_google_oauth_failure_independently(self):
        from antigravity_auth.auth_sync import sync_token_to_all_auth_stores

        with patch("antigravity_auth.auth_sync.sync_token_to_auth_json") as auth_json_sync, \
             patch("antigravity_auth.auth_sync.sync_token_to_google_oauth", return_value=False) as google_sync:
            result = sync_token_to_all_auth_stores(
                access_token="acc_111",
                refresh_token="ref_222|proj_333",
                project_id="proj_333",
                email="user@example.com",
                expires_ms=123,
                set_active=True,
            )

        self.assertEqual(result.auth_json, True)
        self.assertEqual(result.google_oauth, False)
        self.assertEqual(result.ok, False)
        self.assertEqual(bool(result), False)
        auth_json_sync.assert_called_once()
        google_sync.assert_called_once()

    def test_sync_token_to_all_auth_stores_reports_auth_json_failure_independently(self):
        from antigravity_auth.auth_sync import sync_token_to_all_auth_stores, sync_token_to_all_auth_stores_bool

        with patch("antigravity_auth.auth_sync.sync_token_to_auth_json", side_effect=RuntimeError("boom")), \
             patch("antigravity_auth.auth_sync.sync_token_to_google_oauth", return_value=True):
            result = sync_token_to_all_auth_stores(
                access_token="acc_111",
                refresh_token="ref_222|proj_333",
                project_id="proj_333",
                email="user@example.com",
            )

        self.assertEqual(result.auth_json, False)
        self.assertEqual(result.google_oauth, True)
        self.assertEqual(result.ok, False)
        self.assertEqual(bool(result), False)

        with patch("antigravity_auth.auth_sync.sync_token_to_auth_json", side_effect=RuntimeError("boom")), \
             patch("antigravity_auth.auth_sync.sync_token_to_google_oauth", return_value=True):
            self.assertEqual(
                sync_token_to_all_auth_stores_bool(
                    access_token="acc_111",
                    refresh_token="ref_222|proj_333",
                    project_id="proj_333",
                    email="user@example.com",
                ),
                False,
            )


if __name__ == "__main__":
    unittest.main()
