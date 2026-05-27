"""Tests for the HTTP interceptor — headers-only request hook."""

import json
import os
import tempfile
import unittest
from unittest.mock import patch

import httpx


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

    def test_preserves_authorization(self):
        r = self._make_request()
        self.hook(r)
        self.assertIn("Bearer test", r.headers.get("Authorization", ""))

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
            "antigravity_auth.interceptor.build_antigravity_headers",
            return_value={"User-Agent": "antigravity-test"},
        ) as build_headers:
            self.hook(r)
        build_headers.assert_called_once_with(header_style="antigravity")

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

    def test_request_hook_preserves_authorization_when_sync_reports_failure(self):
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

        self.assertEqual(r.headers["Authorization"], "Bearer test")
        self.assertIsNone(fake_mgr.marked_index)
        self.assertFalse(fake_mgr.saved)

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

    def test_401_syncs_rotated_refresh_token_to_google_oauth(self):
        from antigravity_auth.interceptor import _antigravity_response_hook
        from antigravity_auth.storage import save_accounts

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

        with patch("antigravity_auth.token.refresh_access_token", return_value={
            "access": "new-access",
            "refresh": "new-refresh|proj-1",
            "expires": 123,
        }), patch("antigravity_auth.auth_sync.sync_token_to_google_oauth", side_effect=lambda **kw: synced.append(kw) or True):
            _antigravity_response_hook(response)

        self.assertEqual(synced[0]["refresh_token"], "new-refresh|proj-1")
