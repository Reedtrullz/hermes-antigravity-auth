"""OpenAI-shaped client facade for Antigravity Cloud Code."""

from __future__ import annotations

import logging
import asyncio
from typing import Any, Iterator

import httpx

from .constants import ANTIGRAVITY_DEFAULT_PROJECT_ID
from .constants import ide_user_agent
from .endpoints import select_endpoint
from .transform.envelope import build_antigravity_envelope, build_antigravity_url


logger = logging.getLogger(__name__)

VIRTUAL_CLOUDCODE_BASE_URL = "cloudcode-pa://google"


def is_antigravity_cloudcode_base_url(base_url: Any) -> bool:
  normalized = str(base_url or "").strip().lower()
  return normalized.startswith("cloudcode-pa://") or "cloudcode-pa.googleapis.com" in normalized


def resolve_project_id() -> str:
  """Return the best local project id without making network calls."""
  import os

  for name in ("HERMES_GEMINI_PROJECT_ID", "GOOGLE_CLOUD_PROJECT", "GCLOUD_PROJECT"):
    value = os.getenv(name, "").strip()
    if value:
      return value

  try:
    from .storage import get_active_token_from_auth_json

    active = get_active_token_from_auth_json()
    project_id = str(active.get("project_id") or "").strip()
    if project_id:
      return project_id
  except Exception:
    pass

  try:
    from .storage import load_accounts, resolve_active_account_index

    data = load_accounts()
    accounts = data.get("accounts")
    if isinstance(accounts, list) and accounts:
      index = resolve_active_account_index(data)
      if 0 <= index < len(accounts) and isinstance(accounts[index], dict):
        project_id = str(accounts[index].get("projectId") or "").strip()
        if project_id:
          return project_id
  except Exception:
    pass

  return ANTIGRAVITY_DEFAULT_PROJECT_ID


def _wrap_antigravity_http_client(client: httpx.Client) -> httpx.Client:
  try:
    from .interceptor import _wrap_http_client

    return _wrap_http_client(client)
  except Exception as exc:
    logger.warning(
      "Could not wrap Antigravity HTTP client; requests may miss auth hooks: %s",
      exc,
    )
    return client


def _load_native_adapter() -> Any:
  try:
    from agent import gemini_native_adapter
  except Exception as exc:
    raise RuntimeError(
      "Antigravity Cloud Code transport requires Hermes' "
      "agent.gemini_native_adapter module."
    ) from exc
  return gemini_native_adapter


def _response_payload(body: dict[str, Any]) -> dict[str, Any]:
  response = body.get("response")
  if isinstance(response, dict):
    payload = dict(response)
    usage = body.get("usageMetadata") or body.get("usage_metadata")
    if usage and "usageMetadata" not in payload and "usage_metadata" not in payload:
      payload["usageMetadata"] = usage
    return payload
  return body


def _stream_payload(event: dict[str, Any]) -> dict[str, Any]:
  response = event.get("response")
  if isinstance(response, dict):
    payload = dict(response)
    usage = event.get("usageMetadata") or event.get("usage_metadata")
    if usage and "usageMetadata" not in payload and "usage_metadata" not in payload:
      payload["usageMetadata"] = usage
    return payload
  return event


def _read_streaming_error_body(response: httpx.Response) -> str:
  try:
    from agent.bounded_response import read_streaming_error_body

    return read_streaming_error_body(response)
  except Exception:
    try:
      response.read()
      return response.text
    except Exception:
      return ""


class _CloudCodeChatCompletions:
  def __init__(self, client: "AntigravityCloudCodeClient") -> None:
    self._client = client

  def create(self, **kwargs: Any) -> Any:
    return self._client._create_chat_completion(**kwargs)


class _CloudCodeChatNamespace:
  def __init__(self, client: "AntigravityCloudCodeClient") -> None:
    self.completions = _CloudCodeChatCompletions(client)


class _AsyncCloudCodeChatCompletions:
  def __init__(self, client: "AsyncAntigravityCloudCodeClient") -> None:
    self._client = client

  async def create(self, **kwargs: Any) -> Any:
    return await self._client._create_chat_completion(**kwargs)


class _AsyncCloudCodeChatNamespace:
  def __init__(self, client: "AsyncAntigravityCloudCodeClient") -> None:
    self.completions = _AsyncCloudCodeChatCompletions(client)


class AntigravityCloudCodeClient:
  """Minimal OpenAI-SDK-compatible facade over Cloud Code generateContent."""

  def __init__(
    self,
    *,
    api_key: str = "antigravity-oauth",
    base_url: str = VIRTUAL_CLOUDCODE_BASE_URL,
    default_headers: dict[str, str] | None = None,
    timeout: Any = None,
    http_client: httpx.Client | None = None,
    **_: Any,
  ) -> None:
    self.api_key = api_key or "antigravity-oauth"
    self.base_url = base_url or VIRTUAL_CLOUDCODE_BASE_URL
    self._endpoint = self._resolve_endpoint(self.base_url)
    self._default_headers = dict(default_headers or {})
    self._timeout = timeout or httpx.Timeout(connect=15.0, read=600.0, write=30.0, pool=30.0)
    self._http = _wrap_antigravity_http_client(http_client or httpx.Client(timeout=self._timeout))
    self.chat = _CloudCodeChatNamespace(self)
    self.is_closed = False

  @staticmethod
  def _resolve_endpoint(base_url: str) -> str:
    normalized = str(base_url or "").strip().rstrip("/")
    if normalized.startswith("http://") or normalized.startswith("https://"):
      return normalized
    return select_endpoint()

  def _endpoint_for_request(self) -> str:
    if str(self.base_url or "").strip().lower().startswith(("http://", "https://")):
      return self._endpoint
    self._endpoint = self._resolve_endpoint(self.base_url)
    return self._endpoint

  def close(self) -> None:
    self.is_closed = True
    try:
      self._http.close()
    except Exception:
      pass

  def __enter__(self) -> "AntigravityCloudCodeClient":
    return self

  def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
    self.close()

  @staticmethod
  def _advance_stream_iterator(iterator: Iterator[Any]) -> tuple[bool, Any]:
    try:
      return False, next(iterator)
    except StopIteration:
      return True, None

  def _headers(self, *, streaming: bool = False) -> dict[str, str]:
    headers = {
      "Content-Type": "application/json",
      "Accept": "text/event-stream" if streaming else "application/json",
      "Authorization": f"Bearer {self.api_key}",
      "User-Agent": ide_user_agent(),
    }
    headers.update(self._default_headers)
    return headers

  def _create_chat_completion(
    self,
    *,
    model: str = "gemini-3.5-flash",
    messages: list[dict[str, Any]] | None = None,
    stream: bool = False,
    tools: Any = None,
    tool_choice: Any = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
    top_p: float | None = None,
    stop: Any = None,
    extra_body: dict[str, Any] | None = None,
    timeout: Any = None,
    **_: Any,
  ) -> Any:
    native = _load_native_adapter()
    thinking_config = None
    if isinstance(extra_body, dict):
      thinking_config = extra_body.get("thinking_config") or extra_body.get("thinkingConfig")

    request_payload = native.build_gemini_request(
      messages=messages or [],
      tools=tools,
      tool_choice=tool_choice,
      temperature=temperature,
      max_tokens=max_tokens,
      top_p=top_p,
      stop=stop,
      thinking_config=thinking_config,
    )
    envelope = build_antigravity_envelope(
      request_payload,
      model=model,
      project_id=resolve_project_id(),
    )

    if stream:
      return self._stream_completion(native, model=model, envelope=envelope, timeout=timeout)

    url = build_antigravity_url(self._endpoint_for_request(), model, action="generateContent", streaming=False)
    response = self._http.post(
      url,
      json=envelope,
      headers=self._headers(streaming=False),
      timeout=timeout or self._timeout,
    )
    if response.status_code != 200:
      raise native.gemini_http_error(response)
    try:
      body = response.json()
    except ValueError as exc:
      raise RuntimeError(f"Invalid JSON from Antigravity Cloud Code API: {exc}") from exc
    if not isinstance(body, dict):
      raise RuntimeError("Invalid Antigravity Cloud Code response: expected JSON object")
    return native.translate_gemini_response(_response_payload(body), model=model)

  def _stream_completion(
    self,
    native: Any,
    *,
    model: str,
    envelope: dict[str, Any],
    timeout: Any = None,
  ) -> Iterator[Any]:
    url = build_antigravity_url(self._endpoint_for_request(), model, action="streamGenerateContent", streaming=True)

    def _generator() -> Iterator[Any]:
      try:
        with self._http.stream(
          "POST",
          url,
          json=envelope,
          headers=self._headers(streaming=True),
          timeout=timeout or self._timeout,
        ) as response:
          if response.status_code != 200:
            body_text = _read_streaming_error_body(response)
            raise native.gemini_http_error(response, body_text=body_text)
          tool_call_indices: dict[str, dict[str, Any]] = {}
          for event in native._iter_sse_events(response):
            payload = _stream_payload(event)
            for chunk in native.translate_stream_event(payload, model, tool_call_indices):
              yield chunk
      except httpx.HTTPError as exc:
        raise RuntimeError(f"Antigravity Cloud Code streaming request failed: {exc}") from exc

    return _generator()


class AsyncAntigravityCloudCodeClient:
  """Async wrapper matching AsyncOpenAI.chat.completions.create()."""

  def __init__(self, sync_client: AntigravityCloudCodeClient) -> None:
    self._sync = sync_client
    self.api_key = sync_client.api_key
    self.base_url = sync_client.base_url
    self.chat = _AsyncCloudCodeChatNamespace(self)
    self._real_client = sync_client

  async def _create_chat_completion(self, **kwargs: Any) -> Any:
    stream = bool(kwargs.get("stream"))
    result = await asyncio.to_thread(self._sync.chat.completions.create, **kwargs)
    if not stream:
      return result

    async def _async_stream() -> Any:
      try:
        while True:
          done, chunk = await asyncio.to_thread(self._sync._advance_stream_iterator, result)
          if done:
            break
          yield chunk
      finally:
        close = getattr(result, "close", None)
        if callable(close):
          await asyncio.to_thread(close)

    return _async_stream()

  async def close(self) -> None:
    await asyncio.to_thread(self._sync.close)

  @property
  def is_closed(self) -> bool:
    return bool(getattr(self._sync, "is_closed", False))

  async def __aenter__(self) -> "AsyncAntigravityCloudCodeClient":
    return self

  async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
    await self.close()
