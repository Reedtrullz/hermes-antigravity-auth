"""Shared HTTP utility helpers."""
from __future__ import annotations

import gzip


def decompress_response(body: bytes, response) -> bytes:
    """Decompress gzip-encoded HTTP response bodies."""
    encoding = response.headers.get("Content-Encoding", "")
    if "gzip" in encoding:
        return gzip.decompress(body)
    return body
