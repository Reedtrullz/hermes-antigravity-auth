"""Tests for the Hermes 0.17 Antigravity Cloud Code client."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch
import unittest

import httpx

from antigravity_auth.cloudcode_client import (
  AntigravityCloudCodeClient,
  AntigravityCloudCodeError,
  _fallback_translate_gemini_response,
  _translate_stream_event,
)


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

  def test_non_stream_request_normalizes_httpx_timeout_extension(self):
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
      request.read()
      captured.append(request)
      return httpx.Response(
        200,
        json={"response": {"candidates": [{"content": {"parts": [{"text": "ok"}]}}]}},
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
        model="gemini-3.5-flash-low",
        messages=[{"role": "user", "content": "hello"}],
        timeout=httpx.Timeout(connect=1.0, read=2.0, write=3.0, pool=4.0),
      )

    self.assertEqual(response.choices[0].message.content, "ok")
    self.assertEqual(
      captured[0].extensions["timeout"],
      {"connect": 1.0, "read": 2.0, "write": 3.0, "pool": 4.0},
    )

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

  def test_stream_parser_accepts_no_space_crlf_and_final_event_without_blank_line(self):
    payload = {
      "response": {
        "candidates": [{
          "content": {"role": "model", "parts": [{"text": "hello"}]},
          "finishReason": "STOP",
        }],
      },
    }
    bodies = [
      f"data:{json.dumps(payload)}\r\n\r\n",
      f"data:{json.dumps(payload)}",
    ]

    for body in bodies:
      with self.subTest(body=body):
        def handler(request: httpx.Request) -> httpx.Response:
          request.read()
          return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=body,
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

        self.assertEqual(chunks[0].choices[0].delta.content, "hello")

  def test_streaming_sse_error_includes_recovery_metadata(self):
    def handler(request: httpx.Request) -> httpx.Response:
      request.read()
      payload = {
        "error": {
          "message": "messages.2: tool_use id call_1 has no matching tool_result"
        }
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
      with self.assertRaises(AntigravityCloudCodeError) as caught:
        list(client.chat.completions.create(
          model="gemini-3.5-flash-low",
          messages=[{"role": "user", "content": "hello"}],
          stream=True,
        ))

    self.assertEqual(caught.exception.code, "antigravity_stream_event_error")
    self.assertEqual(caught.exception.details["recoveryType"], "tool_result_missing")
    self.assertEqual(caught.exception.details["messageIndex"], 2)
    self.assertEqual(caught.exception.details["statusCode"], 200)
    self.assertTrue(caught.exception.details["streaming"])

  def test_non_stream_retries_once_after_response_hook_marks_ready(self):
    for status in (401, 403, 429):
      with self.subTest(status=status):
        captured: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
          request.read()
          captured.append(request)
          if len(captured) == 1:
            return httpx.Response(status, json={"error": {"message": "retry me"}}, request=request)
          return httpx.Response(
            200,
            json={"response": {"candidates": [{"content": {"parts": [{"text": "ok"}]}}]}},
            request=request,
          )

        def fake_response_hook(response: httpx.Response) -> None:
          if response.status_code == status:
            response.request.extensions["antigravity_retry_ready"] = True

        def fake_request_hook(request: httpx.Request) -> None:
          self.assertNotIn("authorization", request.headers)
          request.headers["Authorization"] = "Bearer fresh-token"

        client = AntigravityCloudCodeClient(
          http_client=httpx.Client(transport=httpx.MockTransport(handler))
        )
        with patch(
          "antigravity_auth.cloudcode_client.get_config",
          return_value=SimpleNamespace(cli_first=False),
        ), patch(
          "antigravity_auth.cloudcode_client._select_request_account",
          return_value=_selected_account(),
        ), patch(
          "antigravity_auth.cloudcode_client._process_response_hooks",
          side_effect=fake_response_hook,
        ), patch(
          "antigravity_auth.interceptor._antigravity_request_hook",
          side_effect=fake_request_hook,
        ):
          response = client.chat.completions.create(
            model="gemini-3.5-flash-low",
            messages=[{"role": "user", "content": "hello"}],
          )

        self.assertEqual(response.choices[0].message.content, "ok")
        self.assertEqual(len(captured), 2)
        self.assertTrue(captured[1].extensions["antigravity_retry_attempted"])
        self.assertEqual(captured[1].extensions["antigravity_retry_original_status"], status)
        self.assertEqual(captured[1].headers["authorization"], "Bearer fresh-token")

  def test_non_stream_does_not_retry_without_retry_ready_marker(self):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
      request.read()
      calls.append(request)
      return httpx.Response(429, json={"error": {"message": "quota"}}, request=request)

    client = AntigravityCloudCodeClient(
      http_client=httpx.Client(transport=httpx.MockTransport(handler))
    )

    with patch(
      "antigravity_auth.cloudcode_client.get_config",
      return_value=SimpleNamespace(cli_first=False),
    ), patch(
      "antigravity_auth.cloudcode_client._select_request_account",
      return_value=_selected_account(),
    ), patch(
      "antigravity_auth.cloudcode_client._process_response_hooks",
      return_value=None,
    ):
      with self.assertRaises(AntigravityCloudCodeError):
        client.chat.completions.create(
          model="gemini-3.5-flash-low",
          messages=[{"role": "user", "content": "hello"}],
        )

    self.assertEqual(len(calls), 1)

  def test_streaming_error_does_not_replay_even_when_retry_ready(self):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
      request.read()
      calls.append(request)
      return httpx.Response(429, json={"error": {"message": "quota"}}, request=request)

    def fake_response_hook(response: httpx.Response) -> None:
      response.request.extensions["antigravity_retry_ready"] = True

    client = AntigravityCloudCodeClient(
      http_client=httpx.Client(transport=httpx.MockTransport(handler))
    )

    with patch(
      "antigravity_auth.cloudcode_client.get_config",
      return_value=SimpleNamespace(cli_first=False),
    ), patch(
      "antigravity_auth.cloudcode_client._select_request_account",
      return_value=_selected_account(),
    ), patch(
      "antigravity_auth.cloudcode_client._process_response_hooks",
      side_effect=fake_response_hook,
    ):
      with self.assertRaises(AntigravityCloudCodeError):
        list(client.chat.completions.create(
          model="gemini-3.5-flash-low",
          messages=[{"role": "user", "content": "hello"}],
          stream=True,
        ))

    self.assertEqual(len(calls), 1)

  def test_fallback_non_stream_translates_function_calls_and_reasoning(self):
    response = _fallback_translate_gemini_response(
      {
        "candidates": [{
          "content": {
            "parts": [
              {"text": "internal", "thoughtSignature": "sig"},
              {"functionCall": {"id": "call_1", "name": "mcp/query", "args": {"q": "hi"}}},
            ],
          },
          "finishReason": "FUNCTION_CALL",
        }],
      },
      model="gemini-3.5-flash-low",
    )

    message = response.choices[0].message
    self.assertIsNone(message.content)
    self.assertEqual(message.reasoning_content, "internal")
    self.assertEqual(message.tool_calls[0].id, "call_1")
    self.assertEqual(message.tool_calls[0].function.name, "mcp_query")
    self.assertEqual(message.tool_calls[0].function.arguments, '{"q":"hi"}')
    self.assertEqual(response.choices[0].finish_reason, "tool_calls")

  def test_fallback_stream_translates_function_call_delta(self):
    chunks = _translate_stream_event(
      {
        "candidates": [{
          "content": {
            "parts": [
              {"functionCall": {"id": "call_1", "name": "mcp/query", "args": {"q": "hi"}}},
            ],
          },
        }],
      },
      "gemini-3.5-flash-low",
      {},
    )

    tool_call = chunks[0].choices[0].delta.tool_calls[0]
    self.assertEqual(tool_call.index, 0)
    self.assertEqual(tool_call.id, "call_1")
    self.assertEqual(tool_call.function.name, "mcp_query")
    self.assertEqual(tool_call.function.arguments, '{"q":"hi"}')


if __name__ == "__main__":
  unittest.main()
