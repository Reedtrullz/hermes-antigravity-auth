"""Tests for shared time helpers."""

from __future__ import annotations

from unittest.mock import patch

from antigravity_auth._time_utils import now_ms


def test_now_ms_returns_epoch_milliseconds():
  with patch("antigravity_auth._time_utils.time.time", return_value=1234.567):
    assert now_ms() == 1234567.0
