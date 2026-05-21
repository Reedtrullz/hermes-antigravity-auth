from __future__ import annotations


def is_thinking_part(part: dict) -> bool:
  """Check if a part is a thinking/reasoning block.

  A part is a thinking block if any of these conditions are met:
  - ``part.get("thought") == True`` (Gemini-style)
  - ``part.get("type") in ("thinking", "redacted_thinking", "reasoning")`` (Anthropic-style)
  - ``part.get("thinking") is not None`` (has thinking field)
  """
  if not isinstance(part, dict):
    return False
  return (
    part.get("thought") is True
    or part.get("type") in ("thinking", "redacted_thinking", "reasoning")
    or part.get("thinking") is not None
  )


def has_signature_field(part: dict) -> bool:
  """Check if a part has a signature field (``signature`` or ``thoughtSignature``).

  Used to detect foreign thinking blocks that might have unknown type values.
  """
  if not isinstance(part, dict):
    return False
  return "signature" in part or "thoughtSignature" in part


def is_tool_block(part: dict) -> bool:
  """Check if a part is a tool block.

  Tool blocks must never be filtered — they are required for tool call/result pairing.
  Handles multiple formats:
  - Anthropic: ``{type: "tool_use"}``, ``{type: "tool_result", tool_use_id}``
  - Gemini: ``{functionCall}``, ``{functionResponse}``
  """
  if not isinstance(part, dict):
    return False
  return (
    part.get("type") in ("tool_use", "tool_result")
    or "tool_use_id" in part
    or "tool_call_id" in part
    or "functionCall" in part
    or "functionResponse" in part
  )


def _strip_cache_control(obj: object) -> object:
  """Recursively strip ``cache_control`` and ``providerOptions`` fields."""
  if obj is None or isinstance(obj, (str, int, float, bool)):
    return obj
  if isinstance(obj, list):
    return [_strip_cache_control(item) for item in obj]
  if isinstance(obj, dict):
    result: dict[str, object] = {}
    for key, value in obj.items():
      if key in ("cache_control", "providerOptions"):
        continue
      result[key] = _strip_cache_control(value)
    return result
  return obj


def sanitize_thinking_part(part: dict) -> dict | None:
  """Sanitize a thinking part, keeping only the allowed fields.

  - Gemini-style ``{thought: True, text, thoughtSignature}`` →
    ``{thought: True, text}``
  - Anthropic-style ``{type: "thinking", thinking, signature}`` →
    ``{type: "thinking", thinking}``

  If thinking content is empty/None and no signature is present, returns
  ``None`` (drop the part entirely).
  """
  if not isinstance(part, dict):
    return None

  # Gemini-style thought blocks: {thought: True, text, thoughtSignature}
  if part.get("thought") is True:
    text_content = part.get("text")
    if isinstance(text_content, dict):
      text_content = text_content.get("text")

    has_content = isinstance(text_content, str) and text_content.strip() != ""
    if not has_content and "thoughtSignature" not in part:
      return None

    sanitized: dict[str, object] = {"thought": True}
    if text_content is not None:
      sanitized["text"] = text_content
    if "thoughtSignature" in part:
      sanitized["thoughtSignature"] = part["thoughtSignature"]
    return sanitized

  # Anthropic-style thinking/redacted_thinking blocks:
  # {type: "thinking"|"redacted_thinking", thinking, signature}
  type_val = part.get("type")
  if type_val in ("thinking", "redacted_thinking") or part.get("thinking") is not None:
    thinking_content = part.get("thinking") or part.get("text")
    if isinstance(thinking_content, dict):
      nested = thinking_content
      thinking_content = nested.get("text") or nested.get("thinking")

    has_content = isinstance(thinking_content, str) and thinking_content.strip() != ""
    if not has_content and "signature" not in part:
      return None

    sanitized = {
      "type": "redacted_thinking" if type_val == "redacted_thinking" else "thinking",
    }
    if thinking_content is not None:
      sanitized["thinking"] = thinking_content
    if "signature" in part:
      sanitized["signature"] = part["signature"]
    return sanitized

  # Reasoning blocks: {type: "reasoning", text, signature}
  if type_val == "reasoning":
    text_content = part.get("text")
    if isinstance(text_content, dict):
      text_content = text_content.get("text")

    has_content = isinstance(text_content, str) and text_content.strip() != ""
    if not has_content and "signature" not in part:
      return None

    sanitized = {"type": "reasoning"}
    if text_content is not None:
      sanitized["text"] = text_content
    if "signature" in part:
      sanitized["signature"] = part["signature"]
    return sanitized

  # Fallback: strip cache_control and providerOptions recursively
  result = _strip_cache_control(part)
  return result if isinstance(result, dict) else None


def strip_all_thinking_blocks(content_array: list[dict]) -> list[dict]:
  """Unconditionally strip ALL thinking/reasoning blocks from a content array.

  - Filters out all thinking parts and signature-bearing parts
  - PRESERVES all tool blocks unconditionally
  - PRESERVES all non-thinking, non-signature parts
  """
  result: list[dict] = []
  for item in content_array:
    if not isinstance(item, dict):
      result.append(item)
      continue
    if is_tool_block(item):
      result.append(item)
      continue
    if is_thinking_part(item):
      continue
    if has_signature_field(item):
      continue
    result.append(item)
  return result


def strip_thinking_blocks(contents: list[dict], is_claude: bool = True) -> list[dict]:
  """Strip ALL thinking/reasoning blocks from a Gemini-style contents array.

  For Claude models (``is_claude=True``, default), all thinking blocks are
  unconditionally stripped from every content entry's parts.

  For non-Claude models, the contents array passes through unchanged.
  """
  if not is_claude:
    return contents

  result: list[dict] = []
  for content in contents:
    if not isinstance(content, dict):
      result.append(content)
      continue

    if isinstance(content.get("parts"), list):
      result.append({
        **content,
        "parts": strip_all_thinking_blocks(content["parts"]),
      })
    elif isinstance(content.get("content"), list):
      result.append({
        **content,
        "content": strip_all_thinking_blocks(content["content"]),
      })
    else:
      result.append(content)

  return result


def filter_contents_thinking(contents: list[dict], is_claude: bool) -> list[dict]:
  """Filter thinking blocks from a contents array with role awareness.

  - If ``is_claude`` is ``True`` and the content entry has role ``"model"`` or
    ``"assistant"``, thinking blocks are stripped from its parts/content array.
  - Other roles (e.g. ``"user"``) are left unchanged.
  - If ``is_claude`` is ``False``, all entries pass through unchanged.
  """
  if not is_claude:
    return contents

  result: list[dict] = []
  for content in contents:
    if not isinstance(content, dict):
      result.append(content)
      continue

    role = content.get("role", "")
    should_strip = role in ("model", "assistant")

    if isinstance(content.get("parts"), list):
      parts = content["parts"]
      result.append({
        **content,
        "parts": strip_all_thinking_blocks(parts) if should_strip else parts,
      })
    elif isinstance(content.get("content"), list):
      content_list = content["content"]
      result.append({
        **content,
        "content": strip_all_thinking_blocks(content_list) if should_strip else content_list,
      })
    else:
      result.append(content)

  return result


def deep_filter_thinking_blocks(payload: dict, is_claude: bool = True) -> dict:
  """Recursively walk a payload dict and filter thinking blocks.

  Walks all levels of ``payload``:
  - If ``payload["contents"]`` exists (list), filters each content entry's
    ``parts`` array via ``filter_contents_thinking``.
  - If ``payload["messages"]`` exists (list), filters each message's
    ``content`` array via ``filter_contents_thinking``.
  - Handles nested ``request`` objects (Antigravity envelope may have
    ``payload.request.contents``).
  - Uses a ``set()`` of ``id(obj)`` to detect and break circular references.

  Mutates the payload in-place AND returns it.
  """
  visited: set[int] = set()

  def walk(value: object) -> None:
    if value is None:
      return
    if not isinstance(value, (dict, list)):
      return

    obj_id = id(value)
    if obj_id in visited:
      return
    visited.add(obj_id)

    if isinstance(value, list):
      for item in value:
        walk(item)
      return

    # value is a dict
    if isinstance(value.get("contents"), list):
      value["contents"] = filter_contents_thinking(value["contents"], is_claude)

    if isinstance(value.get("messages"), list):
      value["messages"] = filter_contents_thinking(value["messages"], is_claude)

    # Walk into nested request (Antigravity envelope)
    request_val = value.get("request")
    if isinstance(request_val, dict):
      walk(request_val)

    # Walk remaining dict values
    for key, val in value.items():
      if key in ("contents", "messages"):
        # Already processed above, skip to avoid double-processing
        continue
      walk(val)

  walk(payload)
  return payload
