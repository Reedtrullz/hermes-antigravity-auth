"""Regression tests: JSON body rewrites must keep httpx request internals in sync.

Background: ``_replace_request_json`` rewrites the request body in the httpx
request hook (model remap, Claude transforms). httpx 0.28.x sends the
pre-encoded body via ``request.stream``, not ``request._content`` — updating
only ``_content``/``Content-Length`` makes h11 fail the send with
``Too little data for declared Content-Length`` whenever the rewritten body
is larger than the original (exactly what the Claude transforms do).
"""

import json
import unittest

import httpx

from antigravity_auth.interceptor import _replace_request_json


def _stream_bytes(request: httpx.Request) -> bytes:
  chunks = [chunk for chunk in request.stream]
  return b"".join(chunks)


class TestReplaceRequestJsonStreamSync(unittest.TestCase):
  """content bytes, stream bytes and Content-Length must always agree."""

  def _check_invariants(self, request: httpx.Request, expected: dict) -> None:
    expected_bytes = json.dumps(expected, separators=(",", ":")).encode("utf-8")
    self.assertEqual(request.content, expected_bytes)
    self.assertEqual(_stream_bytes(request), expected_bytes)
    self.assertEqual(request.headers["Content-Length"], str(len(expected_bytes)))
    # request.read() must also see the rewritten body (hook re-reads it).
    self.assertEqual(json.loads(request.read()), expected)

  def test_enlarged_body_stays_in_sync(self) -> None:
    """Growing the body (the Claude-transform direction) must not desync."""
    request = httpx.Request("POST", "https://example.test/v1", json={"a": 1})
    body = {"a": 1, "request": {"model": "m", "padding": "x" * 500}}
    _replace_request_json(request, body)
    self._check_invariants(request, body)

  def test_shrunk_body_stays_in_sync(self) -> None:
    request = httpx.Request(
      "POST", "https://example.test/v1", json={"a": 1, "big": "y" * 500}
    )
    body = {"a": 1}
    _replace_request_json(request, body)
    self._check_invariants(request, body)

  def test_stream_matches_declared_length(self) -> None:
    """The exact invariant h11 enforces on the wire."""
    request = httpx.Request("POST", "https://example.test/v1", json={"a": 1})
    body = {"a": 1, "request": {"tool_call_ids": ["id-1", "id-2"], "blob": "z" * 300}}
    _replace_request_json(request, body)
    declared = int(request.headers["Content-Length"])
    self.assertEqual(len(_stream_bytes(request)), declared)
