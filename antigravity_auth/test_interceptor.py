"""Tests for the HTTP interceptor module."""

import json
import unittest

import httpx


class TestRequestHook(unittest.TestCase):
    """Test the Antigravity request hook transforms Code Assist -> Antigravity."""

    @classmethod
    def setUpClass(cls):
        """Patch httpx.Request.content to be writable (read-only in httpx >=0.28)."""
        content_property = httpx.Request.__dict__["content"]
        if content_property.fset is None:
            httpx.Request.content = property(
                content_property.fget,
                lambda self, v: setattr(self, "_content", v),
                content_property.fdel,
                content_property.__doc__,
            )

    def setUp(self):
        from antigravity_auth.interceptor import _antigravity_request_hook
        self.hook = _antigravity_request_hook

    def test_transforms_code_assist_envelope(self):
        """A Code Assist envelope should be rewritten to Antigravity format."""
        code_assist_body = {
            "project": "test-project",
            "model": "gemini-3-flash-preview",
            "user_prompt_id": "abc123",
            "request": {
                "contents": [{"role": "user", "parts": [{"text": "Hello"}]}],
            },
        }
        request = httpx.Request(
            "POST",
            "https://cloudcode-pa.googleapis.com/v1internal:generateContent",
            content=json.dumps(code_assist_body).encode("utf-8"),
        )

        self.hook(request)

        new_body = json.loads(request.content)
        self.assertIn("requestType", new_body, "Should add requestType field")
        self.assertEqual(new_body["requestType"], "agent")
        self.assertIn("userAgent", new_body, "Should add userAgent field")
        self.assertIn("requestId", new_body, "Should add requestId field")
        self.assertIn("request", new_body, "Should preserve inner request")
        # systemInstruction should be injected when absent
        inner = new_body["request"]
        self.assertIn("systemInstruction", inner,
                       "Should inject systemInstruction for Antigravity")

    def test_rewrites_headers(self):
        """Headers should be replaced with Antigravity headers."""
        request = httpx.Request(
            "POST",
            "https://cloudcode-pa.googleapis.com/v1internal:generateContent",
            content=json.dumps({
                "project": "test",
                "model": "gemini-3-flash-preview",
                "request": {"contents": []},
            }).encode("utf-8"),
            headers={
                "User-Agent": "hermes-agent",
                "Authorization": "Bearer token",
            },
        )

        self.hook(request)

        # Old Hermes headers should be gone
        ua = request.headers.get("User-Agent", "")
        self.assertNotIn("hermes-agent", ua, "Hermes UA should be replaced")
        # Client-Metadata should be present (Antigravity-style)
        self.assertIn("Client-Metadata", request.headers,
                       "Should add Client-Metadata header")

    def test_ignores_non_cloudcode_urls(self):
        """Non-Cloud Code URLs should pass through unchanged."""
        original = b'{"key": "value"}'
        request = httpx.Request(
            "POST", "https://example.com/api",
            content=original,
        )
        self.hook(request)
        self.assertEqual(request.content, original,
                         "Non-cloudcode requests should not be modified")

    def test_ignores_non_envelope_bodies(self):
        """Bodies that don't look like envelopes should pass through."""
        original = b'{"messages": [{"role": "user", "content": "hi"}]}'
        request = httpx.Request(
            "POST",
            "https://cloudcode-pa.googleapis.com/v1internal:generateContent",
            content=original,
        )
        self.hook(request)
        self.assertEqual(request.content, original,
                         "Non-envelope bodies should pass through unchanged")

    def test_preserves_project_and_model(self):
        """Project ID and model name should be preserved in the envelope."""
        code_assist_body = {
            "project": "my-gcp-project-123",
            "model": "claude-sonnet-4-6",
            "user_prompt_id": "test-123",
            "request": {
                "contents": [
                    {"role": "user", "parts": [{"text": "Hi"}]}
                ],
            },
        }
        request = httpx.Request(
            "POST",
            "https://cloudcode-pa.googleapis.com/v1internal:generateContent",
            content=json.dumps(code_assist_body).encode("utf-8"),
        )

        self.hook(request)

        new_body = json.loads(request.content)
        self.assertEqual(new_body["project"], "my-gcp-project-123")
        self.assertEqual(new_body["model"], "claude-sonnet-4-6")
