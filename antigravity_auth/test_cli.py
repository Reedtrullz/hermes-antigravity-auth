import unittest
import tempfile
import os
import sys
from unittest.mock import patch, MagicMock
from pathlib import Path

from .storage import get_hermes_home
from .cli import check_quotas_and_verify, delete_account, interactive_accounts_menu, run_login_flow
from . import cli as cli_module

class TestCli(unittest.TestCase):
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

    def test_run_login_flow_manual(self):
        with patch.object(cli_module, "exchange_antigravity") as mock_exchange:
            mock_exchange.return_value = {
                "type": "success",
                "email": "test@example.com",
                "refresh": "refresh_abc|project_123",
                "access": "access_xyz",
                "expires": 9999999999,
                "projectId": "project_123"
            }

            with patch("builtins.input", return_value="http://localhost:51121/?code=***&state=state_abc"):
                success = run_login_flow(project_id="project_123", no_browser=True)
                self.assertTrue(success)

    def test_run_login_flow_manual_code_only_uses_returned_state(self):
        auth_data = {
            "url": "https://auth",
            "verifier": "v",
            "state": "encoded-state",
            "projectId": "project_123",
            "project_id": "project_123",
        }
        with patch.object(cli_module, "authorize_antigravity", return_value=auth_data), \
             patch("builtins.input", return_value="manual-code"), \
             patch.object(cli_module, "exchange_antigravity") as mock_exchange, \
             patch.object(cli_module, "sync_token_to_all_auth_stores"):
            mock_exchange.return_value = {
                "type": "success",
                "email": "test@example.com",
                "refresh": "refresh_abc|project_123",
                "access": "access_xyz",
                "expires": 9999999999,
                "projectId": "project_123",
            }
            success = run_login_flow(project_id="project_123", no_browser=True)
        self.assertTrue(success)
        mock_exchange.assert_called_once_with("manual-code", "encoded-state")

    def test_delete_account(self):
        from .storage import load_accounts, save_accounts
        accounts_data = load_accounts()
        accounts_data["accounts"] = [
            {"email": "to_delete@example.com", "refreshToken": "ref1", "projectId": "p1"},
            {"email": "keep@example.com", "refreshToken": "ref2", "projectId": "p2"}
        ]
        save_accounts(accounts_data)

        with patch("antigravity_auth.token.refresh_access_token", return_value={
            "access": "access-keep",
            "refresh": "ref2|p2",
            "expires": 123,
        }), patch("antigravity_auth.cli.sync_token_to_all_auth_stores"):
            self.assertTrue(delete_account("to_delete@example.com"))
        loaded = load_accounts()
        self.assertEqual(len(loaded["accounts"]), 1)
        self.assertEqual(loaded["accounts"][0]["email"], "keep@example.com")

    def test_delete_active_account_syncs_next_account(self):
        from .storage import load_accounts, save_accounts
        accounts_data = load_accounts()
        accounts_data["accounts"] = [
            {"email": "to_delete@example.com", "refreshToken": "raw-delete", "projectId": "proj-delete"},
            {
                "email": "keep@example.com",
                "refreshToken": "raw-keep",
                "projectId": "proj-keep",
                "managedProjectId": "managed-keep",
            },
        ]
        accounts_data["activeIndex"] = 0
        accounts_data["activeIndexByFamily"] = {"claude": 0, "gemini": 0}
        save_accounts(accounts_data)

        refresh_calls = []

        def fake_refresh(auth, **kwargs):
            refresh_calls.append(auth)
            return {
                "access": "access-keep",
                "refresh": "rotated-keep|proj-keep|managed-keep",
                "expires": 456,
            }

        with patch("antigravity_auth.token.refresh_access_token", side_effect=fake_refresh), \
             patch("antigravity_auth.cli.sync_token_to_all_auth_stores") as mock_sync:
            self.assertTrue(delete_account("0"))

        loaded = load_accounts()
        self.assertEqual(len(loaded["accounts"]), 1)
        self.assertEqual(loaded["accounts"][0]["email"], "keep@example.com")
        self.assertEqual(refresh_calls, [{
            "refresh": "raw-keep|proj-keep|managed-keep",
            "email": "keep@example.com",
        }])
        mock_sync.assert_called_once_with(
            access_token="access-keep",
            refresh_token="rotated-keep|proj-keep|managed-keep",
            project_id="proj-keep",
            email="keep@example.com",
            expires_ms=456,
            set_active=True,
        )

    def test_delete_active_account_syncs_next_account_when_refresh_fails(self):
        from .storage import load_accounts, save_accounts
        accounts_data = load_accounts()
        accounts_data["accounts"] = [
            {"email": "to_delete@example.com", "refreshToken": "raw-delete", "projectId": "proj-delete"},
            {
                "email": "keep@example.com",
                "refreshToken": "raw-keep",
                "projectId": "proj-keep",
                "managedProjectId": "managed-keep",
            },
        ]
        accounts_data["activeIndex"] = 0
        accounts_data["activeIndexByFamily"] = {"claude": 0, "gemini": 0}
        save_accounts(accounts_data)

        with patch("antigravity_auth.token.refresh_access_token", side_effect=RuntimeError("offline")), \
             patch("antigravity_auth.cli.sync_token_to_all_auth_stores") as mock_sync:
            self.assertTrue(delete_account("0"))

        loaded = load_accounts()
        self.assertEqual(len(loaded["accounts"]), 1)
        self.assertEqual(loaded["accounts"][0]["email"], "keep@example.com")
        mock_sync.assert_called_once_with(
            access_token="",
            refresh_token="raw-keep|proj-keep|managed-keep",
            project_id="proj-keep",
            email="keep@example.com",
            expires_ms=None,
            set_active=True,
        )

    def test_delete_before_active_account_updates_family_indices(self):
        from .storage import load_accounts, save_accounts
        accounts_data = load_accounts()
        accounts_data["accounts"] = [
            {"email": "delete@example.com", "refreshToken": "raw-delete", "projectId": "proj-delete"},
            {"email": "middle@example.com", "refreshToken": "raw-middle", "projectId": "proj-middle"},
            {
                "email": "keep-active@example.com",
                "refreshToken": "raw-active",
                "projectId": "proj-active",
                "managedProjectId": "managed-active",
            },
        ]
        accounts_data["activeIndex"] = 2
        accounts_data["activeIndexByFamily"] = {"claude": 2, "gemini": 2}
        save_accounts(accounts_data)

        refresh_calls = []

        def fake_refresh(auth, **kwargs):
            refresh_calls.append(auth)
            return {
                "access": "access-active",
                "refresh": "rotated-active|proj-active|managed-active",
                "expires": 789,
            }

        with patch("antigravity_auth.token.refresh_access_token", side_effect=fake_refresh), \
             patch("antigravity_auth.cli.sync_token_to_all_auth_stores") as mock_sync:
            self.assertTrue(delete_account("0"))

        loaded = load_accounts()
        self.assertEqual([acc["email"] for acc in loaded["accounts"]], [
            "middle@example.com",
            "keep-active@example.com",
        ])
        self.assertEqual(loaded["activeIndex"], 1)
        self.assertEqual(loaded["activeIndexByFamily"], {"claude": 1, "gemini": 1})
        self.assertEqual(refresh_calls, [{
            "refresh": "raw-active|proj-active|managed-active",
            "email": "keep-active@example.com",
        }])
        mock_sync.assert_called_once_with(
            access_token="access-active",
            refresh_token="rotated-active|proj-active|managed-active",
            project_id="proj-active",
            email="keep-active@example.com",
            expires_ms=789,
            set_active=True,
        )

    def test_delete_last_account_clears_runtime_credentials(self):
        from .storage import load_accounts, save_accounts
        accounts_data = load_accounts()
        accounts_data["accounts"] = [{
            "email": "last@example.com",
            "refreshToken": "raw-last",
            "projectId": "proj-last",
        }]
        accounts_data["activeIndex"] = 0
        save_accounts(accounts_data)

        with patch("antigravity_auth.cli.sync_token_to_all_auth_stores") as mock_sync:
            self.assertTrue(delete_account("last@example.com"))

        loaded = load_accounts()
        self.assertEqual(loaded["accounts"], [])
        mock_sync.assert_called_once()
        args, kwargs = mock_sync.call_args
        if args:
            self.assertEqual(args[:2], ("", ""))
        else:
            self.assertEqual(kwargs.get("access_token"), "")
            self.assertEqual(kwargs.get("refresh_token"), "")
        self.assertEqual(kwargs.get("project_id"), "")
        self.assertIsNone(kwargs.get("email"))
        self.assertFalse(kwargs.get("set_active"))

    def test_check_quotas_refreshes_with_packed_project_id(self):
        from .storage import save_accounts
        save_accounts({
            "version": 4,
            "accounts": [{
                "email": "user@example.com",
                "refreshToken": "raw-refresh",
                "projectId": "proj-1",
            }],
            "activeIndex": 0,
            "activeIndexByFamily": {"claude": 0, "gemini": 0},
        })
        calls = []

        def fake_refresh(auth, **kwargs):
            calls.append(auth["refresh"])
            return {"access": "access", "refresh": "rotated|proj-1", "expires": 123}

        with patch("antigravity_auth.token.refresh_access_token", side_effect=fake_refresh), \
             patch("antigravity_auth.accounts.quota.fetch_quota_from_api", return_value=[]), \
             patch("antigravity_auth.verification.verify_account_access"):
            check_quotas_and_verify()

        self.assertEqual(calls, ["raw-refresh|proj-1"])

    def test_account_switch_syncs_rotated_packed_refresh_with_managed_project_id(self):
        from .storage import save_accounts
        save_accounts({
            "version": 4,
            "accounts": [{
                "email": "user@example.com",
                "refreshToken": "raw-refresh",
                "projectId": "proj-1",
                "managedProjectId": "managed-1",
            }],
            "activeIndex": 0,
            "activeIndexByFamily": {"claude": 0, "gemini": 0},
        })
        refresh_calls = []
        sync_calls = []

        def fake_refresh(auth, **kwargs):
            refresh_calls.append(auth["refresh"])
            return {"access": "access", "refresh": "rotated|proj-1|managed-1", "expires": 123}

        def fake_sync(**kwargs):
            sync_calls.append(kwargs)
            return True

        with patch("builtins.input", side_effect=["3", "0", "6"]), \
             patch("antigravity_auth.token.refresh_access_token", side_effect=fake_refresh), \
             patch("antigravity_auth.cli.sync_token_to_all_auth_stores", side_effect=fake_sync):
            interactive_accounts_menu()

        self.assertEqual(refresh_calls, ["raw-refresh|proj-1|managed-1"])
        self.assertEqual(sync_calls[0]["access_token"], "access")
        self.assertEqual(sync_calls[0]["refresh_token"], "rotated|proj-1|managed-1")
        self.assertEqual(sync_calls[0]["project_id"], "proj-1")
        self.assertEqual(sync_calls[0]["email"], "user@example.com")
        self.assertEqual(sync_calls[0]["expires_ms"], 123)
        self.assertTrue(sync_calls[0]["set_active"])

if __name__ == "__main__":
    unittest.main()
