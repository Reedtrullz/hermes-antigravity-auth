"""Offline self-test for the Antigravity transform and packaging path."""

from __future__ import annotations

import json
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import __version__
from .install_plugins import install_plugins
from .transform import (
  build_antigravity_envelope,
  build_antigravity_url,
  resolve_model_for_header_style,
  transform_antigravity_response,
  transform_messages_to_contents,
)


@dataclass(frozen=True)
class SelftestRow:
  status: str
  check: str
  detail: str


def _ok(check: str, detail: str) -> SelftestRow:
  return SelftestRow("PASS", check, detail)


def _fail(check: str, detail: str) -> SelftestRow:
  return SelftestRow("FAIL", check, detail)


def _assert(condition: bool, message: str) -> None:
  if not condition:
    raise AssertionError(message)


def _sample_messages() -> list[dict[str, Any]]:
  return [
    {"role": "system", "content": "You are a concise self-test assistant."},
    {"role": "user", "content": "Check the Antigravity request path."},
    {
      "role": "assistant",
      "content": "",
      "tool_calls": [{
        "id": "call_selftest",
        "type": "function",
        "function": {
          "name": "inspect_install",
          "arguments": "{\"target\":\"hermes\"}",
        },
      }],
    },
    {
      "role": "tool",
      "tool_call_id": "call_selftest",
      "name": "inspect_install",
      "content": "{\"status\":\"ok\"}",
    },
    {"role": "user", "content": "Return selftest OK."},
  ]


def _check_request_round_trip() -> list[SelftestRow]:
  rows: list[SelftestRow] = []
  model = "antigravity-claude-sonnet-4-6"
  effective_model = resolve_model_for_header_style(model, "antigravity")

  try:
    contents, system_instruction = transform_messages_to_contents(_sample_messages())
    _assert(system_instruction is not None, "system instruction was not extracted")
    has_tool_call = any(
      part.get("functionCall", {}).get("name") == "inspect_install"
      for item in contents
      for part in item.get("parts", [])
    )
    has_tool_response = any(
      part.get("functionResponse", {}).get("id") == "call_selftest"
      for item in contents
      for part in item.get("parts", [])
    )
    _assert(has_tool_call, "tool call was not converted")
    _assert(has_tool_response, "tool response was not converted")
    rows.append(_ok(
      "message transform",
      "OpenAI messages converted to Gemini contents with tool call/response parts",
    ))
  except Exception as exc:
    return [_fail("message transform", str(exc))]

  try:
    envelope = build_antigravity_envelope(
      {
        "contents": contents,
        "system_instruction": system_instruction,
      },
      model=effective_model,
      project_id="selftest-project",
      header_style="antigravity",
    )
    _assert(envelope["project"] == "selftest-project", "project missing from envelope")
    _assert(envelope["model"] == "claude-sonnet-4-6", "effective model missing from envelope")
    _assert(envelope.get("requestType") == "agent", "Antigravity requestType missing")
    _assert("systemInstruction" in envelope["request"], "systemInstruction missing from request")
    _assert("system_instruction" not in envelope["request"], "snake_case system_instruction leaked into request")
    rows.append(_ok(
      "request envelope",
      "Antigravity envelope built with project, model, requestType, and systemInstruction",
    ))
  except Exception as exc:
    return rows + [_fail("request envelope", str(exc))]

  try:
    url = build_antigravity_url("https://example.invalid", effective_model, streaming=True)
    _assert(
      url == "https://example.invalid/v1internal:streamGenerateContent?alt=sse",
      f"unexpected URL: {url}",
    )
    rows.append(_ok("request url", "Streaming Antigravity URL shape is valid"))
  except Exception as exc:
    return rows + [_fail("request url", str(exc))]

  return rows


def _check_response_round_trip() -> list[SelftestRow]:
  body = json.dumps({
    "response": {
      "candidates": [{
        "content": {
          "parts": [{"text": "selftest OK"}],
        },
      }],
      "usageMetadata": {
        "promptTokenCount": 7,
        "candidatesTokenCount": 3,
        "totalTokenCount": 10,
      },
    },
  })
  try:
    transformed, headers, error = transform_antigravity_response(
      body,
      streaming=False,
      status_code=200,
      headers={"content-type": "application/json"},
      requested_model="antigravity-claude-sonnet-4-6",
      effective_model="claude-sonnet-4-6",
      project_id="selftest-project",
      endpoint="https://example.invalid",
    )
    _assert(error is None, f"unexpected recovery error: {error}")
    parsed = json.loads(transformed)
    text = parsed["candidates"][0]["content"]["parts"][0]["text"]
    _assert(text == "selftest OK", f"unexpected response text: {text}")
    _assert(
      headers is not None and headers.get("x-antigravity-total-token-count") == "10",
      "usage headers missing",
    )
    return [_ok("response transform", "Antigravity response unwrapped and usage headers extracted")]
  except Exception as exc:
    return [_fail("response transform", str(exc))]


def _check_plugin_manifest_generation() -> list[SelftestRow]:
  try:
    with tempfile.TemporaryDirectory() as tmpdir:
      root = Path(tmpdir)
      install_plugins(root)
      for rel in (
        "plugins/antigravity-cli/plugin.yaml",
        "plugins/model-providers/antigravity/plugin.yaml",
      ):
        text = (root / rel).read_text(encoding="utf-8")
        _assert(
          f"version: {__version__}" in text,
          f"{rel} does not use package version {__version__}",
        )
    return [_ok("plugin manifests", f"Generated wrappers use package version {__version__}")]
  except Exception as exc:
    return [_fail("plugin manifests", str(exc))]


def _check_packaging_guard(root: str | Path | None = None) -> list[SelftestRow]:
  try:
    from .packaging_guard import assert_no_local_credentials_module
    assert_no_local_credentials_module(root)
    return [_ok("packaging guard", "No local credential module would be included in a wheel/sdist")]
  except Exception as exc:
    return [_fail("packaging guard", str(exc))]


def run_selftest(*, packaging_root: str | Path | None = None) -> list[SelftestRow]:
  """Run offline round-trip checks without credentials, network, or Hermes."""
  rows: list[SelftestRow] = []
  rows.extend(_check_request_round_trip())
  rows.extend(_check_response_round_trip())
  rows.extend(_check_plugin_manifest_generation())
  rows.extend(_check_packaging_guard(packaging_root))
  return rows


def format_selftest_rows(rows: list[SelftestRow]) -> str:
  lines = ["Antigravity selftest"]
  for row in rows:
    lines.append(f"{row.status:<4} {row.check}: {row.detail}")
  if all(row.status == "PASS" for row in rows):
    lines.append("Result: PASS")
  else:
    lines.append("Result: FAIL")
  return "\n".join(lines)


def print_selftest() -> bool:
  rows = run_selftest()
  print(format_selftest_rows(rows))
  return all(row.status == "PASS" for row in rows)


def main() -> None:
  sys.exit(0 if print_selftest() else 1)


if __name__ == "__main__":
  main()
