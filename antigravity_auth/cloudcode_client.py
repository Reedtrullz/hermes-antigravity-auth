"""OpenAI-compatible Antigravity Cloud Code client for Hermes 0.17+."""

from __future__ import annotations

import json
import logging
import time
import uuid
import asyncio
from types import SimpleNamespace
from typing import Any, Iterator

import httpx

from .config import get_config
from .endpoints import mark_endpoint_failed, select_endpoint
from .fingerprint import (
  build_fingerprint_headers,
  generate_fingerprint,
  update_fingerprint_version,
)
from .redaction import redact_secret_text
from .transform.envelope import (
  build_antigravity_envelope,
  build_antigravity_headers,
  resolve_model_for_header_style,
)
from .transform.messages import transform_messages_to_contents
from .transform.schema import clean_json_schema

logger = logging.getLogger(__name__)


class AntigravityCloudCodeError(Exception):
  """Provider error shape compatible with Hermes' retry/error reporting."""

  def __init__(
    self,
    message: str,
    *,
    code: str = "antigravity_cloudcode_error",
    status_code: int | None = None,
    response: httpx.Response | None = None,
    retry_after: float | None = None,
    details: dict[str, Any] | None = None,
  ) -> None:
    super().__init__(message)
    self.code = code
    self.status_code = status_code
    self.response = response
    self.retry_after = retry_after
    self.details = details or {}
    self.body = {"error": {"message": message, "code": code, "type": "error"}}


class _AntigravityChatCompletions:
  def __init__(self, client: "AntigravityCloudCodeClient") -> None:
    self._client = client

  def create(self, **kwargs: Any) -> Any:
    return self._client._create_chat_completion(**kwargs)


class _AntigravityChatNamespace:
  def __init__(self, client: "AntigravityCloudCodeClient") -> None:
    self.completions = _AntigravityChatCompletions(client)


class _AsyncAntigravityChatCompletions:
  def __init__(self, client: "AsyncAntigravityCloudCodeClient") -> None:
    self._client = client

  async def create(self, **kwargs: Any) -> Any:
    return await self._client._create_chat_completion(**kwargs)


class _AsyncAntigravityChatNamespace:
  def __init__(self, client: "AsyncAntigravityCloudCodeClient") -> None:
    self.completions = _AsyncAntigravityChatCompletions(client)


class AntigravityCloudCodeClient:
  """Minimal OpenAI SDK facade over Antigravity's Cloud Code endpoint."""

  def __init__(
    self,
    *,
    api_key: str = "",
    base_url: str | None = None,
    default_headers: dict[str, str] | None = None,
    timeout: Any = None,
    http_client: httpx.Client | None = None,
    **_: Any,
  ) -> None:
    self.api_key = api_key or ""
    self.base_url = base_url or "cloudcode-pa://google"
    self._default_headers = dict(default_headers or {})
    self.chat = _AntigravityChatNamespace(self)
    self.is_closed = False
    self._http = http_client or httpx.Client(
      timeout=timeout or httpx.Timeout(connect=15.0, read=600.0, write=30.0, pool=30.0)
    )

  def close(self) -> None:
    self.is_closed = True
    try:
      self._http.close()
    except Exception:
      pass

  def __enter__(self):
    return self

  def __exit__(self, exc_type, exc_val, exc_tb):
    self.close()

  @staticmethod
  def _advance_stream_iterator(iterator: Iterator[Any]) -> tuple[bool, Any | None]:
    try:
      return False, next(iterator)
    except StopIteration:
      return True, None

  def _create_chat_completion(
    self,
    *,
    model: str = "gemini-3.5-flash-low",
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
    config = get_config()
    cli_first = bool(getattr(config, "cli_first", False))
    header_style = _select_header_style_for_model(model, cli_first)
    effective_model = resolve_model_for_header_style(model, header_style)
    selected = _select_request_account(effective_model, header_style, config)
    if not selected or not selected.get("access"):
      raise AntigravityCloudCodeError(
        "No usable Antigravity OAuth account could be selected. "
        "Run `hermes antigravity accounts` and `hermes antigravity doctor`.",
        code="antigravity_no_account",
      )

    thinking_config = None
    if isinstance(extra_body, dict):
      thinking_config = extra_body.get("thinking_config") or extra_body.get("thinkingConfig")
    request_payload = _build_gemini_request(
      messages=messages or [],
      tools=tools,
      tool_choice=tool_choice,
      temperature=temperature,
      max_tokens=max_tokens,
      top_p=top_p,
      stop=stop,
      thinking_config=thinking_config,
    )
    transform_model = f"{model} {effective_model}"
    if "claude" in transform_model.lower():
      _inject_tool_call_ids(request_payload)
      _apply_claude_transforms(request_payload)

    project_id = _project_id_from_selection(selected)
    envelope = build_antigravity_envelope(
      request_payload=request_payload,
      model=effective_model,
      project_id=project_id or "",
      header_style=header_style,
    )
    if not project_id:
      envelope.pop("project", None)
    headers = _build_headers(
      selected=selected,
      header_style=header_style,
      project_id=project_id,
      streaming=stream,
      default_headers=self._default_headers,
    )
    endpoint = select_endpoint(config)
    action = "streamGenerateContent" if stream else "generateContent"
    suffix = "?alt=sse" if stream else ""
    url = f"{endpoint}/v1internal:{action}{suffix}"

    request = _make_request(
      url=url,
      envelope=envelope,
      headers=headers,
      selected=selected,
      model=effective_model,
      header_style=header_style,
    )
    if stream:
      return self._stream_completion(
        request=request,
        model=effective_model,
        endpoint=endpoint,
        timeout=timeout,
      )
    return self._non_stream_completion(
      request=request,
      model=effective_model,
      endpoint=endpoint,
      timeout=timeout,
    )

  def _non_stream_completion(
    self,
    *,
    request: httpx.Request,
    model: str,
    endpoint: str,
    timeout: Any = None,
  ) -> Any:
    if timeout is not None:
      request.extensions["timeout"] = timeout
    try:
      response = self._http.send(request)
    except httpx.HTTPError as exc:
      raise AntigravityCloudCodeError(
        f"Antigravity Cloud Code request failed: {exc}",
        code="antigravity_request_error",
      ) from exc
    _process_response_hooks(response)
    if response.status_code >= 500:
      mark_endpoint_failed(endpoint)
    if response.status_code != 200:
      _raise_http_error(response, model=model, endpoint=endpoint)
    payload = _read_json(response)
    response_payload = _unwrap_response_payload(payload)
    return _translate_gemini_response(response_payload, model=model)

  def _stream_completion(
    self,
    *,
    request: httpx.Request,
    model: str,
    endpoint: str,
    timeout: Any = None,
  ) -> Iterator[Any]:
    if timeout is not None:
      request.extensions["timeout"] = timeout

    def _generator() -> Iterator[Any]:
      try:
        response = self._http.send(request, stream=True)
      except httpx.HTTPError as exc:
        raise AntigravityCloudCodeError(
          f"Antigravity Cloud Code streaming request failed: {exc}",
          code="antigravity_stream_error",
        ) from exc
      try:
        if response.status_code >= 500:
          mark_endpoint_failed(endpoint)
        if response.status_code != 200:
          response.read()
          _process_response_hooks(response)
          _raise_http_error(response, model=model, endpoint=endpoint)
        _process_response_hooks(response)
        tool_call_indices: dict[str, dict[str, Any]] = {}
        for event in _iter_sse_events(response):
          if isinstance(event.get("error"), dict):
            message = _error_message_from_body(event)
            raise AntigravityCloudCodeError(
              message,
              code="antigravity_stream_event_error",
              status_code=response.status_code,
              response=response,
            )
          event_payload = _unwrap_response_payload(event)
          for chunk in _translate_stream_event(event_payload, model, tool_call_indices):
            yield chunk
      finally:
        response.close()

    return _generator()


class AsyncAntigravityCloudCodeClient:
  """Async wrapper used by Hermes auxiliary-client callers."""

  def __init__(self, sync_client: AntigravityCloudCodeClient) -> None:
    self._sync = sync_client
    self._real_client = sync_client
    self.api_key = sync_client.api_key
    self.base_url = sync_client.base_url
    self.chat = _AsyncAntigravityChatNamespace(self)

  async def _create_chat_completion(self, **kwargs: Any) -> Any:
    stream = bool(kwargs.get("stream"))
    result = await asyncio.to_thread(self._sync.chat.completions.create, **kwargs)
    if not stream:
      return result

    async def _async_stream() -> Any:
      while True:
        done, chunk = await asyncio.to_thread(self._sync._advance_stream_iterator, result)
        if done:
          break
        yield chunk

    return _async_stream()

  async def close(self) -> None:
    await asyncio.to_thread(self._sync.close)


def _select_header_style_for_model(model: str, cli_first: bool) -> str:
  from .interceptor import _select_header_style_for_model as select_header_style
  return select_header_style(model, cli_first)


def _select_request_account(model: str, header_style: str, config: Any) -> dict[str, Any] | None:
  from .interceptor import _select_request_account as select_request_account
  return select_request_account(model, header_style, config)


def _inject_tool_call_ids(inner_request: dict[str, Any]) -> None:
  from .interceptor import _inject_tool_call_ids as inject_tool_call_ids
  inject_tool_call_ids(inner_request)


def _apply_claude_transforms(inner_request: dict[str, Any]) -> None:
  from .interceptor import _apply_claude_transforms as apply_claude_transforms
  apply_claude_transforms(inner_request)


def _persist_managed_account_state(account: Any) -> bool:
  from .interceptor import _persist_managed_account_state as persist_managed_account_state
  return persist_managed_account_state(account)


def _project_id_from_selection(selected: dict[str, Any]) -> str | None:
  identity = selected.get("account_identity")
  if isinstance(identity, dict):
    for key in ("project_id", "managed_project_id"):
      value = identity.get(key)
      if isinstance(value, str) and value:
        return value
  account = selected.get("account")
  parts = getattr(account, "refresh_parts", None)
  for value in (
    getattr(parts, "project_id", None),
    getattr(parts, "managed_project_id", None),
  ):
    if isinstance(value, str) and value:
      return value
  return None


def _build_headers(
  *,
  selected: dict[str, Any],
  header_style: str,
  project_id: str,
  streaming: bool,
  default_headers: dict[str, str],
) -> dict[str, str]:
  headers: dict[str, str] = {}
  account = selected.get("account")
  if account is not None:
    try:
      fingerprint_changed = False
      fp = getattr(account, "fingerprint", None)
      if not fp:
        fp = generate_fingerprint()
        account.fingerprint = fp
        fingerprint_changed = True
      if isinstance(fp, dict):
        if update_fingerprint_version(fp):
          fingerprint_changed = True
        headers.update(build_fingerprint_headers(fp))
        cm = fp.get("clientMetadata")
        if cm:
          headers["Client-Metadata"] = json.dumps(cm, separators=(",", ":"))
        if fingerprint_changed:
          _persist_managed_account_state(account)
    except Exception as exc:
      logger.debug("Could not build persistent Antigravity fingerprint headers: %s", exc)
  if not headers:
    headers.update(build_antigravity_headers(header_style=header_style))
  headers.update(default_headers)
  headers["Authorization"] = f"Bearer {selected['access']}"
  headers["Content-Type"] = "application/json"
  headers["Accept"] = "text/event-stream" if streaming else "application/json"
  if project_id:
    headers["x-goog-user-project"] = project_id
  return headers


def _make_request(
  *,
  url: str,
  envelope: dict[str, Any],
  headers: dict[str, str],
  selected: dict[str, Any],
  model: str,
  header_style: str,
) -> httpx.Request:
  extensions: dict[str, Any] = {
    "antigravity_header_style": header_style,
    "antigravity_model_family": _model_family_for_model(model),
  }
  selected_index = selected.get("account_index")
  if type(selected_index) is int:
    extensions["antigravity_selected_account_index"] = selected_index
    extensions["antigravity_selected_account_identity"] = selected.get("account_identity")
  return httpx.Request(
    "POST",
    url,
    json=envelope,
    headers=headers,
    extensions=extensions,
  )


def _model_family_for_model(model: str) -> str:
  from .interceptor import _model_family_for_model as model_family_for_model
  return model_family_for_model(model)


def _process_response_hooks(response: httpx.Response) -> None:
  try:
    from .interceptor import _antigravity_response_hook
    _antigravity_response_hook(response)
  except Exception as exc:
    logger.debug("Antigravity response hook failed in Cloud Code client: %s", exc)


def _build_gemini_request(
  *,
  messages: list[dict[str, Any]],
  tools: Any = None,
  tool_choice: Any = None,
  temperature: float | None = None,
  max_tokens: int | None = None,
  top_p: float | None = None,
  stop: Any = None,
  thinking_config: Any = None,
) -> dict[str, Any]:
  try:
    from agent.gemini_native_adapter import build_gemini_request
    return build_gemini_request(
      messages=messages,
      tools=tools,
      tool_choice=tool_choice,
      temperature=temperature,
      max_tokens=max_tokens,
      top_p=top_p,
      stop=stop,
      thinking_config=thinking_config,
    )
  except Exception:
    pass

  contents, system_instruction = transform_messages_to_contents(messages)
  request: dict[str, Any] = {"contents": contents}
  if system_instruction:
    request["systemInstruction"] = system_instruction
  gemini_tools = _translate_tools_to_gemini(tools)
  if gemini_tools:
    request["tools"] = gemini_tools
  tool_config = _translate_tool_choice_to_gemini(tool_choice)
  if tool_config:
    request["toolConfig"] = tool_config
  generation_config: dict[str, Any] = {}
  if temperature is not None:
    generation_config["temperature"] = temperature
  if max_tokens is not None:
    generation_config["maxOutputTokens"] = max_tokens
  if top_p is not None:
    generation_config["topP"] = top_p
  if stop:
    generation_config["stopSequences"] = stop if isinstance(stop, list) else [str(stop)]
  if isinstance(thinking_config, dict) and thinking_config:
    generation_config["thinkingConfig"] = dict(thinking_config)
  if generation_config:
    request["generationConfig"] = generation_config
  return request


def _translate_tools_to_gemini(tools: Any) -> list[dict[str, Any]]:
  if not isinstance(tools, list):
    return []
  declarations: list[dict[str, Any]] = []
  for tool in tools:
    if not isinstance(tool, dict):
      continue
    fn = tool.get("function") or {}
    if not isinstance(fn, dict):
      continue
    name = fn.get("name")
    if not isinstance(name, str) or not name:
      continue
    declaration: dict[str, Any] = {"name": name}
    description = fn.get("description")
    if isinstance(description, str) and description:
      declaration["description"] = description
    parameters = fn.get("parameters")
    if isinstance(parameters, dict):
      cleaned = clean_json_schema(parameters)
      if isinstance(cleaned, dict):
        declaration["parameters"] = cleaned
    declarations.append(declaration)
  return [{"functionDeclarations": declarations}] if declarations else []


def _translate_tool_choice_to_gemini(tool_choice: Any) -> dict[str, Any] | None:
  if tool_choice is None:
    return None
  if isinstance(tool_choice, str):
    if tool_choice == "auto":
      return {"functionCallingConfig": {"mode": "AUTO"}}
    if tool_choice == "required":
      return {"functionCallingConfig": {"mode": "ANY"}}
    if tool_choice == "none":
      return {"functionCallingConfig": {"mode": "NONE"}}
  if isinstance(tool_choice, dict):
    fn = tool_choice.get("function") or {}
    name = fn.get("name")
    if isinstance(name, str) and name:
      return {"functionCallingConfig": {"mode": "ANY", "allowedFunctionNames": [name]}}
  return None


def _read_json(response: httpx.Response) -> dict[str, Any]:
  try:
    parsed = response.json()
  except ValueError as exc:
    raise AntigravityCloudCodeError(
      f"Invalid JSON from Antigravity Cloud Code: {exc}",
      code="antigravity_invalid_json",
      status_code=response.status_code,
      response=response,
    ) from exc
  if not isinstance(parsed, dict):
    raise AntigravityCloudCodeError(
      "Invalid Antigravity Cloud Code response shape",
      code="antigravity_invalid_response",
      status_code=response.status_code,
      response=response,
    )
  return parsed


def _unwrap_response_payload(payload: dict[str, Any]) -> dict[str, Any]:
  response = payload.get("response")
  if isinstance(response, dict):
    return response
  return payload


def _translate_gemini_response(payload: dict[str, Any], model: str) -> Any:
  try:
    from agent.gemini_native_adapter import translate_gemini_response
    return translate_gemini_response(payload, model=model)
  except Exception:
    return _fallback_translate_gemini_response(payload, model=model)


def _fallback_translate_gemini_response(payload: dict[str, Any], model: str) -> SimpleNamespace:
  candidates = payload.get("candidates") or []
  candidate = candidates[0] if isinstance(candidates, list) and candidates and isinstance(candidates[0], dict) else {}
  content = candidate.get("content") if isinstance(candidate, dict) else {}
  parts = content.get("parts") if isinstance(content, dict) else []
  text = ""
  if isinstance(parts, list):
    text = "".join(str(part.get("text", "")) for part in parts if isinstance(part, dict) and isinstance(part.get("text"), str))
  usage_meta = payload.get("usageMetadata") or {}
  usage = SimpleNamespace(
    prompt_tokens=int(usage_meta.get("promptTokenCount") or 0),
    completion_tokens=int(usage_meta.get("candidatesTokenCount") or 0),
    total_tokens=int(usage_meta.get("totalTokenCount") or 0),
    prompt_tokens_details=SimpleNamespace(cached_tokens=int(usage_meta.get("cachedContentTokenCount") or 0)),
  )
  message = SimpleNamespace(
    role="assistant",
    content=text or None,
    tool_calls=None,
    reasoning=None,
    reasoning_content=None,
    reasoning_details=None,
  )
  choice = SimpleNamespace(index=0, message=message, finish_reason="stop")
  return SimpleNamespace(
    id=f"chatcmpl-{uuid.uuid4().hex[:12]}",
    object="chat.completion",
    created=int(time.time()),
    model=model,
    choices=[choice],
    usage=usage,
  )


def _iter_sse_events(response: httpx.Response) -> Iterator[dict[str, Any]]:
  buffer = ""
  for chunk in response.iter_text():
    if not chunk:
      continue
    buffer += chunk
    while "\n" in buffer:
      line, buffer = buffer.split("\n", 1)
      line = line.rstrip("\r")
      if not line or not line.startswith("data: "):
        continue
      data = line[6:]
      if data == "[DONE]":
        return
      try:
        parsed = json.loads(data)
      except json.JSONDecodeError:
        logger.debug("Non-JSON Antigravity SSE line: %s", data[:200])
        continue
      if isinstance(parsed, dict):
        yield parsed


def _translate_stream_event(
  event: dict[str, Any],
  model: str,
  tool_call_indices: dict[str, dict[str, Any]],
) -> list[Any]:
  try:
    from agent.gemini_native_adapter import translate_stream_event
    return translate_stream_event(event, model, tool_call_indices)
  except Exception:
    candidates = event.get("candidates") or []
    if not isinstance(candidates, list) or not candidates:
      return []
    candidate = candidates[0] if isinstance(candidates[0], dict) else {}
    parts = ((candidate.get("content") or {}).get("parts") or []) if isinstance(candidate, dict) else []
    chunks = []
    for part in parts:
      if isinstance(part, dict) and isinstance(part.get("text"), str) and part["text"]:
        delta = SimpleNamespace(
          role="assistant",
          content=part["text"],
          tool_calls=None,
          reasoning=None,
          reasoning_content=None,
        )
        chunks.append(SimpleNamespace(
          id=f"chatcmpl-{uuid.uuid4().hex[:12]}",
          object="chat.completion.chunk",
          created=int(time.time()),
          model=model,
          choices=[SimpleNamespace(index=0, delta=delta, finish_reason=None)],
          usage=None,
        ))
    if candidate.get("finishReason"):
      delta = SimpleNamespace(
        role="assistant",
        content=None,
        tool_calls=None,
        reasoning=None,
        reasoning_content=None,
      )
      chunks.append(SimpleNamespace(
        id=f"chatcmpl-{uuid.uuid4().hex[:12]}",
        object="chat.completion.chunk",
        created=int(time.time()),
        model=model,
        choices=[SimpleNamespace(index=0, delta=delta, finish_reason="stop")],
        usage=None,
      ))
    return chunks


def _error_message_from_body(body: dict[str, Any]) -> str:
  error = body.get("error")
  if isinstance(error, dict):
    message = error.get("message")
    if isinstance(message, str) and message:
      return redact_secret_text(message)
  return redact_secret_text(str(body))


def _retry_after_from_response(response: httpx.Response, body: dict[str, Any]) -> float | None:
  header = response.headers.get("Retry-After") or response.headers.get("retry-after")
  if header:
    try:
      return float(header)
    except ValueError:
      pass
  try:
    from .transform.response import extract_retry_info
    retry_info = extract_retry_info(body)
    if isinstance(retry_info, dict):
      retry_ms = retry_info.get("retryDelayMs")
      if isinstance(retry_ms, (int, float)):
        return max(1.0, float(retry_ms) / 1000.0)
  except Exception:
    pass
  return None


def _raise_http_error(response: httpx.Response, *, model: str, endpoint: str) -> None:
  body: dict[str, Any] = {}
  try:
    parsed = response.json()
    if isinstance(parsed, dict):
      body = parsed
  except Exception:
    body = {}
  message = _error_message_from_body(body) if body else redact_secret_text(response.text[:500])
  if not message:
    message = f"Antigravity Cloud Code HTTP {response.status_code}"
  retry_after = _retry_after_from_response(response, body)
  debug = (
    f"\n\n[Debug Info]\n"
    f"Model: {model}\n"
    f"Endpoint: {endpoint}\n"
    f"Status: {response.status_code}"
  )
  raise AntigravityCloudCodeError(
    message + debug,
    status_code=response.status_code,
    response=response,
    retry_after=retry_after,
    details=body,
  )
