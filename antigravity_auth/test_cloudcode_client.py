"""Tests for the Hermes 0.17 Antigravity Cloud Code client."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch
import unittest

import httpx

from antigravity_auth.cloudcode_client import AntigravityCloudCodeClient


def _selected_account(project_id: str = "project-123") -> dict:
  return {
    "access": "access-token",
    "account": None,
    "account_index": 0,
    "account_identity": {
      "email": "test@example.com",
      "project_id": project_id,
      "managed_project_id": "",
    },
    "family": "gemini",
  }


class TestAntigravityCloudCodeClient(unittest.TestCase):

  def test_non_stream_request_builds_antigravity_envelope_and_response(self):
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
      request.read()
      captured.append(request)
      return httpx.Response(
        200,
        json={
          "response": {
            "candidates": [{
              "content": {"role": "model", "parts": [{"text": "hello back"}]},
              "finishReason": "STOP",
            }],
            "usageMetadata": {
              "promptTokenCount": 2,
              "candidatesTokenCount": 3,
              "totalTokenCount": 5,
            },
          },
        },
        request=request,
      )

    client = AntigravityCloudCodeClient(
      http_client=httpx.Client(transport=httpx.MockTransport(handler))
    )

    with patch(
      "antigravity_auth.cloudcode_client.get_config",
      return_value=SimpleNamespace(cli_first=False),
    ), patch(
      "antigravity_auth.cloudcode_client._select_request_account",
      return_value=_selected_account(),
    ):
      response = client.chat.completions.create(
        model="gemini-3.5-flash-high",
        messages=[
          {"role": "system", "content": "System text"},
          {"role": "user", "content": "hello"},
        ],
        tools=[{
          "type": "function",
          "function": {
            "name": "pick_mode",
            "description": "Pick a mode",
            "parameters": {
              "type": "object",
              "properties": {"mode": {"const": "web"}},
              "required": ["mode"],
            },
          },
        }],
        temperature=0,
        max_tokens=7,
      )

    self.assertEqual(response.choices[0].message.content, "hello back")
    self.assertEqual(response.usage.total_tokens, 5)
    self.assertEqual(str(captured[0].url), "https://cloudcode-pa.googleapis.com/v1internal:generateContent")
    self.assertEqual(captured[0].headers["authorization"], "Bearer access-token")
    body = json.loads(captured[0].content)
    self.assertEqual(body["project"], "project-123")
    self.assertEqual(body["model"], "gemini-3-flash-agent")
    self.assertEqual(body["request"]["contents"][0]["parts"][0]["text"], "hello")
    self.assertIn("systemInstruction", body["request"])
    rendered = json.dumps(body)
    self.assertNotIn('"const"', rendered)
    self.assertIn('"enum"', rendered)

  def test_omits_project_header_and_envelope_field_when_account_has_no_project(self):
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
      request.read()
      captured.append(request)
      return httpx.Response(
        200,
        json={"response": {"candidates": []}},
        request=request,
      )

    selected = _selected_account(project_id="")
    client = AntigravityCloudCodeClient(
      http_client=httpx.Client(transport=httpx.MockTransport(handler))
    )

    with patch(
      "antigravity_auth.cloudcode_client.get_config",
      return_value=SimpleNamespace(cli_first=False),
    ), patch(
      "antigravity_auth.cloudcode_client._select_request_account",
      return_value=selected,
    ):
      client.chat.completions.create(
        model="gemini-3.5-flash-low",
        messages=[{"role": "user", "content": "hello"}],
        max_tokens=1,
      )

    body = json.loads(captured[0].content)
    self.assertNotIn("project", body)
    self.assertNotIn("x-goog-user-project", captured[0].headers)

  def test_stream_request_returns_openai_like_chunks(self):
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
      request.read()
      captured.append(request)
      payload = {
        "response": {
          "candidates": [{
            "content": {"role": "model", "parts": [{"text": "hello"}]},
            "finishReason": "STOP",
          }],
          "usageMetadata": {
            "promptTokenCount": 1,
            "candidatesTokenCount": 1,
            "totalTokenCount": 2,
          },
        },
      }
      return httpx.Response(
        200,
        headers={"content-type": "text/event-stream"},
        content=f"data: {json.dumps(payload)}\n\n",
        request=request,
      )

    client = AntigravityCloudCodeClient(
      http_client=httpx.Client(transport=httpx.MockTransport(handler))
    )

    with patch(
      "antigravity_auth.cloudcode_client.get_config",
      return_value=SimpleNamespace(cli_first=False),
    ), patch(
      "antigravity_auth.cloudcode_client._select_request_account",
      return_value=_selected_account(),
    ):
      chunks = list(client.chat.completions.create(
        model="gemini-3.5-flash-low",
        messages=[{"role": "user", "content": "hello"}],
        stream=True,
      ))

    self.assertEqual(str(captured[0].url), "https://cloudcode-pa.googleapis.com/v1internal:streamGenerateContent?alt=sse")
    self.assertEqual(captured[0].headers["accept"], "text/event-stream")
    self.assertEqual(chunks[0].choices[0].delta.content, "hello")
    self.assertEqual(chunks[-1].choices[0].finish_reason, "stop")


if __name__ == "__main__":
  unittest.main()
