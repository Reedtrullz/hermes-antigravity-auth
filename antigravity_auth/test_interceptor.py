"""Tests for the HTTP interceptor — headers-only request hook."""

import json
import os
import sys
import tempfile
import types
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
