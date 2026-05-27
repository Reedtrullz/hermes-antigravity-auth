"""Tests for the HTTP interceptor — headers-only request hook."""

import json
import os
import tempfile
import unittest
from unittest.mock import patch

import httpx


class TestRequestHook(unittest.TestCase):

    def setUp(self):
        from antigravity_auth.interceptor import _antigravity_request_hook
        self.hook = _antigravity_request_hook

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
