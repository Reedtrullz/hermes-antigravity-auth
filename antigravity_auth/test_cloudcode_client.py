import asyncio
import json
import os
import sys
import types
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import httpx

import antigravity_auth.cloudcode_client as cloudcode_client


class TestAntigravityCloudCodeClient(unittest.TestCase):
  def test_wrap_antigravity_http_client_warns_when_interceptor_wrap_fails(self):
    client = httpx.Client()
    try:
      with patch(
        "antigravity_auth.interceptor._wrap_http_client",
        side_effect=RuntimeError("wrap exploded"),
      ), self.assertLogs("antigravity_auth.cloudcode_client", level="WARNING") as logs:
        wrapped = cloudcode_client._wrap_antigravity_http_client(client)

      self.assertIs(wrapped, client)
      self.assertIn("Could not wrap Antigravity HTTP client", "\n".join(logs.output))
    finally:
      client.close()

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
      client = cloudcode_client.AntigravityCloudCodeClient(
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

  def test_virtual_cloudcode_endpoint_is_reselected_per_request(self):
    captured_urls: list[str] = []

    def build_gemini_request(**kwargs):
      return {"contents": kwargs.get("messages") or []}

    def translate_gemini_response(payload, model):
      return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="ok", tool_calls=None), finish_reason="stop")],
        model=model,
      )

    def transport(request: httpx.Request) -> httpx.Response:
      captured_urls.append(str(request.url))
      request.read()
      return httpx.Response(
        200,
        request=request,
        json={"response": {"candidates": [{"content": {"parts": [{"text": "ok"}]}}]}},
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
    ), patch(
      "antigravity_auth.cloudcode_client.select_endpoint",
      side_effect=[
        "https://init.example.test",
        "https://first.example.test",
        "https://second.example.test",
      ],
    ):
      client = cloudcode_client.AntigravityCloudCodeClient(
        api_key="placeholder",
        base_url="cloudcode-pa://google",
        http_client=httpx.Client(transport=httpx.MockTransport(transport)),
      )
      client.chat.completions.create(model="gemini-2.5-flash", messages=[])
      client.chat.completions.create(model="gemini-2.5-flash", messages=[])
      client.close()

    self.assertEqual(captured_urls, [
      "https://first.example.test/v1internal:generateContent",
      "https://second.example.test/v1internal:generateContent",
    ])

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
      sync_client = cloudcode_client.AntigravityCloudCodeClient(
        api_key="placeholder",
        base_url="cloudcode-pa://google",
        http_client=httpx.Client(transport=httpx.MockTransport(transport)),
      )
      async_client = cloudcode_client.AsyncAntigravityCloudCodeClient(sync_client)

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

  def test_async_stream_aclose_closes_sync_iterator(self):
    class ClosableIterator:
      def __init__(self):
        self.closed = False
        self._items = iter(["one", "two"])

      def __iter__(self):
        return self

      def __next__(self):
        return next(self._items)

      def close(self):
        self.closed = True

    result = ClosableIterator()

    class FakeSyncClient:
      def __init__(self):
        self.api_key = "placeholder"
        self.base_url = "cloudcode-pa://google"
        self.is_closed = False
        self.chat = SimpleNamespace(
          completions=SimpleNamespace(create=lambda **kwargs: result),
        )

      def _advance_stream_iterator(self, iterator):
        try:
          return False, next(iterator)
        except StopIteration:
          return True, None

      def close(self):
        self.is_closed = True

    async def read_one_and_close():
      sync_client = FakeSyncClient()
      async_client = cloudcode_client.AsyncAntigravityCloudCodeClient(sync_client)
      stream = await async_client.chat.completions.create(stream=True)
      first = await stream.__anext__()
      await stream.aclose()
      self.assertFalse(async_client.is_closed)
      async with async_client:
        pass
      self.assertTrue(async_client.is_closed)
      return first

    first = asyncio.run(read_one_and_close())

    self.assertEqual(first, "one")
    self.assertTrue(result.closed)


if __name__ == "__main__":
  unittest.main()
