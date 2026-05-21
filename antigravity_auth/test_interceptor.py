"""Tests for the HTTP interceptor — headers-only request hook."""

import json
import unittest
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
