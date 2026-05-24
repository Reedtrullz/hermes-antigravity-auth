"""SSE streaming response parsing, usage extraction, and error rewriting."""
from __future__ import annotations

import json
import re
from typing import Any


SYNTHETIC_THINKING_PLACEHOLDER = "[Thinking preserved]\n"
DEBUG_MESSAGE_PREFIX = "[hermes-antigravity-auth debug]"
ANTIGRAVITY_PREVIEW_LINK = "https://goo.gle/enable-preview-features"


def _parse_antigravity_api_body(raw_text: str) -> dict[str, Any] | None:
  try:
    parsed = json.loads(raw_text)
  except json.JSONDecodeError:
    return None
  if isinstance(parsed, list):
    for item in parsed:
      if isinstance(item, dict):
        return item
    return None
  if isinstance(parsed, dict):
    return parsed
  return None


def _transform_gemini_candidate(candidate: Any) -> Any:
  if not isinstance(candidate, dict):
    return candidate

  content = candidate.get("content")
  if not isinstance(content, dict):
    return candidate

  parts = content.get("parts")
  if not isinstance(parts, list):
    return candidate

  thinking_texts: list[str] = []
  transformed_parts: list[dict[str, Any]] = []

  for part in parts:
    if not isinstance(part, dict):
      transformed_parts.append(part)
      continue

    if part.get("thought") is True:
      thinking_text = part.get("text") or ""
      if isinstance(thinking_text, dict):
        thinking_text = thinking_text.get("text", "")
      thinking_texts.append(str(thinking_text))

      transformed: dict[str, Any] = {**part, "type": "reasoning"}

      sig = part.get("signature") or part.get("thoughtSignature")
      if sig is not None:
        transformed["providerMetadata"] = {
          "anthropic": {"signature": sig},
        }
        transformed.pop("signature", None)
        transformed.pop("thoughtSignature", None)

      transformed_parts.append(transformed)
      continue

    if part.get("type") == "thinking":
      thinking_text = part.get("thinking") or part.get("text") or ""
      thinking_texts.append(str(thinking_text))

      transformed = {
        **part,
        "type": "reasoning",
        "text": str(thinking_text),
        "thought": True,
      }

      sig = part.get("signature") or part.get("thoughtSignature")
      if sig is not None:
        transformed["providerMetadata"] = {
          "anthropic": {"signature": sig},
        }
        transformed.pop("signature", None)
        transformed.pop("thoughtSignature", None)

      transformed_parts.append(transformed)
      continue

    function_call = part.get("functionCall")
    if isinstance(function_call, dict):
      args = function_call.get("args", {})
      if isinstance(args, str):
        try:
          args = json.loads(args)
        except json.JSONDecodeError:
          pass
      transformed_parts.append({
        **part,
        "functionCall": {**function_call, "args": args},
      })
      continue

    transformed_parts.append(part)

  result: dict[str, Any] = {**candidate, "content": {**content, "parts": transformed_parts}}
  if thinking_texts:
    result["reasoning_content"] = "\n\n".join(thinking_texts)

  return result


def _transform_thinking_parts(response: dict[str, Any]) -> dict[str, Any]:
  if not isinstance(response, dict):
    return response

  result = dict(response)
  reasoning_texts: list[str] = []

  content = result.get("content")
  if isinstance(content, list):
    transformed_content: list[dict[str, Any]] = []
    for block in content:
      if isinstance(block, dict) and block.get("type") == "thinking":
        thinking_text = block.get("thinking") or block.get("text") or ""
        reasoning_texts.append(str(thinking_text))

        transformed: dict[str, Any] = {
          **block,
          "type": "reasoning",
          "text": str(thinking_text),
          "thought": True,
        }

        sig = block.get("signature") or block.get("thoughtSignature")
        if sig is not None:
          transformed["providerMetadata"] = {
            "anthropic": {"signature": sig},
          }
          transformed.pop("signature", None)
          transformed.pop("thoughtSignature", None)

        transformed_content.append(transformed)
      else:
        transformed_content.append(block)

    result["content"] = transformed_content

  candidates = result.get("candidates")
  if isinstance(candidates, list):
    result["candidates"] = [_transform_gemini_candidate(c) for c in candidates]

  if reasoning_texts and "reasoning_content" not in result:
    result["reasoning_content"] = "\n\n".join(reasoning_texts)

  return result


def _needs_preview_access_override(status_code: int, body: dict[str, Any], requested_model: str | None) -> bool:
  if status_code != 404:
    return False

  check_str = requested_model or ""
  if re.search(r"antigravity|opus|claude", check_str, re.IGNORECASE):
    return True

  error = body.get("error")
  if isinstance(error, dict):
    error_message = error.get("message", "")
    if isinstance(error_message, str) and re.search(r"antigravity|opus|claude", error_message, re.IGNORECASE):
      return True

  return False


def _extract_parsed_usage(parsed: dict[str, Any]) -> dict[str, int] | None:
  """Extract usage metadata from a parsed JSON object.

  Looks for usageMetadata at the top level or under response.usageMetadata.
  """
  response_data = parsed.get("response")
  if isinstance(response_data, dict):
    usage = response_data.get("usageMetadata") or response_data.get("usage_metadata")
    if isinstance(usage, dict):
      return _extract_usage_values(usage)

  usage = parsed.get("usageMetadata") or parsed.get("usage_metadata")
  if isinstance(usage, dict):
    return _extract_usage_values(usage)
  return None


def _extract_usage_from_sse_payload(body: str) -> dict[str, Any] | None:
  """Extract usage metadata from an SSE stream payload.

  Handles multi-line SSE data blocks by accumulating ``data:`` lines
  until a blank line is encountered, then joining and parsing.
  """
  if not isinstance(body, str):
    return None
  lines = body.split("\n")
  current_data: list[str] = []
  for line in lines:
    if line.startswith("data: "):
      current_data.append(line[6:])
    elif line == "" and current_data:
      data_str = "".join(current_data)
      try:
        parsed = json.loads(data_str)
      except json.JSONDecodeError:
        current_data = []
        continue
      usage = _extract_parsed_usage(parsed)
      if usage:
        return usage
      current_data = []
  return None


def _extract_usage_values(usage: dict[str, Any]) -> dict[str, int] | None:
  result: dict[str, int] = {}
  for key in (
    "totalTokenCount",
    "promptTokenCount",
    "candidatesTokenCount",
    "cachedContentTokenCount",
    "thoughtsTokenCount",
  ):
    val = usage.get(key)
    if isinstance(val, (int, float)):
      result[key] = int(val)
  return result if result else None


def _build_usage_headers(
  usage: dict[str, int] | None,
  headers: dict[str, str] | None = None,
) -> dict[str, str] | None:
  if usage is None:
    return headers or None
  result = dict(headers or {})
  if usage.get("cachedContentTokenCount") is not None:
    result["x-antigravity-cached-content-token-count"] = str(usage["cachedContentTokenCount"])
  if usage.get("totalTokenCount") is not None:
    result["x-antigravity-total-token-count"] = str(usage["totalTokenCount"])
  if usage.get("promptTokenCount") is not None:
    result["x-antigravity-prompt-token-count"] = str(usage["promptTokenCount"])
  if usage.get("candidatesTokenCount") is not None:
    result["x-antigravity-candidates-token-count"] = str(usage["candidatesTokenCount"])
  return result if result else headers or None


def transform_antigravity_response(
  body: str | bytes,
  streaming: bool,
  status_code: int = 200,
  headers: dict[str, str] | None = None,
  requested_model: str | None = None,
  effective_model: str | None = None,
  project_id: str | None = None,
  endpoint: str | None = None,
  session_id: str | None = None,
  debug_text: str | None = None,
) -> tuple[str, dict[str, str] | None, dict[str, str] | None]:
  """Return (transformed_body, extra_headers_or_None, error_or_None).

  error_or_None contains recovery info ({recoveryType: ...}) when a
  recoverable error is detected.
  """
  if isinstance(body, bytes):
    body = body.decode("utf-8", errors="replace")

  resolved_headers = headers or {}
  content_type = resolved_headers.get("content-type", resolved_headers.get("Content-Type", "application/json")).lower()
  is_json = "application/json" in content_type
  is_sse = "text/event-stream" in content_type

  if not is_json and not is_sse:
    return (body, None, None)

  extra_headers: dict[str, str] | None = None
  extra_headers_dict: dict[str, str] = {}

  if streaming and is_sse:
    usage = _extract_usage_from_sse_payload(body)
    return (body, _build_usage_headers(usage, extra_headers_dict), None)

  parsed = _parse_antigravity_api_body(body)
  if parsed is None:
    return (body, None, None)

  if status_code != 200:
    # Check for preview access rewrite (404 with Antigravity/Claude models)
    patched = rewrite_preview_access_error(parsed, status_code, requested_model)
    if patched is not None:
      parsed = patched

    return _handle_error_response(
      parsed, body, status_code, resolved_headers,
      requested_model, effective_model, project_id, endpoint, debug_text,
    )

  usage = extract_usage_from_body(body)

  extra_headers = _build_usage_headers(usage, extra_headers)

  response_data = parsed.get("response")
  if response_data is not None:
    if isinstance(response_data, dict):
      transformed = response_data
      if debug_text:
        transformed = inject_debug_thinking(transformed, debug_text)
      transformed = _transform_thinking_parts(transformed)
      return (json.dumps(transformed), extra_headers or None, None)

    return (json.dumps(response_data), extra_headers or None, None)

  return (json.dumps(parsed), extra_headers or None, None)


def _handle_error_response(
  parsed: dict[str, Any],
  raw_body: str,
  status_code: int,
  headers: dict[str, str],
  requested_model: str | None,
  effective_model: str | None,
  project_id: str | None,
  endpoint: str | None,
  debug_text: str | None,
) -> tuple[str, dict[str, str] | None, dict[str, str] | None]:
  error_info = parsed.get("error")
  error_body: dict[str, Any] = parsed

  if not isinstance(error_info, dict):
    error_body = {"error": {"message": str(parsed)}}
    error_info = error_body["error"]

  raw_message = error_info.get("message", "")
  if not isinstance(raw_message, str) or not raw_message:
    raw_message = "Unknown error"

  request_id = headers.get("x-request-id", headers.get("X-Request-Id", "N/A"))

  debug_info = (
    f"\n\n[Debug Info]\n"
    f"Requested Model: {requested_model or 'Unknown'}\n"
    f"Effective Model: {effective_model or 'Unknown'}\n"
    f"Project: {project_id or 'Unknown'}\n"
    f"Endpoint: {endpoint or 'Unknown'}\n"
    f"Status: {status_code}\n"
    f"Request ID: {request_id}"
  )

  injected_debug = f"\n\n{debug_text}" if debug_text else ""
  error_body["error"]["message"] = raw_message + debug_info + injected_debug

  extra_headers: dict[str, str] = {}
  msg_lower = raw_message.lower()

  has_thinking = "thinking" in msg_lower
  has_order = any(x in msg_lower for x in (
    "first block", "must start with", "preceeding", "preceding",
  ))
  has_expected_found = (
    ("expected thinking" in msg_lower or "expected a thinking" in msg_lower)
    and "found" in msg_lower
  )
  if has_thinking and (has_order or has_expected_found):
    return (
      json.dumps(error_body),
      extra_headers or None,
      {"recoveryType": "thinking_block_order"},
    )

  if any(x in msg_lower for x in (
    "prompt is too long",
    "context length exceeded",
    "context_length_exceeded",
    "maximum context length",
  )):
    extra_headers["x-antigravity-context-error"] = "prompt_too_long"

  if "tool_use" in msg_lower and "tool_result" in msg_lower and (
    "without" in msg_lower or "immediately after" in msg_lower
  ):
    extra_headers["x-antigravity-context-error"] = "tool_pairing"
    return (
      json.dumps(error_body),
      extra_headers or None,
      {"recoveryType": "tool_result_missing"},
    )

  retry_info = extract_retry_info(parsed)
  if retry_info is not None:
    retry_delay_ms = retry_info.get("retryDelayMs")
    if retry_delay_ms is not None:
      retry_sec = str(max(1, int(retry_delay_ms / 1000)))
      extra_headers["Retry-After"] = retry_sec
      extra_headers["retry-after-ms"] = str(int(retry_delay_ms))

  return (json.dumps(error_body), extra_headers or None, None)


def extract_usage_from_body(body: str) -> dict[str, Any] | None:
  if not isinstance(body, str):
    return None

  try:
    parsed = json.loads(body)
  except json.JSONDecodeError:
    return None

  if isinstance(parsed, list):
    for item in parsed:
      if isinstance(item, dict):
        usage = item.get("usageMetadata") or item.get("usage_metadata")
        if isinstance(usage, dict):
          return _extract_usage_values(usage)
    return None

  if not isinstance(parsed, dict):
    return None

  response_data = parsed.get("response")
  if isinstance(response_data, dict):
    usage = response_data.get("usageMetadata") or response_data.get("usage_metadata")
    if isinstance(usage, dict):
      return _extract_usage_values(usage)

  usage = parsed.get("usageMetadata") or parsed.get("usage_metadata")
  if isinstance(usage, dict):
    return _extract_usage_values(usage)

  return None


def inject_debug_thinking(response_body: dict[str, Any], debug_text: str) -> dict[str, Any]:
  if not isinstance(response_body, dict):
    return response_body

  candidates = response_body.get("candidates")
  if isinstance(candidates, list) and candidates:
    first = candidates[0]
    if isinstance(first, dict):
      content = first.get("content")
      if isinstance(content, dict):
        parts = content.get("parts")
        if isinstance(parts, list):
          new_parts: list[dict[str, Any]] = [{"thought": True, "text": debug_text}]
          new_parts.extend(parts)
          new_first: dict[str, Any] = {**first, "content": {**content, "parts": new_parts}}
          new_candidates: list[dict[str, Any]] = [new_first]
          new_candidates.extend(candidates[1:])
          return {**response_body, "candidates": new_candidates}
    return response_body

  content = response_body.get("content")
  if isinstance(content, list):
    new_content: list[dict[str, Any]] = [{"type": "thinking", "thinking": debug_text}]
    new_content.extend(content)
    return {**response_body, "content": new_content}

  if "reasoning_content" not in response_body:
    return {**response_body, "reasoning_content": debug_text}

  return response_body


def rewrite_preview_access_error(
  body: dict[str, Any],
  status_code: int,
  requested_model: str | None,
) -> dict[str, Any] | None:
  if not _needs_preview_access_override(status_code, body, requested_model):
    return None

  error = body.get("error", {})
  if not isinstance(error, dict):
    error = {}

  trimmed = error.get("message", "")
  if not isinstance(trimmed, str):
    trimmed = ""

  trimmed = trimmed.strip()
  prefix = (
    trimmed
    if trimmed
    else "Antigravity preview features are not enabled for this account."
  )
  enhanced = (
    f"{prefix} Request preview access at {ANTIGRAVITY_PREVIEW_LINK}"
    " before using this model."
  )

  return {
    **body,
    "error": {
      **error,
      "message": enhanced,
    },
  }


def extract_retry_info(body: dict[str, Any]) -> dict[str, Any] | None:
  if not isinstance(body, dict):
    return None

  error = body.get("error")
  if not isinstance(error, dict):
    return None

  details = error.get("details")
  if not isinstance(details, list):
    return None

  for detail in details:
    if not isinstance(detail, dict):
      continue
    if detail.get("@type") != "type.googleapis.com/google.rpc.RetryInfo":
      continue
    retry_delay = detail.get("retryDelay")
    if not isinstance(retry_delay, str):
      continue

    match = re.match(r"^([\d.]+)s$", retry_delay)
    if match:
      seconds = float(match.group(1))
      if seconds > 0:
        return {"retryDelayMs": int(seconds * 1000)}

  return None
