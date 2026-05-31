import unittest
import tempfile
import os
import sys
import threading
import urllib.error
import urllib.request
from unittest.mock import patch, MagicMock
from pathlib import Path

from .storage import get_hermes_home
from .cli import (
    check_quotas_and_verify,
    delete_account,
    interactive_accounts_menu,
    run_login_flow,
    set_account_project,
)
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
        auth_data = {
            "url": "https://auth",
            "verifier": "v",
            "state": "state_abc",
        }
        with patch.object(cli_module, "authorize_antigravity", return_value=auth_data), \
             patch.object(cli_module, "exchange_antigravity") as mock_exchange:
            mock_exchange.return_value = {
                "type": "success",
                "email": "test@example.com",
                "refresh": "refresh_abc|project_123",
                "access": "access_xyz",
                "expires": 9999999999,
                "projectId": "project_123"
            }

            with patch("builtins.input", return_value="http://localhost:51121/?code=manual-code&state=state_abc"):
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

    def test_run_login_flow_returns_false_when_auth_json_sync_fails(self):
        from antigravity_auth.auth_sync import AuthSyncResult

        auth_data = {
            "url": "https://auth",
            "verifier": "v",
            "state": "encoded-state",
        }
        with patch.object(cli_module, "authorize_antigravity", return_value=auth_data), \
             patch("builtins.input", return_value="manual-code"), \
             patch.object(cli_module, "exchange_antigravity", return_value={
                 "type": "success",
                 "email": "test@example.com",
                 "refresh": "refresh_abc|project_123",
                 "access": "access_xyz",
                 "expires": 9999999999,
                 "projectId": "project_123",
             }), \
             patch.object(
                 cli_module,
                 "sync_token_to_all_auth_stores",
                 return_value=AuthSyncResult(auth_json=False, google_oauth=True),
             ), \
             patch("builtins.print") as mock_print:
            success = run_login_flow(project_id="project_123", no_browser=True)

        self.assertFalse(success)
        printed = "\n".join(str(call.args[0]) for call in mock_print.call_args_list if call.args)
        self.assertIn("Hermes auth.json could not be updated", printed)
        self.assertNotIn("SUCCESS: Successfully authenticated", printed)

    def test_run_login_flow_rejects_manual_redirect_state_mismatch(self):
        auth_data = {
            "url": "https://auth",
            "verifier": "v",
            "state": "expected-state",
        }
        with patch.object(cli_module, "authorize_antigravity", return_value=auth_data), \
             patch("builtins.input", return_value="http://localhost:51121/?code=manual-code&state=wrong-state"), \
             patch.object(cli_module, "exchange_antigravity") as mock_exchange:
            success = run_login_flow(project_id="project_123", no_browser=True)

        self.assertFalse(success)
        mock_exchange.assert_not_called()

    def test_run_login_flow_rejects_manual_redirect_missing_state(self):
        auth_data = {
            "url": "https://auth",
            "verifier": "v",
            "state": "expected-state",
        }
        with patch.object(cli_module, "authorize_antigravity", return_value=auth_data), \
             patch("builtins.input", return_value="http://localhost:51121/?code=manual-code"), \
             patch.object(cli_module, "exchange_antigravity") as mock_exchange:
            success = run_login_flow(project_id="project_123", no_browser=True)

        self.assertFalse(success)
        mock_exchange.assert_not_called()

    def test_run_login_flow_browser_callback_waits_for_expected_state(self):
        auth_data = {
            "url": "https://auth",
            "verifier": "v",
            "state": "expected-state",
        }
        with patch.object(cli_module, "authorize_antigravity", return_value=auth_data), \
             patch.object(cli_module.webbrowser, "open", return_value=True), \
             patch.object(cli_module, "run_callback_server", return_value=("browser-code", "expected-state")) as mock_callback, \
             patch.object(cli_module, "exchange_antigravity", return_value={
                 "type": "success",
                 "email": "test@example.com",
                 "refresh": "refresh_abc|project_123",
                 "access": "access_xyz",
                 "expires": 9999999999,
                 "projectId": "project_123",
             }), \
             patch.object(cli_module, "sync_token_to_all_auth_stores"):
            success = run_login_flow(project_id="project_123", no_browser=False)

        self.assertTrue(success)
        mock_callback.assert_called_once_with(
            port=51121,
            timeout=60,
            expected_state="expected-state",
        )

    def test_callback_handler_rejects_state_mismatch_without_consuming_callback(self):
        server = cli_module.ThreadSafeHTTPServer(("127.0.0.1", 0), cli_module.OAuthCallbackHandler)
        server.expected_state = "expected-state"
        server.callback_code = None
        server.callback_state = None
        server.callback_error = None
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = server.server_address[1]
            with self.assertRaises(urllib.error.HTTPError) as context:
                urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/?code=evil-code&state=wrong-state",
                    timeout=5,
                )
            self.assertEqual(context.exception.code, 400)
            self.assertIsNone(server.callback_code)
            self.assertIsNone(server.callback_state)

            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/?code=good-code&state=expected-state",
                timeout=5,
            ) as response:
                self.assertEqual(response.status, 200)
                self.assertIn(b"Authentication Success", response.read())

            self.assertEqual(server.callback_code, "good-code")
            self.assertEqual(server.callback_state, "expected-state")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_callback_handler_rejects_error_callback_state_mismatch_without_stopping(self):
        server = cli_module.ThreadSafeHTTPServer(("127.0.0.1", 0), cli_module.OAuthCallbackHandler)
        server.expected_state = "expected-state"
        server.callback_code = None
        server.callback_state = None
        server.callback_error = None
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = server.server_address[1]
            with self.assertRaises(urllib.error.HTTPError) as context:
                urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/?error=access_denied&state=wrong-state",
                    timeout=5,
                )
            self.assertEqual(context.exception.code, 400)
            self.assertIsNone(server.callback_code)
            self.assertIsNone(server.callback_state)
            self.assertIsNone(server.callback_error)

            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/?code=good-code&state=expected-state",
                timeout=5,
            ) as response:
                self.assertEqual(response.status, 200)

            self.assertEqual(server.callback_code, "good-code")
            self.assertEqual(server.callback_state, "expected-state")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_callback_handler_records_matching_state_oauth_error_as_failure(self):
        server = cli_module.ThreadSafeHTTPServer(("127.0.0.1", 0), cli_module.OAuthCallbackHandler)
        server.expected_state = "expected-state"
        server.callback_code = None
        server.callback_state = None
        server.callback_error = None
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = server.server_address[1]
            with self.assertRaises(urllib.error.HTTPError) as context:
                urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/?error=access_denied&state=expected-state",
                    timeout=5,
                )
            self.assertEqual(context.exception.code, 400)
            self.assertIsNone(server.callback_code)
            self.assertEqual(server.callback_state, "expected-state")
            self.assertEqual(server.callback_error, "access_denied")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_run_login_flow_sets_family_indices_and_cursor_to_new_account(self):
        from .storage import load_accounts, save_accounts
        save_accounts({
            "version": 4,
            "accounts": [{
                "email": "existing@example.com",
                "refreshToken": "existing-refresh",
                "projectId": "existing-project",
            }],
            "activeIndex": 0,
            "activeIndexByFamily": {"claude": 0, "gemini": 0},
            "cursor": 0,
        })
        auth_data = {
            "url": "https://auth",
            "verifier": "v",
            "state": "encoded-state",
        }
        with patch.object(cli_module, "authorize_antigravity", return_value=auth_data), \
             patch("builtins.input", return_value="manual-code"), \
             patch.object(cli_module, "exchange_antigravity", return_value={
                 "type": "success",
                 "email": "new@example.com",
                 "refresh": "new-refresh|new-project",
                 "access": "new-access",
                 "expires": 9999999999,
                 "projectId": "new-project",
             }), \
             patch.object(cli_module, "sync_token_to_all_auth_stores"):
            success = run_login_flow(project_id="new-project", no_browser=True)

        self.assertTrue(success)
        loaded = load_accounts()
        self.assertEqual([acc["email"] for acc in loaded["accounts"]], [
            "existing@example.com",
            "new@example.com",
        ])
        self.assertEqual(loaded["activeIndex"], 1)
        self.assertEqual(loaded["activeIndexByFamily"], {"claude": 1, "gemini": 1})
        self.assertEqual(loaded["cursor"], 1)

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

    def test_set_account_project_updates_account_and_syncs_active_runtime(self):
        from .storage import load_accounts, save_accounts
        save_accounts({
            "version": 4,
            "accounts": [{
                "email": "user@example.com",
                "refreshToken": "raw-refresh",
                "projectId": "",
                "managedProjectId": "old-managed",
                "accessToken": "cached-access",
                "accessTokenExpiresAt": 9999999999,
            }],
            "activeIndex": 0,
            "activeIndexByFamily": {"claude": 0, "gemini": 0},
            "cursor": 0,
        })

        with patch("antigravity_auth.token.refresh_access_token", return_value={
            "access": "fresh-access",
            "refresh": "raw-refresh|project-123",
            "expires": 123,
        }) as refresh, patch(
            "antigravity_auth.cli.sync_token_to_all_auth_stores",
            return_value=True,
        ) as sync:
            self.assertTrue(set_account_project("0", "project-123"))

        loaded = load_accounts()
        self.assertEqual(loaded["accounts"][0]["projectId"], "project-123")
        self.assertEqual(loaded["accounts"][0]["managedProjectId"], "")
        refresh.assert_called_once_with(
            {"refresh": "raw-refresh|project-123", "email": "user@example.com"},
            persist=True,
            set_active=True,
        )
        sync.assert_called_once_with(
            access_token="fresh-access",
            refresh_token="raw-refresh|project-123",
            project_id="project-123",
            email="user@example.com",
            expires_ms=123,
            set_active=True,
        )

    def test_set_account_project_does_not_sync_inactive_account(self):
        from .storage import load_accounts, save_accounts
        save_accounts({
            "version": 4,
            "accounts": [
                {"email": "active@example.com", "refreshToken": "active-refresh", "projectId": "active-project"},
                {"email": "inactive@example.com", "refreshToken": "inactive-refresh", "projectId": ""},
            ],
            "activeIndex": 0,
            "activeIndexByFamily": {"claude": 0, "gemini": 0},
            "cursor": 0,
        })

        with patch("antigravity_auth.token.refresh_access_token") as refresh, patch(
            "antigravity_auth.cli.sync_token_to_all_auth_stores",
        ) as sync:
            self.assertTrue(set_account_project("inactive@example.com", "project-456"))

        loaded = load_accounts()
        self.assertEqual(loaded["accounts"][1]["projectId"], "project-456")
        refresh.assert_not_called()
        sync.assert_not_called()

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

    def test_account_switch_sets_family_indices_and_cursor(self):
        from .storage import load_accounts, save_accounts
        save_accounts({
            "version": 4,
            "accounts": [
                {"email": "old@example.com", "refreshToken": "old-refresh", "projectId": "old-project"},
                {"email": "new@example.com", "refreshToken": "new-refresh", "projectId": "new-project"},
            ],
            "activeIndex": 0,
            "activeIndexByFamily": {"claude": 0, "gemini": 0},
            "cursor": 0,
        })

        with patch("builtins.input", side_effect=["3", "1", "6"]), \
             patch("antigravity_auth.token.refresh_access_token", return_value={
                 "access": "new-access",
                 "refresh": "new-refresh|new-project",
                 "expires": 123,
             }), \
             patch("antigravity_auth.cli.sync_token_to_all_auth_stores"):
            interactive_accounts_menu()

        loaded = load_accounts()
        self.assertEqual(loaded["activeIndex"], 1)
        self.assertEqual(loaded["activeIndexByFamily"], {"claude": 1, "gemini": 1})
        self.assertEqual(loaded["cursor"], 1)

if __name__ == "__main__":
    unittest.main()
