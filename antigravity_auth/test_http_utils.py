"""Tests for shared HTTP helpers."""

from __future__ import annotations

import gzip
from types import SimpleNamespace

from antigravity_auth._http_utils import decompress_response


def test_decompress_response_returns_original_body_without_gzip_header():
  body = b"plain response"
  response = SimpleNamespace(headers={})

  result = decompress_response(body, response)

  assert result is body


def test_decompress_response_decompresses_gzip_body():
  body = gzip.compress(b"compressed response")
  response = SimpleNamespace(headers={"Content-Encoding": "gzip"})

  assert decompress_response(body, response) == b"compressed response"


def test_decompress_response_accepts_gzip_in_encoding_list():
  body = gzip.compress(b"compressed response")
  response = SimpleNamespace(headers={"Content-Encoding": "br, gzip"})

  assert decompress_response(body, response) == b"compressed response"
