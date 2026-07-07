import asyncio
import json
import os
import sys
import types
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import httpx


class TestAntigravityCloudCodeClient(unittest.TestCase):
  def test_non_stream_request_uses_cloudcode_envelope_and_openai_response_shape(self):
    captured: list[httpx.Request] = []

    def build_gemini_request(**kwargs):
      messages = kwargs.get("messages") or []
      return {
        "contents": [{
          "role": "user",
          "parts": [{"text": messages[0]["content"]}],
        }],
        "generationConfig": {"maxOutputTokens": kwargs.get("max_tokens")},
      }

    def translate_gemini_response(payload, model):
      text = payload["candidates"][0]["content"]["parts"][0]["text"]
      return SimpleNamespace(
        choices=[SimpleNamespace(
          message=SimpleNamespace(content=text, tool_calls=None),
          finish_reason="stop",
        )],
        model=model,
      )

    def transport(request: httpx.Request) -> httpx.Response:
      captured.append(request)
      request.read()
      return httpx.Response(
        200,
        request=request,
        json={
          "response": {
            "candidates": [{
              "content": {"parts": [{"text": "hello from antigravity"}]},
              "finishReason": "STOP",
            }],
            "usageMetadata": {"totalTokenCount": 3},
          },
        },
      )

    native = types.ModuleType("agent.gemini_native_adapter")
    native.build_gemini_request = build_gemini_request
    native.translate_gemini_response = translate_gemini_response
    native.gemini_http_error = lambda response, body_text="": RuntimeError(body_text or response.text)
    native._iter_sse_events = lambda response: iter(())
    native.translate_stream_event = lambda event, model, tool_call_indices: []
    agent = types.ModuleType("agent")

    with patch.dict(sys.modules, {
      "agent": agent,
      "agent.gemini_native_adapter": native,
    }), patch.dict(os.environ, {
      "HERMES_GEMINI_PROJECT_ID": "project-1",
    }), patch(
      "antigravity_auth.cloudcode_client._wrap_antigravity_http_client",
      side_effect=lambda client: client,
    ):
      from antigravity_auth.cloudcode_client import AntigravityCloudCodeClient

      client = AntigravityCloudCodeClient(
        api_key="placeholder",
        base_url="cloudcode-pa://google",
        http_client=httpx.Client(transport=httpx.MockTransport(transport)),
      )
      response = client.chat.completions.create(
        model="gemini-2.5-flash",
        messages=[{"role": "user", "content": "hello"}],
        max_tokens=12,
      )

    self.assertEqual(response.choices[0].message.content, "hello from antigravity")
    self.assertEqual(len(captured), 1)
    request = captured[0]
    self.assertEqual(
      str(request.url),
      "https://cloudcode-pa.googleapis.com/v1internal:generateContent",
    )
    body = json.loads(request.content)
    self.assertEqual(body["project"], "project-1")
    self.assertEqual(body["model"], "gemini-2.5-flash")
    self.assertEqual(body["request"]["contents"][0]["parts"][0]["text"], "hello")

  def test_async_stream_and_close_match_hermes_facade(self):
    def build_gemini_request(**kwargs):
      return {"contents": kwargs.get("messages") or []}

    def translate_stream_event(event, model, tool_call_indices):
      return [SimpleNamespace(delta=event["delta"], model=model)]

    def transport(request: httpx.Request) -> httpx.Response:
      return httpx.Response(200, request=request, content=b"data: ok\n\n")

    native = types.ModuleType("agent.gemini_native_adapter")
    native.build_gemini_request = build_gemini_request
    native.translate_gemini_response = lambda payload, model: SimpleNamespace(model=model)
    native.gemini_http_error = lambda response, body_text="": RuntimeError(body_text or response.text)
    native._iter_sse_events = lambda response: iter([{"delta": "one"}, {"delta": "two"}])
    native.translate_stream_event = translate_stream_event
    agent = types.ModuleType("agent")

    with patch.dict(sys.modules, {
      "agent": agent,
      "agent.gemini_native_adapter": native,
    }), patch.dict(os.environ, {
      "HERMES_GEMINI_PROJECT_ID": "project-1",
    }), patch(
      "antigravity_auth.cloudcode_client._wrap_antigravity_http_client",
      side_effect=lambda client: client,
    ):
      from antigravity_auth.cloudcode_client import (
        AntigravityCloudCodeClient,
        AsyncAntigravityCloudCodeClient,
      )

      sync_client = AntigravityCloudCodeClient(
        api_key="placeholder",
        base_url="cloudcode-pa://google",
        http_client=httpx.Client(transport=httpx.MockTransport(transport)),
      )
      async_client = AsyncAntigravityCloudCodeClient(sync_client)

      async def collect_stream():
        stream = await async_client.chat.completions.create(
          model="gemini-2.5-flash",
          messages=[{"role": "user", "content": "hello"}],
          stream=True,
        )
        chunks = []
        async for chunk in stream:
          chunks.append(chunk.delta)
        await async_client.close()
        return chunks

      chunks = asyncio.run(collect_stream())

    self.assertEqual(chunks, ["one", "two"])
    self.assertTrue(sync_client.is_closed)


if __name__ == "__main__":
  unittest.main()
