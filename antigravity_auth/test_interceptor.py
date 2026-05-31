"""Tests for the HTTP interceptor — headers-only request hook."""

import json
import os
import stat
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx


class TestTracePermissions(unittest.TestCase):
    """Trace directory and files must use private permissions."""

    def test_trace_directory_is_private(self):
        """Trace directory must be created with 0o700 permissions."""
        with tempfile.TemporaryDirectory() as tmp:
            trace_dir = Path(tmp) / "antigravity-traces"
            import antigravity_auth.interceptor as _int
            old_trace_dir = _int._TRACE_DIR
            _int._TRACE_DIR = None
            try:
                with patch.object(_int, "get_hermes_home", create=True, side_effect=AttributeError):
                    pass
                with patch("antigravity_auth.interceptor._TRACE_DIR", None):
                    with patch("antigravity_auth.storage.get_hermes_home", return_value=Path(tmp)):
                        _int._TRACE_DIR = None
                        _int._trace("test-event", key="value")
            finally:
                _int._TRACE_DIR = old_trace_dir

            self.assertTrue(trace_dir.exists(), "trace directory was not created")
            mode = stat.S_IMODE(trace_dir.stat().st_mode)
            self.assertEqual(mode, 0o700, f"trace dir mode is {oct(mode)}, expected 0o700")

    def test_trace_file_is_private(self):
        """Trace files must be created with 0o600 permissions."""
        with tempfile.TemporaryDirectory() as tmp:
            trace_dir = Path(tmp) / "antigravity-traces"
            import antigravity_auth.interceptor as _int
            old_trace_dir = _int._TRACE_DIR
            _int._TRACE_DIR = None
            try:
                with patch("antigravity_auth.storage.get_hermes_home", return_value=Path(tmp)):
                    _int._TRACE_DIR = None
                    _int._trace("test-event", key="value")
            finally:
                _int._TRACE_DIR = old_trace_dir

            trace_files = [f for f in trace_dir.iterdir() if f.is_file()]
            self.assertGreater(len(trace_files), 0, "no trace files created")
            for tf in trace_files:
                mode = stat.S_IMODE(tf.stat().st_mode)
                self.assertEqual(mode, 0o600, f"trace file {tf.name} mode is {oct(mode)}, expected 0o600")

    def test_cleanup_old_traces(self):
        """Trace rotation should remove old files beyond max_files."""
        with tempfile.TemporaryDirectory() as tmp:
            traces_path = Path(tmp)
            import time
            for i in range(10):
                f = traces_path / f"trace-{i}.log"
                f.write_text(f"line {i}")
                os.utime(f, (time.time() - (10 - i), time.time() - (10 - i)))

            from antigravity_auth.interceptor import _cleanup_old_traces
            _cleanup_old_traces(traces_path, max_files=5)

            remaining = list(traces_path.glob("trace-*.log"))
            self.assertEqual(len(remaining), 5, f"Expected 5 files, got {len(remaining)}")


class TestModelHeaderHelpers(unittest.TestCase):

    def test_claude_uses_antigravity_headers_even_when_cli_first_enabled(self):
        from antigravity_auth.interceptor import _select_header_style_for_model
        self.assertEqual(
            _select_header_style_for_model("claude-sonnet-4-6-thinking", cli_first=True),
            "antigravity",
        )

    def test_gemini_uses_gemini_cli_headers_only_when_cli_first_enabled(self):
        from antigravity_auth.interceptor import _select_header_style_for_model
        self.assertEqual(
            _select_header_style_for_model("gemini-3.1-pro-high", cli_first=True),
            "gemini-cli",
        )
        self.assertEqual(
            _select_header_style_for_model("gemini-3.1-pro-high", cli_first=False),
            "antigravity",
        )

    def test_model_family_for_claude_and_gemini(self):
        from antigravity_auth.interceptor import _model_family_for_model
        self.assertEqual(_model_family_for_model("claude-sonnet-4-6"), "claude")
        self.assertEqual(_model_family_for_model("gemini-3.1-pro-high"), "gemini")
        self.assertEqual(_model_family_for_model("gpt-oss-120b-medium"), "gemini")


class TestRequestHook(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_hermes_home = os.environ.get("HERMES_HOME")
        os.environ["HERMES_HOME"] = self.temp_dir.name
        from antigravity_auth.config import invalidate_config_cache
        from antigravity_auth.accounts import shared
        invalidate_config_cache()
        self.original_shared_manager = shared.get_global_manager()
        shared._instance = None
        from antigravity_auth.interceptor import _antigravity_request_hook
        self.hook = _antigravity_request_hook

    def tearDown(self):
        from antigravity_auth.config import invalidate_config_cache
        from antigravity_auth.accounts import shared
        shared._instance = self.original_shared_manager
        if self.original_hermes_home is not None:
            os.environ["HERMES_HOME"] = self.original_hermes_home
        else:
            os.environ.pop("HERMES_HOME", None)
        invalidate_config_cache()
        self.temp_dir.cleanup()

    def _make_request(self, model="gemini-3-flash-preview"):
        body = {
            "project": "test",
            "model": model,
            "user_prompt_id": "abc",
            "request": {"contents": [{"role": "user", "parts": [{"text": "Hello"}]}]},
        }
        # httpx 0.28: must build request with json= then read
        r = httpx.Request(
            "POST",
            "https://cloudcode-pa.googleapis.com/v1internal:generateContent",
            json=body,
            headers={"Authorization": "Bearer test", "User-Agent": "hermes-agent"},
        )
        r.read()  # pre-load body
        return r

    def test_rewrites_headers(self):
        r = self._make_request()
        self.hook(r)
        ua = r.headers.get("User-Agent", "")
        self.assertNotIn("hermes-agent", ua)
        self.assertIn("Client-Metadata", r.headers)

    def test_removes_authorization_when_no_account_selected(self):
        r = self._make_request()
        self.hook(r)
        self.assertNotIn("Authorization", r.headers)
        self.assertEqual(r.extensions.get("antigravity_account_selection_failed"), True)

    def test_preserves_content_type(self):
        r = self._make_request()
        self.hook(r)
        self.assertIn("application/json", r.headers.get("content-type", ""))

    def test_claude_request_uses_antigravity_headers_when_cli_first_enabled(self):
        r = self._make_request(model="claude-sonnet-4-6-thinking")
        config = type("Config", (), {
            "cli_first": True,
            "soft_quota_cache_ttl_minutes": "auto",
            "quota_refresh_interval_minutes": 15,
            "account_selection_strategy": "hybrid",
            "pid_offset_enabled": False,
            "soft_quota_threshold_percent": 90,
        })()
        with patch("antigravity_auth.interceptor.get_config", return_value=config), patch(
            "antigravity_auth.interceptor._select_request_account",
            return_value=None,
        ), patch(
            "antigravity_auth.interceptor.build_antigravity_headers",
            return_value={"User-Agent": "antigravity-test"},
        ) as build_headers, patch(
            "antigravity_auth.interceptor.generate_fingerprint"
        ) as generate:
            self.hook(r)
        build_headers.assert_called_once_with(header_style="antigravity")
        generate.assert_not_called()

    def test_request_hook_records_header_style_and_model_family_metadata(self):
        r = self._make_request(model="claude-sonnet-4-6-thinking")
        config = type("Config", (), {
            "cli_first": True,
            "soft_quota_cache_ttl_minutes": "auto",
            "quota_refresh_interval_minutes": 15,
            "account_selection_strategy": "hybrid",
            "pid_offset_enabled": False,
            "soft_quota_threshold_percent": 90,
        })()
        with patch("antigravity_auth.interceptor.get_config", return_value=config):
            self.hook(r)
        self.assertEqual(r.extensions["antigravity_header_style"], "antigravity")
        self.assertEqual(r.extensions["antigravity_model_family"], "claude")

    def test_request_hook_sets_authorization_for_selected_account(self):
        class FakeRefreshParts:
            refresh_token = "refresh-1"
            project_id = "proj-1"
            managed_project_id = "managed-1"

        class FakeAccount:
            index = 7
            email = "selected@example.com"
            refresh_parts = FakeRefreshParts()

        class FakeManager:
            def __init__(self):
                self.family = None
                self.model = None
                self.strategy = None
                self.header_style = None
                self.pid_offset_enabled = None
                self.soft_quota_threshold_percent = None
                self.soft_quota_cache_ttl_ms = None
                self.marked_index = None
                self.saved = False

            def get_current_or_next_for_family(
                self,
                family,
                *,
                model=None,
                strategy=None,
                header_style=None,
                pid_offset_enabled=False,
                soft_quota_threshold_percent=100,
                soft_quota_cache_ttl_ms=600_000,
            ):
                self.family = family
                self.model = model
                self.strategy = strategy
                self.header_style = header_style
                self.pid_offset_enabled = pid_offset_enabled
                self.soft_quota_threshold_percent = soft_quota_threshold_percent
                self.soft_quota_cache_ttl_ms = soft_quota_cache_ttl_ms
                return FakeAccount()

            def mark_account_used(self, account_index):
                self.marked_index = account_index

            def save_to_disk(self):
                self.saved = True
                return True

        fake_mgr = FakeManager()
        config = type("Config", (), {
            "cli_first": True,
            "soft_quota_cache_ttl_minutes": "auto",
            "quota_refresh_interval_minutes": 15,
            "account_selection_strategy": "hybrid",
            "pid_offset_enabled": True,
            "soft_quota_threshold_percent": 80,
        })()
        r = self._make_request(model="claude-sonnet-4-6-thinking")

        with patch("antigravity_auth.interceptor.get_config", return_value=config), patch(
            "antigravity_auth.accounts.shared.get_or_create_global_manager",
            return_value=fake_mgr,
        ), patch(
            "antigravity_auth.token.refresh_access_token",
            return_value={
                "access": "selected-access",
                "refresh": "refresh-1|proj-1|managed-1",
                "expires": 123,
            },
        ), patch(
            "antigravity_auth.auth_sync.sync_token_to_all_auth_stores",
            return_value=True,
        ) as sync_all:
            self.hook(r)

        self.assertEqual(r.headers["Authorization"], "Bearer selected-access")
        self.assertEqual(r.extensions["antigravity_selected_account_index"], 7)
        self.assertEqual(r.extensions["antigravity_selected_account_identity"], {
            "email": "selected@example.com",
            "refresh_token": "refresh-1",
            "project_id": "proj-1",
            "managed_project_id": "managed-1",
        })
        self.assertEqual(fake_mgr.family, "claude")
        self.assertEqual(fake_mgr.model, "claude-sonnet-4-6-thinking")
        self.assertEqual(fake_mgr.header_style, "antigravity")
        sync_all.assert_called_once_with(
            access_token="selected-access",
            refresh_token="refresh-1|proj-1|managed-1",
            project_id="proj-1",
            email="selected@example.com",
            expires_ms=123,
            set_active=True,
        )
        self.assertEqual(fake_mgr.marked_index, 7)
        self.assertTrue(fake_mgr.saved)

    def test_request_hook_removes_stale_authorization_when_sync_reports_full_failure(self):
        class FakeRefreshParts:
            refresh_token = "refresh-1"
            project_id = "proj-1"
            managed_project_id = "managed-1"

        class FakeAccount:
            index = 7
            email = "selected@example.com"
            refresh_parts = FakeRefreshParts()

        class FakeManager:
            def __init__(self):
                self.marked_index = None
                self.saved = False

            def get_current_or_next_for_family(self, *args, **kwargs):
                return FakeAccount()

            def mark_account_used(self, account_index):
                self.marked_index = account_index

            def save_to_disk(self):
                self.saved = True
                return True

        fake_mgr = FakeManager()
        config = type("Config", (), {
            "cli_first": True,
            "soft_quota_cache_ttl_minutes": "auto",
            "quota_refresh_interval_minutes": 15,
            "account_selection_strategy": "hybrid",
            "pid_offset_enabled": True,
            "soft_quota_threshold_percent": 80,
        })()
        r = self._make_request(model="claude-sonnet-4-6-thinking")

        with patch("antigravity_auth.interceptor.get_config", return_value=config), patch(
            "antigravity_auth.accounts.shared.get_or_create_global_manager",
            return_value=fake_mgr,
        ), patch(
            "antigravity_auth.token.refresh_access_token",
            return_value={
                "access": "selected-access",
                "refresh": "rotated-refresh|proj-2|managed-2",
                "expires": 123,
            },
        ), patch(
            "antigravity_auth.auth_sync.sync_token_to_all_auth_stores",
            return_value=False,
        ):
            self.hook(r)

        self.assertNotIn("Authorization", r.headers)
        self.assertEqual(r.extensions.get("antigravity_account_selection_failed"), True)
        self.assertIsNone(fake_mgr.marked_index)
        self.assertFalse(fake_mgr.saved)

    def test_request_hook_uses_selected_token_when_native_google_oauth_sync_fails(self):
        from antigravity_auth.auth_sync import AuthSyncResult

        class FakeRefreshParts:
            refresh_token = "refresh-1"
            project_id = "proj-1"
            managed_project_id = "managed-1"

        class FakeAccount:
            index = 7
            email = "selected@example.com"
            refresh_parts = FakeRefreshParts()

        class FakeManager:
            def __init__(self):
                self.marked_index = None
                self.saved = False

            def get_current_or_next_for_family(self, *args, **kwargs):
                return FakeAccount()

            def mark_account_used(self, account_index):
                self.marked_index = account_index

            def save_to_disk(self):
                self.saved = True
                return True

        fake_mgr = FakeManager()
        config = type("Config", (), {
            "cli_first": True,
            "soft_quota_cache_ttl_minutes": "auto",
            "quota_refresh_interval_minutes": 15,
            "account_selection_strategy": "hybrid",
            "pid_offset_enabled": True,
            "soft_quota_threshold_percent": 80,
        })()
        r = self._make_request(model="claude-sonnet-4-6-thinking")

        with patch("antigravity_auth.interceptor.get_config", return_value=config), patch(
            "antigravity_auth.accounts.shared.get_or_create_global_manager",
            return_value=fake_mgr,
        ), patch(
            "antigravity_auth.token.refresh_access_token",
            return_value={
                "access": "selected-access",
                "refresh": "rotated-refresh|proj-2|managed-2",
                "expires": 123,
            },
        ), patch(
            "antigravity_auth.auth_sync.sync_token_to_all_auth_stores",
            return_value=AuthSyncResult(auth_json=True, google_oauth=False),
        ), self.assertLogs("antigravity_auth.interceptor", level="WARNING") as logs:
            self.hook(r)

        self.assertEqual(r.headers["Authorization"], "Bearer selected-access")
        self.assertEqual(r.extensions["antigravity_selected_account_index"], 7)
        self.assertEqual(fake_mgr.marked_index, 7)
        self.assertTrue(fake_mgr.saved)
        self.assertTrue(any("Native google_oauth sync failed" in message for message in logs.output))

    def test_request_account_refresh_uses_persist_true(self):
        from antigravity_auth.interceptor import _select_request_account

        class FakeAccount:
            index = 0
            email = "user@example.com"
            refresh_parts = type("Refresh", (), {
                "refresh_token": "r",
                "project_id": "p",
                "managed_project_id": "m",
            })()

        class FakeManager:
            def get_current_or_next_for_family(self, *args, **kwargs):
                return FakeAccount()

            def mark_account_used(self, index):
                pass

            def save_to_disk(self):
                return True

        config = type("Config", (), {
            "soft_quota_cache_ttl_minutes": "auto",
            "quota_refresh_interval_minutes": 15,
            "account_selection_strategy": "sticky",
            "pid_offset_enabled": False,
            "soft_quota_threshold_percent": 100,
            "proactive_refresh_buffer_seconds": 1800,
        })()
        calls = []

        with patch("antigravity_auth.accounts.shared.get_or_create_global_manager", return_value=FakeManager()), \
             patch("antigravity_auth.token.refresh_access_token", side_effect=lambda auth, **kw: calls.append(kw) or {"access": "a", "refresh": "r|p|m"}), \
             patch("antigravity_auth.auth_sync.sync_token_to_all_auth_stores", return_value=True):
            _select_request_account("claude-sonnet-4-6", "antigravity", config)

        self.assertEqual(calls[0].get("persist"), True)

    def test_request_account_uses_cached_access_token_without_refresh(self):
        import time
        from antigravity_auth.accounts.manager import AccountManager
        from antigravity_auth.interceptor import _select_request_account
        from antigravity_auth.storage import save_accounts

        save_accounts({
            "version": 4,
            "accounts": [{
                "email": "cached@example.com",
                "refreshToken": "cached-refresh",
                "projectId": "cached-project",
                "accessToken": "cached-access",
                "accessTokenExpiresAt": int(time.time() * 1000) + 3_600_000,
                "lastRefreshAt": 111,
            }],
            "activeIndex": 0,
            "cursor": 0,
            "activeIndexByFamily": {"claude": 0, "gemini": 0},
        })
        manager = AccountManager.load_from_disk()
        config = type("Config", (), {
            "soft_quota_cache_ttl_minutes": "auto",
            "quota_refresh_interval_minutes": 15,
            "account_selection_strategy": "sticky",
            "pid_offset_enabled": False,
            "soft_quota_threshold_percent": 100,
            "proactive_refresh_buffer_seconds": 60,
        })()

        with patch("antigravity_auth.accounts.shared.get_or_create_global_manager", return_value=manager), \
             patch("antigravity_auth.token.refresh_access_token") as refresh:
            selected = _select_request_account("claude-sonnet-4-6", "antigravity", config)

        self.assertIsNotNone(selected)
        self.assertEqual(selected["access"], "cached-access")
        refresh.assert_not_called()

    def test_request_account_refreshes_expired_cached_access_token(self):
        import time
        from antigravity_auth.accounts.manager import AccountManager
        from antigravity_auth.interceptor import _select_request_account
        from antigravity_auth.storage import load_accounts, save_accounts

        save_accounts({
            "version": 4,
            "accounts": [{
                "email": "expired@example.com",
                "refreshToken": "expired-refresh",
                "projectId": "expired-project",
                "accessToken": "expired-access",
                "accessTokenExpiresAt": int(time.time() * 1000) - 1,
            }],
            "activeIndex": 0,
            "cursor": 0,
            "activeIndexByFamily": {"claude": 0, "gemini": 0},
        })
        manager = AccountManager.load_from_disk()
        config = type("Config", (), {
            "soft_quota_cache_ttl_minutes": "auto",
            "quota_refresh_interval_minutes": 15,
            "account_selection_strategy": "sticky",
            "pid_offset_enabled": False,
            "soft_quota_threshold_percent": 100,
            "proactive_refresh_buffer_seconds": 60,
        })()

        with patch("antigravity_auth.accounts.shared.get_or_create_global_manager", return_value=manager), \
             patch("antigravity_auth.token.refresh_access_token", return_value={
                 "access": "fresh-access",
                 "refresh": "expired-refresh|expired-project",
                 "expires": int(time.time() * 1000) + 3_600_000,
             }) as refresh, \
             patch("antigravity_auth.auth_sync.sync_token_to_all_auth_stores", return_value=True):
            selected = _select_request_account("claude-sonnet-4-6", "antigravity", config)

        self.assertIsNotNone(selected)
        self.assertEqual(selected["access"], "fresh-access")
        refresh.assert_called_once()
        loaded = load_accounts()
        self.assertEqual(loaded["accounts"][0]["accessToken"], "fresh-access")
        self.assertIn("lastRefreshAt", loaded["accounts"][0])

    def test_persist_managed_state_does_not_overwrite_newer_rotated_token(self):
        from antigravity_auth.accounts.manager import AccountManager
        from antigravity_auth.interceptor import _persist_managed_account_state
        from antigravity_auth.storage import load_accounts, save_accounts, update_accounts

        save_accounts({
            "version": 4,
            "accounts": [{
                "email": "race@example.com",
                "refreshToken": "old-refresh",
                "projectId": "proj",
                "accessToken": "old-access",
                "accessTokenExpiresAt": 1000,
                "lastRefreshAt": 100,
            }],
            "activeIndex": 0,
            "cursor": 0,
            "activeIndexByFamily": {"claude": 0, "gemini": 0},
        })
        manager = AccountManager.load_from_disk()
        account = manager.get_account_by_index(0)
        self.assertIsNotNone(account)
        assert account is not None
        account.access = "stale-access"
        account.expires = 200
        account.last_refresh_at = 100
        account.last_used = 1234

        def rotate_elsewhere(data):
            data["accounts"][0].update({
                "refreshToken": "new-refresh",
                "accessToken": "new-access",
                "accessTokenExpiresAt": 9999,
                "lastRefreshAt": 999,
            })

        update_accounts(rotate_elsewhere)
        self.assertTrue(_persist_managed_account_state(account, family="claude", set_family_active=True))

        loaded = load_accounts()
        stored = loaded["accounts"][0]
        self.assertEqual(stored["refreshToken"], "new-refresh")
        self.assertEqual(stored["accessToken"], "new-access")
        self.assertEqual(stored["lastRefreshAt"], 999)
        self.assertEqual(stored["lastUsed"], 1234)
        self.assertEqual(account.refresh_parts.refresh_token, "new-refresh")

    def test_request_hook_uses_selected_account_fingerprint(self):
        from antigravity_auth.interceptor import _antigravity_request_hook

        class FakeAccount:
            index = 0
            fingerprint = {
                "userAgent": "UA/account-0",
                "clientMetadata": {"ideType": "ANTIGRAVITY", "platform": "MACOS", "pluginType": "ANTIGRAVITY"},
            }

        config = type("Config", (), {
            "cli_first": False,
            "soft_quota_cache_ttl_minutes": "auto",
            "quota_refresh_interval_minutes": 15,
            "account_selection_strategy": "sticky",
            "pid_offset_enabled": False,
            "soft_quota_threshold_percent": 100,
        })()
        request = httpx.Request(
            "POST",
            "https://cloudcode-pa.googleapis.com/v1internal:generateContent",
            headers={"Authorization": "Bearer stale", "Content-Type": "application/json"},
            json={"model": "claude-sonnet-4-6", "request": {"contents": []}},
        )
        request.read()

        with patch("antigravity_auth.interceptor.get_config", return_value=config), \
             patch("antigravity_auth.interceptor._select_request_account", return_value={"access": "a", "account_index": 0, "account": FakeAccount()}):
            _antigravity_request_hook(request)

        self.assertEqual(request.headers["User-Agent"], "UA/account-0")
        self.assertIn('"platform": "MACOS"', request.headers["Client-Metadata"])

    def test_request_hook_current_selected_account_fingerprint_does_not_generate_or_save(self):
        from antigravity_auth.interceptor import _antigravity_request_hook

        class FakeAccount:
            index = 0
            fingerprint = {
                "userAgent": "UA/current",
                "apiClient": "google-cloud-sdk vscode/1.96.0",
                "clientMetadata": {"ideType": "ANTIGRAVITY", "platform": "MACOS", "pluginType": "GEMINI"},
                "createdAt": 123,
            }

        class FakeManager:
            def __init__(self):
                self.save_count = 0

            def save_to_disk(self):
                self.save_count += 1
                return True

        config = type("Config", (), {
            "cli_first": False,
            "soft_quota_cache_ttl_minutes": "auto",
            "quota_refresh_interval_minutes": 15,
            "account_selection_strategy": "sticky",
            "pid_offset_enabled": False,
            "soft_quota_threshold_percent": 100,
        })()
        request = self._make_request(model="claude-sonnet-4-6")
        fake_mgr = FakeManager()

        with patch("antigravity_auth.interceptor.get_config", return_value=config), \
             patch("antigravity_auth.interceptor._select_request_account", return_value={"access": "a", "account_index": 0, "account": FakeAccount()}), \
             patch("antigravity_auth.interceptor.generate_fingerprint") as generate, \
             patch("antigravity_auth.interceptor.build_antigravity_headers") as build_headers, \
             patch("antigravity_auth.accounts.shared.get_global_manager", return_value=fake_mgr):
            _antigravity_request_hook(request)

        generate.assert_not_called()
        build_headers.assert_not_called()
        self.assertEqual(fake_mgr.save_count, 0)
        self.assertEqual(request.headers["User-Agent"], "UA/current")
        self.assertEqual(request.headers["X-Goog-Api-Client"], "google-cloud-sdk vscode/1.96.0")

    def test_request_hook_missing_selected_account_fingerprint_generates_and_saves_once(self):
        from antigravity_auth.interceptor import _antigravity_request_hook

        class FakeAccount:
            index = 0
            fingerprint = None

        class FakeManager:
            def __init__(self):
                self.save_count = 0

            def save_to_disk(self):
                self.save_count += 1
                return True

        config = type("Config", (), {
            "cli_first": False,
            "soft_quota_cache_ttl_minutes": "auto",
            "quota_refresh_interval_minutes": 15,
            "account_selection_strategy": "sticky",
            "pid_offset_enabled": False,
            "soft_quota_threshold_percent": 100,
        })()
        generated = {
            "userAgent": "UA/generated",
            "apiClient": "google-cloud-sdk vscode/1.96.0",
            "clientMetadata": {"ideType": "ANTIGRAVITY", "platform": "MACOS", "pluginType": "ANTIGRAVITY"},
            "createdAt": 123,
        }
        account = FakeAccount()
        request = self._make_request(model="claude-sonnet-4-6")
        fake_mgr = FakeManager()

        with patch("antigravity_auth.interceptor.get_config", return_value=config), \
             patch("antigravity_auth.interceptor._select_request_account", return_value={"access": "a", "account_index": 0, "account": account}), \
             patch("antigravity_auth.interceptor.generate_fingerprint", return_value=generated) as generate, \
             patch("antigravity_auth.accounts.shared.get_global_manager", return_value=fake_mgr):
            _antigravity_request_hook(request)

        generate.assert_called_once_with()
        self.assertIs(account.fingerprint, generated)
        self.assertEqual(fake_mgr.save_count, 1)
        self.assertEqual(request.headers["User-Agent"], "UA/generated")

    def test_request_hook_updated_selected_account_fingerprint_saves_once(self):
        from antigravity_auth.interceptor import _antigravity_request_hook

        class FakeAccount:
            index = 0
            fingerprint = {
                "userAgent": "UA/old-version",
                "clientMetadata": {"ideType": "ANTIGRAVITY", "platform": "MACOS", "pluginType": "ANTIGRAVITY"},
            }

        class FakeManager:
            def __init__(self):
                self.save_count = 0

            def save_to_disk(self):
                self.save_count += 1
                return True

        config = type("Config", (), {
            "cli_first": False,
            "soft_quota_cache_ttl_minutes": "auto",
            "quota_refresh_interval_minutes": 15,
            "account_selection_strategy": "sticky",
            "pid_offset_enabled": False,
            "soft_quota_threshold_percent": 100,
        })()
        account = FakeAccount()
        request = self._make_request(model="claude-sonnet-4-6")
        fake_mgr = FakeManager()

        with patch("antigravity_auth.interceptor.get_config", return_value=config), \
             patch("antigravity_auth.interceptor._select_request_account", return_value={"access": "a", "account_index": 0, "account": account}), \
             patch("antigravity_auth.interceptor.generate_fingerprint") as generate, \
             patch("antigravity_auth.accounts.shared.get_global_manager", return_value=fake_mgr):
            _antigravity_request_hook(request)

        generate.assert_not_called()
        self.assertIn("createdAt", account.fingerprint)
        self.assertIn("apiClient", account.fingerprint)
        self.assertEqual(fake_mgr.save_count, 1)
        self.assertEqual(request.headers["User-Agent"], "UA/old-version")

    def test_request_hook_removes_stale_authorization_when_selection_fails(self):
        from antigravity_auth.interceptor import _antigravity_request_hook

        config = type("Config", (), {
            "cli_first": False,
            "soft_quota_cache_ttl_minutes": "auto",
            "quota_refresh_interval_minutes": 15,
            "account_selection_strategy": "sticky",
            "pid_offset_enabled": False,
            "soft_quota_threshold_percent": 100,
        })()
        request = httpx.Request(
            "POST",
            "https://cloudcode-pa.googleapis.com/v1internal:generateContent",
            headers={"Authorization": "Bearer stale", "Content-Type": "application/json"},
            json={"model": "claude-sonnet-4-6", "request": {"contents": []}},
        )
        request.read()

        with patch("antigravity_auth.interceptor.get_config", return_value=config), \
             patch("antigravity_auth.interceptor._select_request_account", return_value=None):
            _antigravity_request_hook(request)

        self.assertNotEqual(request.headers.get("Authorization"), "Bearer stale")
        self.assertEqual(request.extensions.get("antigravity_account_selection_failed"), True)

    def test_request_hook_persists_rotated_refresh_before_saving_manager(self):
        class FakeRefreshParts:
            def __init__(self):
                self.refresh_token = "old-refresh"
                self.project_id = "proj-1"
                self.managed_project_id = "managed-1"

        class FakeAccount:
            def __init__(self):
                self.index = 7
                self.email = "selected@example.com"
                self.refresh_parts = FakeRefreshParts()

        class FakeManager:
            def __init__(self):
                self.account = FakeAccount()
                self.save_snapshot = None

            def get_current_or_next_for_family(self, *args, **kwargs):
                return self.account

            def mark_account_used(self, account_index):
                return None

            def save_to_disk(self):
                parts = self.account.refresh_parts
                self.save_snapshot = (
                    parts.refresh_token,
                    parts.project_id,
                    parts.managed_project_id,
                )
                return True

        fake_mgr = FakeManager()
        config = type("Config", (), {
            "cli_first": True,
            "soft_quota_cache_ttl_minutes": "auto",
            "quota_refresh_interval_minutes": 15,
            "account_selection_strategy": "hybrid",
            "pid_offset_enabled": True,
            "soft_quota_threshold_percent": 80,
        })()
        r = self._make_request(model="claude-sonnet-4-6-thinking")

        with patch("antigravity_auth.interceptor.get_config", return_value=config), patch(
            "antigravity_auth.accounts.shared.get_or_create_global_manager",
            return_value=fake_mgr,
        ), patch(
            "antigravity_auth.token.refresh_access_token",
            return_value={
                "access": "selected-access",
                "refresh": "new-refresh|proj-2|managed-2",
                "expires": 123,
            },
        ), patch(
            "antigravity_auth.auth_sync.sync_token_to_all_auth_stores",
            return_value=True,
        ):
            self.hook(r)

        self.assertEqual(fake_mgr.save_snapshot, ("new-refresh", "proj-2", "managed-2"))
        self.assertEqual(r.headers["Authorization"], "Bearer selected-access")
        self.assertEqual(r.extensions["antigravity_selected_account_identity"], {
            "email": "selected@example.com",
            "refresh_token": "new-refresh",
            "project_id": "proj-2",
            "managed_project_id": "managed-2",
        })

    def test_passthrough_non_cloudcode(self):
        r = httpx.Request("GET", "https://example.com/api")
        r.read()
        original_ua = r.headers.get("User-Agent", "")
        self.hook(r)
        self.assertEqual(r.headers.get("User-Agent", ""), original_ua)

    def test_passthrough_non_envelope(self):
        r = httpx.Request(
            "POST",
            "https://cloudcode-pa.googleapis.com/v1internal:generateContent",
            json={"messages": [{"role": "user"}]},
        )
        r.read()
        self.hook(r)
        self.assertEqual(r.headers.get("content-type", ""), "application/json")


class TestInstallProjectContextPatch(unittest.TestCase):

    def test_install_patches_ensure_project_context_to_antigravity_resolver(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        import antigravity_auth.interceptor as interceptor

        class FakeGeminiCloudCodeClient:
            def __init__(self):
                self._http = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, request=request)))
                self._project_context = None
                self._configured_project_id = "configured-project"

            def _ensure_project_context(self, access_token, model):
                return "native"

        def fake_wrap_code_assist_request(**kwargs):
            return kwargs

        updates = []
        fake_google_oauth = types.ModuleType("agent.google_oauth")
        fake_google_oauth.resolve_project_id_from_env = lambda: "env-project"
        fake_google_oauth.load_credentials = lambda: SimpleNamespace(
            project_id="stored-project",
            managed_project_id="managed-project",
        )
        fake_google_oauth.update_project_ids = lambda **kwargs: updates.append(kwargs)

        fake_gca = types.ModuleType("agent.gemini_cloudcode_adapter")
        fake_gca.GeminiCloudCodeClient = FakeGeminiCloudCodeClient
        fake_gca.wrap_code_assist_request = fake_wrap_code_assist_request

        fake_agent = types.ModuleType("agent")
        fake_agent.google_oauth = fake_google_oauth
        fake_agent.gemini_cloudcode_adapter = fake_gca

        original_state = (
            interceptor._PATCHED,
            interceptor._ORIGINAL_INIT,
            interceptor._ORIGINAL_WRAP_CODE_ASSIST,
            interceptor._ORIGINAL_ENSURE_PROJECT_CONTEXT,
        )
        interceptor._PATCHED = False
        interceptor._ORIGINAL_INIT = None
        interceptor._ORIGINAL_WRAP_CODE_ASSIST = None
        interceptor._ORIGINAL_ENSURE_PROJECT_CONTEXT = None

        resolved_calls = []
        resolved_ctx = SimpleNamespace(
            project_id="resolved-project",
            managed_project_id="",
            tier_id="standard-tier",
            source="test",
        )

        def fake_resolve(access_token, **kwargs):
            resolved_calls.append((access_token, kwargs))
            return resolved_ctx

        try:
            with patch.dict(sys.modules, {
                "agent": fake_agent,
                "agent.google_oauth": fake_google_oauth,
                "agent.gemini_cloudcode_adapter": fake_gca,
            }), patch("antigravity_auth.interceptor._install_global_httpx_hook"), patch(
                "antigravity_auth.interceptor._wrap_http_client",
                side_effect=lambda client: client,
            ), patch(
                "antigravity_auth.project_context.resolve_antigravity_project_context",
                side_effect=fake_resolve,
            ):
                self.assertTrue(interceptor.install())
                client = FakeGeminiCloudCodeClient()
                ctx = client._ensure_project_context("access-token", "claude-sonnet-4-6")
                self.assertIs(ctx, resolved_ctx)
                self.assertEqual(resolved_calls, [("access-token", {
                    "configured_project_id": "configured-project",
                    "env_project_id": "env-project",
                    "stored_project_id": "stored-project",
                    "managed_project_id": "managed-project",
                })])
                self.assertEqual(updates, [{
                    "project_id": "resolved-project",
                    "managed_project_id": "",
                }])
                self.assertTrue(interceptor.uninstall())
                self.assertEqual(FakeGeminiCloudCodeClient._ensure_project_context(client, "access-token", "model"), "native")
        finally:
            interceptor._PATCHED = original_state[0]
            interceptor._ORIGINAL_INIT = original_state[1]
            interceptor._ORIGINAL_WRAP_CODE_ASSIST = original_state[2]
            interceptor._ORIGINAL_ENSURE_PROJECT_CONTEXT = original_state[3]


class TestRetryWrapper(unittest.TestCase):

    def _make_request(self):
        req = httpx.Request(
            "POST",
            "https://cloudcode-pa.googleapis.com/v1internal:generateContent",
            json={"model": "gemini-3.1-pro-high", "request": {"contents": []}},
        )
        req.read()
        req.extensions["antigravity_selected_account_index"] = 0
        return req

    def test_send_wrapper_retries_replayable_401_once(self):
        from antigravity_auth.interceptor import _send_with_antigravity_retry

        calls = []

        def original_send(request, *args, **kwargs):
            calls.append(request)
            status = 401 if len(calls) == 1 else 200
            return httpx.Response(status, request=request)

        req = self._make_request()
        req.extensions["antigravity_retry_ready"] = True
        response = _send_with_antigravity_retry(original_send, req)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(calls), 2)
        self.assertTrue(calls[1].extensions["antigravity_retry_attempted"])
        self.assertEqual(calls[1].extensions["antigravity_retry_original_status"], 401)

    def test_headers_for_retry_removes_stale_auth_fingerprint_and_antigravity_headers(self):
        from antigravity_auth.interceptor import _headers_for_retry

        req = self._make_request()
        req.headers["Authorization"] = "Bearer stale-access-token"
        req.headers["Client-Metadata"] = "stale-fingerprint"
        req.headers["User-Agent"] = "stale-antigravity-ua"
        req.headers["X-Goog-Api-Client"] = "stale-client"
        req.headers["Antigravity-Device"] = "stale-device"
        req.headers["X-Antigravity-Device"] = "stale-x-device"
        req.headers["X-Other"] = "keep"

        headers = _headers_for_retry(req)

        self.assertNotIn("authorization", headers)
        self.assertNotIn("client-metadata", headers)
        self.assertNotIn("user-agent", headers)
        self.assertNotIn("x-goog-api-client", headers)
        self.assertNotIn("antigravity-device", headers)
        self.assertNotIn("x-antigravity-device", headers)
        self.assertEqual(headers.get("x-other"), "keep")
        self.assertEqual(headers.get("content-type"), "application/json")

    def test_send_wrapper_retry_does_not_reuse_stale_auth_or_fingerprint_headers(self):
        from antigravity_auth.interceptor import _send_with_antigravity_retry

        calls = []

        def original_send(request, *args, **kwargs):
            calls.append(request)
            status = 401 if len(calls) == 1 else 200
            return httpx.Response(status, request=request)

        req = self._make_request()
        req.extensions["antigravity_retry_ready"] = True
        req.headers["Authorization"] = "Bearer stale-access-token"
        req.headers["User-Agent"] = "stale-antigravity-ua"
        req.headers["X-Goog-Api-Client"] = "stale-client"
        req.headers["Client-Metadata"] = "stale-fingerprint"
        req.headers["X-Antigravity-Device"] = "stale-device"

        response = _send_with_antigravity_retry(original_send, req)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(calls), 2)
        retry_headers = calls[1].headers
        self.assertNotIn("authorization", retry_headers)
        self.assertNotIn("user-agent", retry_headers)
        self.assertNotIn("x-goog-api-client", retry_headers)
        self.assertNotIn("client-metadata", retry_headers)
        self.assertNotIn("x-antigravity-device", retry_headers)
        self.assertEqual(retry_headers.get("content-type"), "application/json")

    def test_send_wrapper_does_not_retry_without_successful_recovery_marker(self):
        from antigravity_auth.interceptor import _send_with_antigravity_retry

        calls = []

        def original_send(request, *args, **kwargs):
            calls.append(request)
            return httpx.Response(401, request=request)

        response = _send_with_antigravity_retry(original_send, self._make_request())

        self.assertEqual(response.status_code, 401)
        self.assertEqual(len(calls), 1)

    def test_send_wrapper_retry_guard_prevents_infinite_loop(self):
        from antigravity_auth.interceptor import _send_with_antigravity_retry

        calls = []

        def original_send(request, *args, **kwargs):
            calls.append(request)
            return httpx.Response(429, request=request)

        req = self._make_request()
        req.extensions["antigravity_retry_attempted"] = True
        response = _send_with_antigravity_retry(original_send, req)

        self.assertEqual(response.status_code, 429)
        self.assertEqual(len(calls), 1)

    def test_wrap_http_client_installs_hooks_once(self):
        from antigravity_auth.interceptor import _antigravity_request_hook, _antigravity_response_hook, _wrap_http_client

        client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, request=request)))
        try:
            _wrap_http_client(client)
            _wrap_http_client(client)
            self.assertEqual(client.event_hooks["request"].count(_antigravity_request_hook), 1)
            self.assertEqual(client.event_hooks["response"].count(_antigravity_response_hook), 1)
        finally:
            client.close()


class TestResponseHook(unittest.TestCase):

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

    def _make_response(
        self,
        model="gemini-3.1-pro-high",
        status=429,
        header_style="antigravity",
        json_body=None,
        stream_body=False,
    ):
        body = {"project": "proj", "model": model, "request": {"contents": []}}
        req = httpx.Request(
            "POST",
            "https://cloudcode-pa.googleapis.com/v1internal:generateContent",
            json=body,
        )
        req.read()
        req.extensions["antigravity_header_style"] = header_style
        req.extensions["antigravity_model_family"] = "claude" if "claude" in model else "gemini"
        if stream_body and json_body is not None:
            body_bytes = json.dumps(json_body).encode("utf-8")
            return httpx.Response(
                status,
                request=req,
                headers={"Retry-After": "3", "Content-Type": "application/json"},
                stream=httpx.ByteStream(body_bytes),
            )
        response_kwargs = {"json": json_body} if json_body is not None else {}
        return httpx.Response(status, request=req, headers={"Retry-After": "3"}, **response_kwargs)

    def test_response_account_for_request_skips_reindexed_identity_mismatch(self):
        from antigravity_auth.interceptor import _response_account_for_request

        class FakeRefreshParts:
            refresh_token = "other-refresh"
            project_id = "other-project"
            managed_project_id = "other-managed"

        class FakeAccount:
            index = 0
            email = "other@example.com"
            refresh_parts = FakeRefreshParts()

        class FakeManager:
            def __init__(self):
                self.current_requested = False

            def get_account_by_index(self, index):
                return FakeAccount() if index == 0 else None

            def get_current_account_for_family(self, family):
                self.current_requested = True
                return FakeAccount()

        mgr = FakeManager()
        selected = _response_account_for_request(mgr, {
            "antigravity_selected_account_index": 0,
            "antigravity_selected_account_identity": {
                "email": "removed@example.com",
                "refresh_token": "removed-refresh",
                "project_id": "removed-project",
                "managed_project_id": "removed-managed",
            },
        }, "gemini")

        self.assertIsNone(selected)
        self.assertFalse(mgr.current_requested)

    def test_403_does_not_cool_reindexed_account_when_identity_mismatches(self):
        from antigravity_auth.interceptor import _antigravity_response_hook

        class FakeRefreshParts:
            refresh_token = "other-refresh"
            project_id = "other-project"
            managed_project_id = "other-managed"

        class FakeAccount:
            def __init__(self):
                self.index = 0
                self.email = "other@example.com"
                self.refresh_parts = FakeRefreshParts()
                self.cooling_down_until = None
                self.cooldown_reason = None

        class FakeManager:
            def __init__(self):
                self.account = FakeAccount()
                self.saved = False
                self.rotation_requested = False

            def get_account_by_index(self, index):
                return self.account if index == 0 else None

            def get_current_account_for_family(self, family):
                return self.account

            def get_current_or_next_for_family(self, family, **kwargs):
                self.rotation_requested = True
                return self.account

            def save_to_disk(self):
                self.saved = True
                return True

        config = type("Config", (), {
            "proactive_token_refresh": False,
            "switch_on_first_rate_limit": True,
            "default_retry_after_seconds": 10,
            "cli_first": False,
            "account_selection_strategy": "sticky",
            "pid_offset_enabled": False,
            "soft_quota_threshold_percent": 100,
            "soft_quota_cache_ttl_minutes": "auto",
            "quota_refresh_interval_minutes": 15,
        })()
        mgr = FakeManager()
        response = self._make_response(model="gemini-3.1-pro-high", status=403)
        response.request.extensions["antigravity_selected_account_index"] = 0
        response.request.extensions["antigravity_selected_account_identity"] = {
            "email": "removed@example.com",
            "refresh_token": "removed-refresh",
            "project_id": "removed-project",
            "managed_project_id": "removed-managed",
        }

        with patch("antigravity_auth.config.get_config", return_value=config), patch(
            "antigravity_auth.accounts.manager.get_or_create_global_manager",
            return_value=mgr,
        ):
            _antigravity_response_hook(response)

        self.assertIsNone(mgr.account.cooldown_reason)
        self.assertFalse(mgr.saved)
        self.assertFalse(mgr.rotation_requested)

    def test_401_does_not_refresh_reindexed_account_when_identity_mismatches(self):
        from antigravity_auth.interceptor import _antigravity_response_hook
        from antigravity_auth.storage import save_accounts

        save_accounts({
            "version": 4,
            "accounts": [{
                "email": "other@example.com",
                "refreshToken": "other-refresh",
                "projectId": "other-project",
                "managedProjectId": "other-managed",
            }],
            "activeIndex": 0,
            "activeIndexByFamily": {"claude": 0, "gemini": 0},
        })
        response = self._make_response(model="gemini-3.1-pro-high", status=401)
        response.request.extensions["antigravity_selected_account_index"] = 0
        response.request.extensions["antigravity_selected_account_identity"] = {
            "email": "removed@example.com",
            "refresh_token": "removed-refresh",
            "project_id": "removed-project",
            "managed_project_id": "removed-managed",
        }
        config = type("Config", (), {
            "proactive_token_refresh": True,
            "switch_on_first_rate_limit": True,
            "default_retry_after_seconds": 10,
            "cli_first": False,
        })()

        with patch("antigravity_auth.config.get_config", return_value=config), patch(
            "antigravity_auth.token.refresh_access_token",
            return_value={"access": "should-not-be-used"},
        ) as refresh_mock:
            _antigravity_response_hook(response)

        refresh_mock.assert_not_called()

    def test_429_for_claude_marks_claude_family(self):
        from antigravity_auth.interceptor import _antigravity_response_hook

        class FakeAccount:
            index = 1

        class FakeManager:
            def __init__(self):
                self.current_family = None
                self.next_family = None
                self.account = FakeAccount()

            def get_current_account_for_family(self, family):
                self.current_family = family
                return self.account

            def get_current_or_next_for_family(self, family, **kwargs):
                self.next_family = family
                return self.account

            def save_to_disk(self):
                return True

        mgr = FakeManager()
        calls = []

        def fake_mark(account, retry_after_ms, family, header_style, model=None):
            calls.append((family, header_style, model))

        config = type("Config", (), {
            "proactive_token_refresh": False,
            "switch_on_first_rate_limit": True,
            "default_retry_after_seconds": 10,
            "cli_first": False,
        })()
        response = self._make_response(model="claude-sonnet-4-6-thinking", status=429)

        with patch("antigravity_auth.config.get_config", return_value=config), patch(
            "antigravity_auth.accounts.manager.get_or_create_global_manager",
            return_value=mgr,
        ), patch(
            "antigravity_auth.accounts.ratelimit.mark_rate_limited",
            side_effect=fake_mark,
        ):
            _antigravity_response_hook(response)

        self.assertEqual(mgr.current_family, "claude")
        self.assertEqual(calls, [("claude", "antigravity", "claude-sonnet-4-6-thinking")])

    def test_429_marks_selected_account_not_current_account(self):
        from antigravity_auth.interceptor import _antigravity_response_hook

        class FakeAccount:
            def __init__(self, index):
                self.index = index
                self.rate_limit_reset_times = type("RateLimits", (), {})()

        class FakeManager:
            def __init__(self):
                self.current = FakeAccount(1)
                self.selected = FakeAccount(0)

            def get_current_account_for_family(self, family):
                return self.current

            def get_account_by_index(self, index):
                return self.selected if index == 0 else None

            def get_current_or_next_for_family(self, family, **kwargs):
                return self.selected

            def save_to_disk(self):
                return True

        config = type("Config", (), {
            "proactive_token_refresh": False,
            "switch_on_first_rate_limit": True,
            "default_retry_after_seconds": 10,
            "cli_first": False,
            "account_selection_strategy": "sticky",
            "pid_offset_enabled": False,
            "soft_quota_threshold_percent": 100,
            "soft_quota_cache_ttl_minutes": "auto",
            "quota_refresh_interval_minutes": 15,
        })()
        mgr = FakeManager()
        marked = []
        response = self._make_response(
            model="claude-sonnet-4-6",
            status=429,
            header_style="antigravity",
        )
        response.request.extensions["antigravity_selected_account_index"] = 0

        with patch("antigravity_auth.config.get_config", return_value=config), patch(
            "antigravity_auth.accounts.manager.get_or_create_global_manager",
            return_value=mgr,
        ), patch(
            "antigravity_auth.accounts.ratelimit.mark_rate_limited",
            side_effect=lambda account, *args: marked.append(account.index),
        ):
            _antigravity_response_hook(response)

        self.assertEqual(marked, [0])

    def test_429_uses_reason_aware_backoff(self):
        from antigravity_auth.interceptor import _antigravity_response_hook

        class FakeAccount:
            index = 0

        class FakeManager:
            def __init__(self):
                self.account = FakeAccount()
                self.reason_call = None

            def get_account_by_index(self, index):
                return self.account

            def get_current_account_for_family(self, family):
                return self.account

            def mark_rate_limited_with_reason(
                self,
                account,
                family,
                header_style,
                model,
                reason,
                retry_after_ms=None,
                failure_ttl_ms=3600_000,
            ):
                self.reason_call = {
                    "account_index": account.index,
                    "family": family,
                    "header_style": header_style,
                    "model": model,
                    "reason": reason,
                    "retry_after_ms": retry_after_ms,
                }
                return 3957.0

            def get_current_or_next_for_family(self, family, **kwargs):
                return self.account

            def save_to_disk(self):
                return True

        config = type("Config", (), {
            "proactive_token_refresh": False,
            "switch_on_first_rate_limit": True,
            "default_retry_after_seconds": 10,
            "cli_first": False,
            "account_selection_strategy": "sticky",
            "pid_offset_enabled": False,
            "soft_quota_threshold_percent": 100,
            "soft_quota_cache_ttl_minutes": "auto",
            "quota_refresh_interval_minutes": 15,
        })()
        mgr = FakeManager()
        body = {
            "error": {
                "code": 429,
                "message": "You have exhausted your capacity on this model. Your quota will reset after 3s.",
                "status": "RESOURCE_EXHAUSTED",
                "details": [{"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "3.957525076s"}],
            }
        }
        response = self._make_response(
            model="claude-sonnet-4-6",
            status=429,
            header_style="antigravity",
            json_body=body,
            stream_body=True,
        )
        response.request.extensions["antigravity_selected_account_index"] = 0

        with patch("antigravity_auth.config.get_config", return_value=config), patch(
            "antigravity_auth.accounts.manager.get_or_create_global_manager",
            return_value=mgr,
        ):
            _antigravity_response_hook(response)

        self.assertIsNotNone(mgr.reason_call)
        assert mgr.reason_call is not None
        self.assertEqual(mgr.reason_call["account_index"], 0)
        self.assertEqual(mgr.reason_call["reason"], "MODEL_CAPACITY_EXHAUSTED")
        self.assertAlmostEqual(mgr.reason_call["retry_after_ms"], 3957.0, places=3)

    def test_429_marks_only_actual_header_style(self):
        from antigravity_auth.interceptor import _antigravity_response_hook

        class FakeAccount:
            index = 1

        class FakeManager:
            def __init__(self):
                self.account = FakeAccount()

            def get_current_account_for_family(self, family):
                return self.account

            def get_current_or_next_for_family(self, family, **kwargs):
                return self.account

            def save_to_disk(self):
                return True

        calls = []

        def fake_mark(account, retry_after_ms, family, header_style, model=None):
            calls.append((family, header_style, model))

        config = type("Config", (), {
            "proactive_token_refresh": False,
            "switch_on_first_rate_limit": True,
            "default_retry_after_seconds": 10,
            "cli_first": True,
        })()
        response = self._make_response(
            model="gemini-3.1-pro-high",
            status=429,
            header_style="antigravity",
        )

        with patch("antigravity_auth.config.get_config", return_value=config), patch(
            "antigravity_auth.accounts.manager.get_or_create_global_manager",
            return_value=FakeManager(),
        ), patch(
            "antigravity_auth.accounts.ratelimit.mark_rate_limited",
            side_effect=fake_mark,
        ):
            _antigravity_response_hook(response)

        self.assertEqual(calls, [("gemini", "antigravity", "gemini-3.1-pro-high")])

    def test_429_rotation_uses_configured_selection_context(self):
        from antigravity_auth.accounts.quota import compute_soft_quota_cache_ttl_ms
        from antigravity_auth.interceptor import _antigravity_response_hook

        class FakeAccount:
            index = 1

        class FakeManager:
            def __init__(self):
                self.account = FakeAccount()
                self.next_family = None
                self.next_kwargs = None

            def get_current_account_for_family(self, family):
                return self.account

            def get_current_or_next_for_family(self, family, **kwargs):
                self.next_family = family
                self.next_kwargs = kwargs
                return self.account

            def save_to_disk(self):
                return True

        config = type("Config", (), {
            "proactive_token_refresh": False,
            "switch_on_first_rate_limit": True,
            "default_retry_after_seconds": 10,
            "cli_first": True,
            "account_selection_strategy": "round-robin",
            "pid_offset_enabled": True,
            "soft_quota_threshold_percent": 77,
            "soft_quota_cache_ttl_minutes": 5,
            "quota_refresh_interval_minutes": 15,
        })()
        mgr = FakeManager()
        response = self._make_response(
            model="gemini-3.1-pro-high",
            status=429,
            header_style="antigravity",
        )

        with patch("antigravity_auth.config.get_config", return_value=config), patch(
            "antigravity_auth.accounts.manager.get_or_create_global_manager",
            return_value=mgr,
        ), patch("antigravity_auth.accounts.ratelimit.mark_rate_limited"):
            _antigravity_response_hook(response)

        self.assertEqual(mgr.next_family, "gemini")
        self.assertEqual(mgr.next_kwargs, {
            "model": "gemini-3.1-pro-high",
            "strategy": "round-robin",
            "header_style": "antigravity",
            "pid_offset_enabled": True,
            "soft_quota_threshold_percent": 77,
            "soft_quota_cache_ttl_ms": compute_soft_quota_cache_ttl_ms(5, 15),
        })

    def test_403_rotation_uses_configured_selection_context(self):
        from antigravity_auth.accounts.quota import compute_soft_quota_cache_ttl_ms
        from antigravity_auth.interceptor import _antigravity_response_hook

        class FakeAccount:
            index = 2

        class FakeManager:
            def __init__(self):
                self.account = FakeAccount()
                self.current_family = None
                self.next_family = None
                self.next_kwargs = None

            def get_current_account_for_family(self, family):
                self.current_family = family
                return self.account

            def get_current_or_next_for_family(self, family, **kwargs):
                self.next_family = family
                self.next_kwargs = kwargs
                return self.account

            def save_to_disk(self):
                return True

        config = type("Config", (), {
            "proactive_token_refresh": False,
            "switch_on_first_rate_limit": True,
            "cli_first": False,
            "account_selection_strategy": "round-robin",
            "pid_offset_enabled": True,
            "soft_quota_threshold_percent": 77,
            "soft_quota_cache_ttl_minutes": 5,
            "quota_refresh_interval_minutes": 15,
            "default_retry_after_seconds": 10,
        })()
        mgr = FakeManager()
        response = self._make_response(
            model="gemini-3.1-pro-high",
            status=403,
            header_style="gemini-cli",
        )

        with patch("antigravity_auth.config.get_config", return_value=config), patch(
            "antigravity_auth.accounts.manager.get_or_create_global_manager",
            return_value=mgr,
        ):
            _antigravity_response_hook(response)

        self.assertEqual(mgr.current_family, "gemini")
        self.assertEqual(mgr.next_family, "gemini")
        self.assertEqual(mgr.next_kwargs, {
            "model": "gemini-3.1-pro-high",
            "strategy": "round-robin",
            "header_style": "gemini-cli",
            "pid_offset_enabled": True,
            "soft_quota_threshold_percent": 77,
            "soft_quota_cache_ttl_ms": compute_soft_quota_cache_ttl_ms(5, 15),
        })

    def test_403_cools_selected_account_not_current_account(self):
        from antigravity_auth.interceptor import _antigravity_response_hook

        class FakeAccount:
            def __init__(self, index):
                self.index = index
                self.cooling_down_until = None
                self.cooldown_reason = None
                self.refresh_parts = type("RefreshParts", (), {
                    "refresh_token": "r",
                    "project_id": "p",
                    "managed_project_id": "m",
                })()
                self.email = "user@example.com"

        class FakeManager:
            def __init__(self):
                self.current = FakeAccount(1)
                self.selected = FakeAccount(0)

            def get_current_account_for_family(self, family):
                return self.current

            def get_account_by_index(self, index):
                return self.selected if index == 0 else None

            def get_current_or_next_for_family(self, family, **kwargs):
                return self.selected

            def save_to_disk(self):
                return True

        config = type("Config", (), {
            "proactive_token_refresh": False,
            "switch_on_first_rate_limit": True,
            "default_retry_after_seconds": 10,
            "cli_first": False,
            "account_selection_strategy": "sticky",
            "pid_offset_enabled": False,
            "soft_quota_threshold_percent": 100,
            "soft_quota_cache_ttl_minutes": "auto",
            "quota_refresh_interval_minutes": 15,
        })()
        mgr = FakeManager()
        response = self._make_response(
            model="claude-sonnet-4-6",
            status=403,
            header_style="antigravity",
        )
        response.request.extensions["antigravity_selected_account_index"] = 0

        with patch("antigravity_auth.config.get_config", return_value=config), patch(
            "antigravity_auth.accounts.manager.get_or_create_global_manager",
            return_value=mgr,
        ), patch(
            "antigravity_auth.token.refresh_access_token",
            return_value={},
        ):
            _antigravity_response_hook(response)

        self.assertEqual(mgr.selected.cooldown_reason, "auth-failure")
        self.assertIsNone(mgr.current.cooldown_reason)
        self.assertNotIn("antigravity_retry_ready", response.request.extensions)

    def test_401_syncs_rotated_refresh_token_to_all_auth_stores(self):
        from antigravity_auth.interceptor import _antigravity_response_hook
        from antigravity_auth.storage import load_accounts, save_accounts

        save_accounts({
            "version": 4,
            "accounts": [{
                "email": "user@example.com",
                "refreshToken": "old-refresh",
                "projectId": "proj-1",
            }],
            "activeIndex": 0,
            "activeIndexByFamily": {"claude": 0, "gemini": 0},
        })
        req = httpx.Request("POST", "https://cloudcode-pa.googleapis.com/v1internal:generateContent")
        response = httpx.Response(401, request=req)
        synced = []
        config = type("Config", (), {
            "proactive_token_refresh": True,
            "cli_first": False,
        })()

        with patch("antigravity_auth.config.get_config", return_value=config), patch(
            "antigravity_auth.token.refresh_access_token",
            return_value={
            "access": "new-access",
            "refresh": "new-refresh|proj-2|managed-2",
            "expires": 123,
            },
        ), patch(
            "antigravity_auth.auth_sync.sync_token_to_all_auth_stores",
            side_effect=lambda **kw: synced.append(kw) or True,
        ):
            _antigravity_response_hook(response)

        self.assertEqual(synced[0], {
            "access_token": "new-access",
            "refresh_token": "new-refresh|proj-2|managed-2",
            "project_id": "proj-2",
            "email": "user@example.com",
            "expires_ms": 123,
            "set_active": True,
        })
        loaded = load_accounts()
        self.assertEqual(loaded["accounts"][0]["refreshToken"], "new-refresh")
        self.assertEqual(loaded["accounts"][0]["projectId"], "proj-2")
        self.assertEqual(loaded["accounts"][0]["managedProjectId"], "managed-2")
        self.assertTrue(response.request.extensions["antigravity_retry_ready"])
        self.assertEqual(response.request.extensions["antigravity_retry_action"], "refreshed-selected-account")

    def test_refreshed_token_sync_returns_none_when_auth_json_sync_fails(self):
        from antigravity_auth.auth_sync import AuthSyncResult
        from antigravity_auth.interceptor import _sync_refreshed_token_to_all_auth_stores

        with patch(
            "antigravity_auth.auth_sync.sync_token_to_all_auth_stores",
            return_value=AuthSyncResult(auth_json=False, google_oauth=True),
        ) as mock_sync:
            parsed = _sync_refreshed_token_to_all_auth_stores(
                refreshed={
                    "access": "new-access",
                    "refresh": "new-refresh|proj-2|managed-2",
                    "expires": 123,
                },
                packed_refresh="old-refresh|proj-1|managed-1",
                project_id="proj-1",
                email="user@example.com",
            )

        self.assertIsNone(parsed)
        mock_sync.assert_called_once_with(
            access_token="new-access",
            refresh_token="new-refresh|proj-2|managed-2",
            project_id="proj-2",
            email="user@example.com",
            expires_ms=123,
            set_active=True,
        )

    def test_401_refreshes_selected_claude_account_not_global_active(self):
        from antigravity_auth.interceptor import _antigravity_response_hook
        from antigravity_auth.storage import load_accounts, save_accounts

        save_accounts({
            "version": 4,
            "accounts": [
                {
                    "email": "global-active@example.com",
                    "refreshToken": "global-refresh",
                    "projectId": "proj-global",
                },
                {
                    "email": "claude@example.com",
                    "refreshToken": "claude-refresh",
                    "projectId": "proj-claude",
                    "managedProjectId": "managed-claude",
                },
            ],
            "activeIndex": 0,
            "activeIndexByFamily": {"claude": 1, "gemini": 0},
        })
        body = {
            "project": "proj",
            "model": "claude-sonnet-4-6-thinking",
            "request": {"contents": []},
        }
        req = httpx.Request(
            "POST",
            "https://cloudcode-pa.googleapis.com/v1internal:generateContent",
            json=body,
        )
        req.read()
        req.extensions["antigravity_header_style"] = "antigravity"
        req.extensions["antigravity_model_family"] = "claude"
        req.extensions["antigravity_selected_account_index"] = 1
        response = httpx.Response(401, request=req)
        refresh_calls = []
        sync_calls = []
        config = type("Config", (), {
            "proactive_token_refresh": True,
            "cli_first": False,
        })()

        def fake_refresh(auth, **kwargs):
            refresh_calls.append(auth)
            return {
                "access": "new-claude-access",
                "refresh": "rotated-claude|proj-rotated|managed-rotated",
                "expires": 456,
            }

        with patch("antigravity_auth.config.get_config", return_value=config), patch(
            "antigravity_auth.token.refresh_access_token",
            side_effect=fake_refresh,
        ), patch(
            "antigravity_auth.auth_sync.sync_token_to_all_auth_stores",
            side_effect=lambda **kw: sync_calls.append(kw) or True,
        ):
            _antigravity_response_hook(response)

        self.assertEqual(refresh_calls, [{
            "refresh": "claude-refresh|proj-claude|managed-claude",
            "email": "claude@example.com",
        }])
        self.assertEqual(sync_calls[0], {
            "access_token": "new-claude-access",
            "refresh_token": "rotated-claude|proj-rotated|managed-rotated",
            "project_id": "proj-rotated",
            "email": "claude@example.com",
            "expires_ms": 456,
            "set_active": True,
        })
        loaded = load_accounts()
        self.assertEqual(loaded["accounts"][0]["refreshToken"], "global-refresh")
        self.assertEqual(loaded["accounts"][1]["refreshToken"], "rotated-claude")
        self.assertEqual(loaded["accounts"][1]["projectId"], "proj-rotated")
        self.assertEqual(loaded["accounts"][1]["managedProjectId"], "managed-rotated")
        self.assertTrue(response.request.extensions["antigravity_retry_ready"])
        self.assertEqual(response.request.extensions["antigravity_retry_action"], "refreshed-selected-account")

    def test_403_rotation_syncs_next_account_to_all_auth_stores(self):
        from antigravity_auth.interceptor import _antigravity_response_hook

        class FakeRefreshParts:
            def __init__(self, refresh_token, project_id, managed_project_id):
                self.refresh_token = refresh_token
                self.project_id = project_id
                self.managed_project_id = managed_project_id

        class FakeAccount:
            def __init__(self, index, email, refresh_token, project_id, managed_project_id=""):
                self.index = index
                self.email = email
                self.refresh_parts = FakeRefreshParts(refresh_token, project_id, managed_project_id)
                self.cooling_down_until = None
                self.cooldown_reason = None

        class FakeManager:
            def __init__(self):
                self.active = FakeAccount(0, "active@example.com", "active-refresh", "proj-active")
                self.next = FakeAccount(1, "next@example.com", "next-refresh", "proj-next", "managed-next")

            def get_current_account_for_family(self, family):
                return self.active

            def get_current_or_next_for_family(self, family, **kwargs):
                return self.next

            def save_to_disk(self):
                return True

        config = type("Config", (), {
            "proactive_token_refresh": False,
            "switch_on_first_rate_limit": True,
            "cli_first": False,
            "account_selection_strategy": "sticky",
            "pid_offset_enabled": False,
            "soft_quota_threshold_percent": 100,
            "soft_quota_cache_ttl_minutes": "auto",
            "quota_refresh_interval_minutes": 15,
            "default_retry_after_seconds": 10,
        })()
        response = self._make_response(model="gemini-3.1-pro-high", status=403)
        sync_calls = []

        with patch("antigravity_auth.config.get_config", return_value=config), patch(
            "antigravity_auth.accounts.manager.get_or_create_global_manager",
            return_value=FakeManager(),
        ), patch("antigravity_auth.token.refresh_access_token", return_value={
            "access": "rotated-access",
            "refresh": "rotated-refresh|proj-rotated|managed-rotated",
            "expires": 789,
        }), patch(
            "antigravity_auth.auth_sync.sync_token_to_all_auth_stores",
            side_effect=lambda **kw: sync_calls.append(kw) or True,
        ):
            _antigravity_response_hook(response)

        self.assertEqual(sync_calls, [{
            "access_token": "rotated-access",
            "refresh_token": "rotated-refresh|proj-rotated|managed-rotated",
            "project_id": "proj-rotated",
            "email": "next@example.com",
            "expires_ms": 789,
            "set_active": True,
        }])
        self.assertTrue(response.request.extensions["antigravity_retry_ready"])
        self.assertEqual(response.request.extensions["antigravity_retry_action"], "rotated-after-403")

    def test_429_rotation_syncs_next_account_to_all_auth_stores(self):
        from antigravity_auth.interceptor import _antigravity_response_hook

        class FakeRefreshParts:
            def __init__(self, refresh_token, project_id, managed_project_id):
                self.refresh_token = refresh_token
                self.project_id = project_id
                self.managed_project_id = managed_project_id

        class FakeAccount:
            def __init__(self, index, email, refresh_token, project_id, managed_project_id=""):
                self.index = index
                self.email = email
                self.refresh_parts = FakeRefreshParts(refresh_token, project_id, managed_project_id)

        class FakeManager:
            def __init__(self):
                self.active = FakeAccount(0, "active@example.com", "active-refresh", "proj-active")
                self.next = FakeAccount(1, "next@example.com", "next-refresh", "proj-next", "managed-next")

            def get_current_account_for_family(self, family):
                return self.active

            def get_current_or_next_for_family(self, family, **kwargs):
                return self.next

            def save_to_disk(self):
                return True

        config = type("Config", (), {
            "proactive_token_refresh": False,
            "switch_on_first_rate_limit": True,
            "cli_first": False,
            "account_selection_strategy": "sticky",
            "pid_offset_enabled": False,
            "soft_quota_threshold_percent": 100,
            "soft_quota_cache_ttl_minutes": "auto",
            "quota_refresh_interval_minutes": 15,
            "default_retry_after_seconds": 10,
        })()
        response = self._make_response(model="gemini-3.1-pro-high", status=429)
        sync_calls = []

        with patch("antigravity_auth.config.get_config", return_value=config), patch(
            "antigravity_auth.accounts.manager.get_or_create_global_manager",
            return_value=FakeManager(),
        ), patch("antigravity_auth.accounts.ratelimit.mark_rate_limited"), patch(
            "antigravity_auth.token.refresh_access_token",
            return_value={
                "access": "rotated-access",
                "refresh": "rotated-refresh|proj-rotated|managed-rotated",
                "expires": 987,
            },
        ), patch(
            "antigravity_auth.auth_sync.sync_token_to_all_auth_stores",
            side_effect=lambda **kw: sync_calls.append(kw) or True,
        ):
            _antigravity_response_hook(response)

        self.assertEqual(sync_calls, [{
            "access_token": "rotated-access",
            "refresh_token": "rotated-refresh|proj-rotated|managed-rotated",
            "project_id": "proj-rotated",
            "email": "next@example.com",
            "expires_ms": 987,
            "set_active": True,
        }])
        self.assertTrue(response.request.extensions["antigravity_retry_ready"])
        self.assertEqual(response.request.extensions["antigravity_retry_action"], "rotated-after-429")

    def test_token_watchdog_resolves_family_index_when_global_active_invalid(self):
        from antigravity_auth.storage import save_accounts
        from antigravity_auth.token_watchdog import _refresh_if_needed

        save_accounts({
            "version": 4,
            "accounts": [
                {"email": "active@example.com", "refreshToken": "active-refresh", "projectId": "proj-active"},
                {
                    "email": "family@example.com",
                    "refreshToken": "family-refresh",
                    "projectId": "proj-family",
                    "managedProjectId": "managed-family",
                },
            ],
            "activeIndex": 99,
            "activeIndexByFamily": {"claude": 1, "gemini": 0},
            "cursor": 0,
        })

        fake_agent = types.ModuleType("agent")
        fake_google_oauth = types.ModuleType("agent.google_oauth")
        setattr(fake_google_oauth, "load_credentials", lambda: type("Creds", (), {
            "refresh_token": "stored-refresh",
            "expires_ms": 0,
        })())
        original_agent = sys.modules.get("agent")
        original_google_oauth = sys.modules.get("agent.google_oauth")
        sys.modules["agent"] = fake_agent
        sys.modules["agent.google_oauth"] = fake_google_oauth
        refresh_calls = []
        config = type("Config", (), {"proactive_refresh_buffer_seconds": 1800})()

        def fake_refresh(auth, **kwargs):
            refresh_calls.append(auth)
            return {"access": "access-family", "refresh": "family-rotated|proj-family", "expires": 123}

        try:
            with patch("antigravity_auth.token.refresh_access_token", side_effect=fake_refresh), \
                 patch("antigravity_auth.auth_sync.sync_token_to_all_auth_stores", return_value=True), \
                 patch("antigravity_auth.auth_sync.sync_token_to_google_oauth", return_value=True):
                _refresh_if_needed(config)
        finally:
            if original_agent is None:
                sys.modules.pop("agent", None)
            else:
                sys.modules["agent"] = original_agent
            if original_google_oauth is None:
                sys.modules.pop("agent.google_oauth", None)
            else:
                sys.modules["agent.google_oauth"] = original_google_oauth

        self.assertEqual(refresh_calls, [{
            "refresh": "family-refresh|proj-family|managed-family",
            "email": "family@example.com",
        }])

    def test_token_watchdog_does_not_log_success_when_auth_json_sync_fails(self):
        from antigravity_auth.auth_sync import AuthSyncResult
        from antigravity_auth.storage import save_accounts
        from antigravity_auth.token_watchdog import _refresh_if_needed

        save_accounts({
            "version": 4,
            "accounts": [{
                "email": "active@example.com",
                "refreshToken": "active-refresh",
                "projectId": "proj-active",
            }],
            "activeIndex": 0,
            "activeIndexByFamily": {"claude": 0, "gemini": 0},
        })

        fake_agent = types.ModuleType("agent")
        fake_google_oauth = types.ModuleType("agent.google_oauth")
        setattr(fake_google_oauth, "load_credentials", lambda: type("Creds", (), {
            "refresh_token": "stored-refresh",
            "expires_ms": 0,
        })())
        original_agent = sys.modules.get("agent")
        original_google_oauth = sys.modules.get("agent.google_oauth")
        sys.modules["agent"] = fake_agent
        sys.modules["agent.google_oauth"] = fake_google_oauth
        config = type("Config", (), {"proactive_refresh_buffer_seconds": 1800})()

        try:
            with patch("antigravity_auth.token.refresh_access_token", return_value={
                "access": "access-active",
                "refresh": "active-rotated|proj-active",
                "expires": 123,
            }), patch(
                "antigravity_auth.auth_sync.sync_token_to_all_auth_stores",
                return_value=AuthSyncResult(auth_json=False, google_oauth=True),
            ), self.assertLogs("antigravity_auth.token_watchdog", level="DEBUG") as logs:
                _refresh_if_needed(config)
        finally:
            if original_agent is None:
                sys.modules.pop("agent", None)
            else:
                sys.modules["agent"] = original_agent
            if original_google_oauth is None:
                sys.modules.pop("agent.google_oauth", None)
            else:
                sys.modules["agent.google_oauth"] = original_google_oauth

        output = "\n".join(logs.output)
        self.assertIn("could not sync auth.json", output)
        self.assertNotIn("Proactively refreshed token", output)
