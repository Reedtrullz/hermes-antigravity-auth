"""Regression tests: loadCodeAssist project-discovery payload compatibility.

Google's ``ClientMetadata`` proto rejects per-OS ``platform`` values such as
``"MACOS"``/``"WINDOWS"`` (HTTP 400 ``INVALID_ARGUMENT``), which made
``fetch_project_id`` silently fail on macOS (it skips non-200 endpoints and
returns ``""``). The payload must use ``PLATFORM_UNSPECIFIED`` — accepted on
every platform — so discovery behaves identically on Linux, macOS, Windows.
"""

import sys
import unittest
from unittest.mock import patch

from antigravity_auth.oauth import fetch_project_id, load_code_assist_payload


class TestLoadCodeAssistPayload(unittest.TestCase):
  """The discovery payload must be platform-independent and API-accepted."""

  def test_platform_is_unspecified(self) -> None:
    payload = load_code_assist_payload()
    self.assertEqual(payload["metadata"]["platform"], "PLATFORM_UNSPECIFIED")
    self.assertEqual(payload["metadata"]["ideType"], "ANTIGRAVITY")
    self.assertEqual(payload["metadata"]["pluginType"], "GEMINI")

  def test_payload_identical_on_all_platforms(self) -> None:
    """Linux / macOS / Windows must all send the same body (no regression)."""
    expected = load_code_assist_payload()
    for platform in ("linux", "darwin", "win32"):
      with patch.object(sys, "platform", platform):
        self.assertEqual(load_code_assist_payload(), expected)

  def test_fetch_uses_compatible_payload(self) -> None:
    """fetch_project_id must POST the compatible payload (not per-OS)."""
    seen: list = []
    import antigravity_auth.oauth as _oauth

    def fake_post(url, headers, data, timeout=10):
      import json as _json
      seen.append((url, _json.loads(data.decode("utf-8"))))
      return 200, b'{"cloudaicompanionProject": "test-project-123"}'

    with patch.object(_oauth, "make_post_request", side_effect=fake_post):
      self.assertEqual(fetch_project_id("dummy-token"), "test-project-123")
    self.assertTrue(seen)
    for _, payload in seen:
      self.assertEqual(payload["metadata"]["platform"], "PLATFORM_UNSPECIFIED")
