"""OpenAI-format messages → Gemini contents[].parts[] format conversion."""
from __future__ import annotations

import json
import re

_TOOL_NAME_MAX_LENGTH = 64
_TOOL_NAME_ALLOWED_RE = re.compile(r"[^A-Za-z0-9_.:-]+")
_TOOL_NAME_FIRST_RE = re.compile(r"^[A-Za-z_]")


def is_claude_model(model: str) -> bool:
  return "claude" in model.lower()


def is_gemini_model(model: str) -> bool:
  lower = model.lower()
  return "gemini" in lower and "claude" not in lower


def is_gpt_oss_model(model: str) -> bool:
  return "gpt-oss" in model.lower()


def parse_data_url(url: str) -> tuple[str, str] | None:
  """Extract (mime_type, base64_data) from a data:mime;base64,DATA URL."""
  match = re.match(r"^data:([^;]+);base64,(.+)$", url, re.DOTALL)
  if match:
    return (match.group(1), match.group(2))
  return None


class ToolNameCollisionError(ValueError):
  """Raised when two tool names normalize to the same Antigravity function name."""


def normalize_antigravity_tool_name(name: object) -> str:
  raw_name = str(name or "").strip()
  normalized = _TOOL_NAME_ALLOWED_RE.sub("_", raw_name)
  normalized = normalized.strip("_")
  if not normalized:
    normalized = "tool"
  if not _TOOL_NAME_FIRST_RE.match(normalized):
    normalized = "_" + normalized
  return normalized[:_TOOL_NAME_MAX_LENGTH]


def validate_antigravity_tool_name_collisions(names: list[str]) -> dict[str, str]:
  mapping: dict[str, str] = {}
  reverse: dict[str, str] = {}
  for raw_name in names:
    normalized = normalize_antigravity_tool_name(raw_name)
    existing = reverse.get(normalized)
    if existing is not None and existing != raw_name:
      raise ToolNameCollisionError(
        f"Tool names {existing!r} and {raw_name!r} both normalize to {normalized!r}"
      )
    reverse[normalized] = raw_name
    mapping[raw_name] = normalized
  return mapping


def _normalize_tool_response_content(content: object) -> dict:
  if isinstance(content, dict):
    return content
  parsed: object = content
  if isinstance(content, str):
    stripped = content.strip()
    if stripped:
      try:
        parsed = json.loads(stripped)
      except (json.JSONDecodeError, ValueError):
        parsed = content
  if isinstance(parsed, dict):
    return parsed
  return {"content": parsed if parsed is not None else ""}


def _is_thinking_like_part(part: dict) -> bool:
  part_type = str(part.get("type") or "").lower()
  return (
    part.get("thought") is True
    or "thoughtSignature" in part
    or "signature" in part and part_type in ("thinking", "reasoning", "redacted_thinking")
    or part_type in ("thinking", "reasoning", "redacted_thinking")
  )


def _convert_thinking_like_part(part: dict) -> dict | None:
  text = part.get("thinking") or part.get("text") or ""
  if not isinstance(text, str):
    text = str(text)
  result: dict = {
    "thought": True,
    "text": text,
  }
  signature = part.get("thoughtSignature") or part.get("signature")
  if signature:
    result["thoughtSignature"] = signature
  return result


def _convert_content_part(
  part: dict,
  tool_call_id_to_name: dict[str, str] | None = None,
) -> dict | None:
  if not isinstance(part, dict):
    return None

  part_type = part.get("type", "")

  if part_type == "text":
    text = part.get("text", "")
    if isinstance(text, str):
      return {"text": text}
    return None

  if part_type == "image_url":
    image_url = part.get("image_url")
    if isinstance(image_url, dict):
      url = image_url.get("url", "")
      if isinstance(url, str):
        parsed = parse_data_url(url)
        if parsed:
          mime_type, data = parsed
          return {"inlineData": {"mimeType": mime_type, "data": data}}
    return None

  if part_type == "tool_use":
    name = normalize_antigravity_tool_name(part.get("name", ""))
    args = part.get("input", {})
    if not isinstance(args, dict):
      args = {}
    function_call = {"name": name, "args": args}
    tool_id = part.get("id")
    if tool_id:
      function_call["id"] = str(tool_id)
      if name and tool_call_id_to_name is not None:
        tool_call_id_to_name[str(tool_id)] = str(name)
    return {"functionCall": function_call}

  if part_type == "tool_result":
    name = normalize_antigravity_tool_name(part.get("name", "")) if part.get("name") else ""
    content = part.get("content", "")
    result_id = part.get("tool_use_id") or part.get("id")
    if not name and result_id and tool_call_id_to_name is not None:
      name = tool_call_id_to_name.get(str(result_id), "")
    function_response = {"name": name, "response": _normalize_tool_response_content(content)}
    if result_id:
      function_response["id"] = str(result_id)
    return {"functionResponse": function_response}

  if _is_thinking_like_part(part):
    return _convert_thinking_like_part(part)

  if "text" in part and isinstance(part["text"], str):
    return {"text": part["text"]}

  return None


def _content_to_parts(
  content: str | list | None,
  tool_call_id_to_name: dict[str, str] | None = None,
) -> list[dict]:
  if content is None:
    return []

  if isinstance(content, str):
    return [{"text": content}] if content else []

  if isinstance(content, list):
    parts: list[dict] = []
    for item in content:
      if isinstance(item, str):
        parts.append({"text": item})
      elif isinstance(item, dict):
        converted = _convert_content_part(item, tool_call_id_to_name)
        if converted is not None:
          parts.append(converted)
    return parts

  return []


def _convert_tool_calls(tool_calls: list, tool_call_id_to_name: dict[str, str]) -> list[dict]:
  """Parse OpenAI tool_calls [{function: {name, arguments: json_string}}] to
  Gemini [{functionCall: {name, args: parsed_dict}}]."""
  parts: list[dict] = []
  for call in tool_calls:
    if not isinstance(call, dict):
      continue
    fn = call.get("function")
    if not isinstance(fn, dict):
      continue
    name = normalize_antigravity_tool_name(fn.get("name", ""))
    arguments_str = fn.get("arguments", "{}")
    if isinstance(arguments_str, str):
      try:
        args = json.loads(arguments_str)
      except (json.JSONDecodeError, ValueError):
        args = {}
    elif isinstance(arguments_str, dict):
      args = arguments_str
    else:
      args = {}
    function_call = {"name": name, "args": args}
    tool_call_id = call.get("id")
    if tool_call_id:
      function_call["id"] = tool_call_id
      if name:
        tool_call_id_to_name[str(tool_call_id)] = name
    parts.append({"functionCall": function_call})
  return parts


def _has_function_response(parts: list[dict]) -> bool:
  return any("functionResponse" in p for p in parts)


def _has_text(parts: list[dict]) -> bool:
  return any("text" in p for p in parts)


def _can_merge(existing_parts: list[dict], new_parts: list[dict]) -> bool:
  """Consecutive same-role merging guard: don't mix functionResponse with text."""
  existing_has_fr = _has_function_response(existing_parts)
  new_has_fr = _has_function_response(new_parts)
  existing_has_text = _has_text(existing_parts)
  new_has_text = _has_text(new_parts)

  if (existing_has_fr and new_has_text) or (existing_has_text and new_has_fr):
    return False

  return True


def transform_messages_to_contents(
  messages: list[dict],
) -> tuple[list[dict], dict | None]:
  """Convert OpenAI messages[] to Gemini (contents[], system_instruction | None).

  System messages are extracted into systemInstruction {parts: [{text}]}.
  Consecutive same-role entries are merged unless it would mix
  functionResponse with text parts.
  """
  system_texts: list[str] = []
  raw_contents: list[dict] = []
  tool_call_id_to_name: dict[str, str] = {}

  for msg in messages:
    if not isinstance(msg, dict):
      continue

    role = msg.get("role", "")
    content = msg.get("content")
    tool_calls = msg.get("tool_calls")

    if role in ("system", "developer"):
      if isinstance(content, str) and content:
        system_texts.append(content)
      elif isinstance(content, list):
        for item in content:
          if isinstance(item, str) and item:
            system_texts.append(item)
          elif isinstance(item, dict) and item.get("type") == "text":
            text = item.get("text", "")
            if isinstance(text, str) and text:
              system_texts.append(text)
      continue

    if role == "assistant":
      parts = _content_to_parts(content, tool_call_id_to_name)
      if isinstance(tool_calls, list) and tool_calls:
        parts.extend(_convert_tool_calls(tool_calls, tool_call_id_to_name))
      if parts:
        raw_contents.append({"role": "model", "parts": parts})
      continue

    if role == "tool":
      tool_call_id = msg.get("tool_call_id")
      tool_name = msg.get("name") or tool_call_id_to_name.get(str(tool_call_id), "")
      if tool_name:
        tool_name = normalize_antigravity_tool_name(tool_name)
      tool_content = msg.get("content", "")
      function_response = {
        "name": tool_name,
        "response": _normalize_tool_response_content(tool_content),
      }
      if tool_call_id:
        function_response["id"] = tool_call_id
      parts = [{
        "functionResponse": function_response
      }]
      raw_contents.append({"role": "user", "parts": parts})
      continue

    parts = _content_to_parts(content, tool_call_id_to_name)
    if parts:
      raw_contents.append({"role": "user", "parts": parts})

  merged: list[dict] = []
  for entry in raw_contents:
    if (
      merged
      and merged[-1]["role"] == entry["role"]
      and _can_merge(merged[-1]["parts"], entry["parts"])
    ):
      merged[-1]["parts"].extend(entry["parts"])
    else:
      merged.append(entry)

  system_instruction: dict | None = None
  if system_texts:
    combined = "\n\n".join(system_texts)
    system_instruction = {"parts": [{"text": combined}]}

  return (merged, system_instruction)
