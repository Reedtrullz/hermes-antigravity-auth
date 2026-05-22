"""OpenAI-format messages → Gemini contents[].parts[] format conversion."""
from __future__ import annotations

import json
import re


def is_claude_model(model: str) -> bool:
  return "claude" in model.lower()


def is_gemini_model(model: str) -> bool:
  lower = model.lower()
  return "gemini" in lower and "claude" not in lower


def parse_data_url(url: str) -> tuple[str, str] | None:
  """Extract (mime_type, base64_data) from a data:mime;base64,DATA URL."""
  match = re.match(r"^data:([^;]+);base64,(.+)$", url, re.DOTALL)
  if match:
    return (match.group(1), match.group(2))
  return None


def _convert_content_part(part: dict) -> dict | None:
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
    name = part.get("name", "")
    args = part.get("input", {})
    if not isinstance(args, dict):
      args = {}
    return {"functionCall": {"name": name, "args": args}}

  if part_type == "tool_result":
    name = part.get("name", "")
    content = part.get("content", "")
    return {"functionResponse": {"name": name, "response": {"content": content}}}

  if "text" in part and isinstance(part["text"], str):
    return {"text": part["text"]}

  return None


def _content_to_parts(content: str | list | None) -> list[dict]:
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
        converted = _convert_content_part(item)
        if converted is not None:
          parts.append(converted)
    return parts

  return []


def _convert_tool_calls(tool_calls: list) -> list[dict]:
  """Parse OpenAI tool_calls [{function: {name, arguments: json_string}}] to
  Gemini [{functionCall: {name, args: parsed_dict}}]."""
  parts: list[dict] = []
  for call in tool_calls:
    if not isinstance(call, dict):
      continue
    fn = call.get("function")
    if not isinstance(fn, dict):
      continue
    name = fn.get("name", "")
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
    parts.append({"functionCall": {"name": name, "args": args}})
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

  for msg in messages:
    if not isinstance(msg, dict):
      continue

    role = msg.get("role", "")
    content = msg.get("content")
    tool_calls = msg.get("tool_calls")

    if role == "system":
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
      parts = _content_to_parts(content)
      if isinstance(tool_calls, list) and tool_calls:
        parts.extend(_convert_tool_calls(tool_calls))
      if parts:
        raw_contents.append({"role": "model", "parts": parts})
      continue

    if role == "tool":
      tool_name = msg.get("name", "")
      tool_content = msg.get("content", "")
      parts = [{
        "functionResponse": {
          "name": tool_name,
          "response": {"content": tool_content},
        }
      }]
      raw_contents.append({"role": "user", "parts": parts})
      continue

    parts = _content_to_parts(content)
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
