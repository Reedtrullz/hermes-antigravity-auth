from __future__ import annotations

import unittest

from antigravity_auth.transform.thinking import (
  _strip_cache_control,
  deep_filter_thinking_blocks,
  filter_contents_thinking,
  has_signature_field,
  is_thinking_part,
  is_tool_block,
  sanitize_thinking_part,
  strip_all_thinking_blocks,
  strip_thinking_blocks,
)


class TestIsThinkingPart(unittest.TestCase):
  """Tests for ``is_thinking_part``."""

  def test_thought_true(self):
    self.assertTrue(is_thinking_part({"thought": True, "text": "..."}))

  def test_thinking_type(self):
    self.assertTrue(is_thinking_part({"type": "thinking", "thinking": "..."}))

  def test_redacted_thinking_type(self):
    self.assertTrue(is_thinking_part({"type": "redacted_thinking"}))

  def test_reasoning_type(self):
    self.assertTrue(is_thinking_part({"type": "reasoning", "text": "..."}))

  def test_thinking_field(self):
    self.assertTrue(is_thinking_part({"thinking": "some content"}))

  def test_non_thinking_part(self):
    self.assertFalse(is_thinking_part({"type": "text", "text": "hello"}))

  def test_null_or_non_dict(self):
    self.assertFalse(is_thinking_part(None))
    self.assertFalse(is_thinking_part("string"))
    self.assertFalse(is_thinking_part(42))
    self.assertFalse(is_thinking_part([]))

  def test_empty_dict(self):
    self.assertFalse(is_thinking_part({}))


class TestHasSignatureField(unittest.TestCase):
  """Tests for ``has_signature_field``."""

  def test_signature_key(self):
    self.assertTrue(has_signature_field({"signature": "abc123"}))

  def test_thought_signature_key(self):
    self.assertTrue(has_signature_field({"thoughtSignature": "abc123"}))

  def test_both_signatures(self):
    self.assertTrue(has_signature_field({
      "signature": "abc",
      "thoughtSignature": "def",
    }))

  def test_no_signature(self):
    self.assertFalse(has_signature_field({"type": "text", "text": "hi"}))

  def test_null_or_non_dict(self):
    self.assertFalse(has_signature_field(None))
    self.assertFalse(has_signature_field("string"))


class TestIsToolBlock(unittest.TestCase):
  """Tests for ``is_tool_block``."""

  def test_tool_use_type(self):
    self.assertTrue(is_tool_block({"type": "tool_use", "name": "get_weather"}))

  def test_tool_result_type(self):
    self.assertTrue(is_tool_block({"type": "tool_result", "tool_use_id": "call_1"}))

  def test_tool_use_id_field(self):
    self.assertTrue(is_tool_block({"tool_use_id": "call_1", "content": "..."}))

  def test_tool_call_id_field(self):
    self.assertTrue(is_tool_block({"tool_call_id": "call_1"}))

  def test_function_call_field(self):
    self.assertTrue(is_tool_block({"functionCall": {"name": "foo"}}))

  def test_function_response_field(self):
    self.assertTrue(is_tool_block({"functionResponse": {"name": "foo"}}))

  def test_non_tool_part(self):
    self.assertFalse(is_tool_block({"type": "text", "text": "hi"}))

  def test_thinking_part_not_tool(self):
    self.assertFalse(is_tool_block({"type": "thinking", "thinking": "..."}))

  def test_null_or_non_dict(self):
    self.assertFalse(is_tool_block(None))
    self.assertFalse(is_tool_block(42))


class TestStripCacheControl(unittest.TestCase):
  """Tests for ``_strip_cache_control``."""

  def test_strips_cache_control(self):
    result = _strip_cache_control({"text": "hi", "cache_control": {"ttl": 100}})
    self.assertEqual(result, {"text": "hi"})

  def test_strips_provider_options(self):
    result = _strip_cache_control({
      "text": "hi",
      "providerOptions": {"model": "claude"},
    })
    self.assertEqual(result, {"text": "hi"})

  def test_strips_nested_cache_control(self):
    result = _strip_cache_control({
      "thinking": {"text": "content", "cache_control": {"ttl": 100}},
      "type": "thinking",
    })
    self.assertEqual(result, {"thinking": {"text": "content"}, "type": "thinking"})

  def test_strips_in_lists(self):
    result = _strip_cache_control([
      {"text": "a", "cache_control": {"ttl": 100}},
      {"text": "b"},
    ])
    self.assertEqual(result, [{"text": "a"}, {"text": "b"}])

  def test_preserves_non_matching(self):
    result = _strip_cache_control({"text": "hello", "type": "text"})
    self.assertEqual(result, {"text": "hello", "type": "text"})

  def handles_none_and_primitives(self):
    self.assertIsNone(_strip_cache_control(None))
    self.assertEqual(_strip_cache_control(42), 42)
    self.assertEqual(_strip_cache_control("str"), "str")
    self.assertEqual(_strip_cache_control(True), True)


class TestSanitizeThinkingPart(unittest.TestCase):
  """Tests for ``sanitize_thinking_part``."""

  def test_gemini_thought_with_text(self):
    part = {"thought": True, "text": "I am thinking..."}
    result = sanitize_thinking_part(part)
    self.assertEqual(result, {"thought": True, "text": "I am thinking..."})

  def test_gemini_thought_with_signature(self):
    part = {"thought": True, "text": "", "thoughtSignature": "sig123"}
    result = sanitize_thinking_part(part)
    # Empty text is preserved alongside signature (matches TS behavior)
    self.assertEqual(result, {"thought": True, "text": "", "thoughtSignature": "sig123"})

  def test_gemini_thought_empty_no_sig_dropped(self):
    part = {"thought": True, "text": ""}
    result = sanitize_thinking_part(part)
    self.assertIsNone(result)

  def test_anthropic_thinking_with_content(self):
    part = {"type": "thinking", "thinking": "I am thinking..."}
    result = sanitize_thinking_part(part)
    self.assertEqual(result, {"type": "thinking", "thinking": "I am thinking..."})

  def test_anthropic_thinking_with_signature(self):
    part = {"type": "thinking", "thinking": "", "signature": "sig456"}
    result = sanitize_thinking_part(part)
    self.assertEqual(result, {"type": "thinking", "signature": "sig456"})

  def test_redacted_thinking_empty_dropped(self):
    """Redacted thinking with no content and no signature returns None."""
    part = {"type": "redacted_thinking"}
    result = sanitize_thinking_part(part)
    self.assertIsNone(result)

  def test_reasoning_block(self):
    part = {"type": "reasoning", "text": "reasoning content"}
    result = sanitize_thinking_part(part)
    self.assertEqual(result, {"type": "reasoning", "text": "reasoning content"})

  def test_reasoning_block_empty_no_sig(self):
    part = {"type": "reasoning", "text": ""}
    result = sanitize_thinking_part(part)
    self.assertIsNone(result)

  def test_non_thinking_part_fallback(self):
    part = {"type": "text", "text": "hello", "cache_control": {"ttl": 100}}
    result = sanitize_thinking_part(part)
    self.assertEqual(result, {"type": "text", "text": "hello"})

  def test_returns_none_for_non_dict(self):
    self.assertIsNone(sanitize_thinking_part(None))
    self.assertIsNone(sanitize_thinking_part("string"))


class TestStripAllThinkingBlocks(unittest.TestCase):
  """Tests for ``strip_all_thinking_blocks``."""

  def test_strips_thinking_part(self):
    parts = [
      {"type": "text", "text": "hello"},
      {"type": "thinking", "thinking": "..."},
      {"type": "text", "text": "world"},
    ]
    result = strip_all_thinking_blocks(parts)
    self.assertEqual(result, [
      {"type": "text", "text": "hello"},
      {"type": "text", "text": "world"},
    ])

  def test_preserves_tool_blocks(self):
    parts = [
      {"type": "thinking", "thinking": "..."},
      {"type": "tool_use", "name": "get_weather", "input": {"loc": "NYC"}},
      {"thought": True, "text": "..."},
    ]
    result = strip_all_thinking_blocks(parts)
    self.assertEqual(result, [
      {"type": "tool_use", "name": "get_weather", "input": {"loc": "NYC"}},
    ])

  def test_strips_signature_only_parts(self):
    parts = [
      {"text": "keep me"},
      {"signature": "abc123"},
      {"thoughtSignature": "def456"},
    ]
    result = strip_all_thinking_blocks(parts)
    self.assertEqual(result, [
      {"text": "keep me"},
    ])

  def test_mixed_content(self):
    parts = [
      {"type": "text", "text": "hello"},
      {"thought": True, "text": "gemini thinking"},
      {"type": "tool_result", "tool_use_id": "call_1", "content": "result"},
      {"type": "thinking", "thinking": "claude thinking"},
      {"type": "text", "text": "world"},
      {"type": "reasoning", "text": "reasoning"},
    ]
    result = strip_all_thinking_blocks(parts)
    self.assertEqual(result, [
      {"type": "text", "text": "hello"},
      {"type": "tool_result", "tool_use_id": "call_1", "content": "result"},
      {"type": "text", "text": "world"},
    ])

  def test_non_dict_items_preserved(self):
    parts = ["string", 42, {"type": "thinking", "thinking": "..."}, None]
    result = strip_all_thinking_blocks(parts)
    self.assertEqual(result, ["string", 42, None])

  def test_empty_list(self):
    self.assertEqual(strip_all_thinking_blocks([]), [])

  def test_all_thinking(self):
    parts = [
      {"type": "thinking", "thinking": "a"},
      {"thought": True, "text": "b"},
      {"type": "reasoning", "text": "c"},
    ]
    result = strip_all_thinking_blocks(parts)
    self.assertEqual(result, [])


class TestStripThinkingBlocks(unittest.TestCase):
  """Tests for ``strip_thinking_blocks``."""

  def test_strips_from_all_roles_when_claude(self):
    contents = [
      {"role": "user", "parts": [{"type": "text", "text": "hi"}]},
      {"role": "model", "parts": [
        {"type": "thinking", "thinking": "..."},
        {"type": "text", "text": "hello"},
      ]},
    ]
    result = strip_thinking_blocks(contents, is_claude=True)
    self.assertEqual(len(result), 2)
    self.assertEqual(result[1]["parts"], [
      {"type": "text", "text": "hello"},
    ])

  def test_passthrough_when_not_claude(self):
    contents = [
      {"role": "model", "parts": [
        {"type": "thinking", "thinking": "..."},
        {"type": "text", "text": "hello"},
      ]},
    ]
    result = strip_thinking_blocks(contents, is_claude=False)
    self.assertEqual(result, contents)

  def test_anthropic_content_format(self):
    contents = [
      {"role": "assistant", "content": [
        {"type": "thinking", "thinking": "..."},
        {"type": "text", "text": "hello"},
      ]},
    ]
    result = strip_thinking_blocks(contents, is_claude=True)
    self.assertEqual(len(result), 1)
    self.assertEqual(result[0]["content"], [
      {"type": "text", "text": "hello"},
    ])

  def test_non_dict_items_preserved(self):
    contents = ["raw string", None]
    result = strip_thinking_blocks(contents, is_claude=True)
    self.assertEqual(result, ["raw string", None])


class TestFilterContentsThinking(unittest.TestCase):
  """Tests for ``filter_contents_thinking``."""

  def test_strips_model_role(self):
    contents = [
      {"role": "model", "parts": [
        {"type": "thinking", "thinking": "..."},
        {"type": "text", "text": "hi"},
      ]},
    ]
    result = filter_contents_thinking(contents, is_claude=True)
    self.assertEqual(result[0]["parts"], [{"type": "text", "text": "hi"}])

  def test_strips_assistant_role(self):
    contents = [
      {"role": "assistant", "parts": [
        {"thinking": "..."},
        {"type": "text", "text": "hi"},
      ]},
    ]
    result = filter_contents_thinking(contents, is_claude=True)
    self.assertEqual(result[0]["parts"], [{"type": "text", "text": "hi"}])

  def test_strips_user_role(self):
    contents = [
      {"role": "user", "parts": [
        {"type": "thinking", "thinking": "..."},
        {"text": "hi"},
      ]},
    ]
    result = filter_contents_thinking(contents, is_claude=True)
    self.assertEqual(result[0]["parts"], [{"text": "hi"}])

  def test_strips_roleless_entry(self):
    contents = [
      {"parts": [
        {"type": "thinking", "thinking": "..."},
        {"text": "hi"},
      ]},
    ]
    result = filter_contents_thinking(contents, is_claude=True)
    self.assertEqual(result[0]["parts"], [{"text": "hi"}])

  def test_preserves_all_when_not_claude(self):
    contents = [
      {"role": "model", "parts": [
        {"type": "thinking", "thinking": "..."},
      ]},
    ]
    result = filter_contents_thinking(contents, is_claude=False)
    self.assertEqual(result, contents)

  def test_preserves_tool_blocks_in_model_role(self):
    contents = [
      {"role": "model", "parts": [
        {"type": "thinking", "thinking": "..."},
        {"type": "tool_use", "name": "get_weather"},
        {"type": "text", "text": "done"},
      ]},
    ]
    result = filter_contents_thinking(contents, is_claude=True)
    self.assertEqual(result[0]["parts"], [
      {"type": "tool_use", "name": "get_weather"},
      {"type": "text", "text": "done"},
    ])


class TestDeepFilterThinkingBlocks(unittest.TestCase):
  """Tests for ``deep_filter_thinking_blocks``."""

  def test_filters_contents_key(self):
    payload = {
      "contents": [
        {"role": "model", "parts": [
          {"type": "thinking", "thinking": "..."},
          {"type": "text", "text": "hi"},
        ]},
      ],
    }
    result = deep_filter_thinking_blocks(payload, is_claude=True)
    self.assertEqual(
      result["contents"][0]["parts"],
      [{"type": "text", "text": "hi"}],
    )

  def test_filters_messages_key(self):
    payload = {
      "messages": [
        {"role": "assistant", "content": [
          {"type": "thinking", "thinking": "..."},
          {"type": "text", "text": "hi"},
        ]},
      ],
    }
    result = deep_filter_thinking_blocks(payload, is_claude=True)
    self.assertEqual(
      result["messages"][0]["content"],
      [{"type": "text", "text": "hi"}],
    )

  def test_handles_nested_request_envelope(self):
    payload = {
      "request": {
        "contents": [
          {"role": "model", "parts": [
            {"type": "thinking", "thinking": "..."},
            {"type": "text", "text": "hi"},
          ]},
        ],
      },
    }
    result = deep_filter_thinking_blocks(payload, is_claude=True)
    self.assertEqual(
      result["request"]["contents"][0]["parts"],
      [{"type": "text", "text": "hi"}],
    )

  def test_handles_nested_request_inside_request(self):
    payload = {
      "request": {
        "request": {
          "contents": [
            {"role": "model", "parts": [
              {"type": "thinking", "thinking": "..."},
              {"type": "text", "text": "hi"},
            ]},
          ],
        },
      },
    }
    result = deep_filter_thinking_blocks(payload, is_claude=True)
    self.assertEqual(
      result["request"]["request"]["contents"][0]["parts"],
      [{"type": "text", "text": "hi"}],
    )

  def test_non_claude_passthrough(self):
    payload = {
      "contents": [
        {"role": "model", "parts": [
          {"type": "thinking", "thinking": "..."},
        ]},
      ],
    }
    result = deep_filter_thinking_blocks(payload, is_claude=False)
    self.assertEqual(result, payload)

  def test_circular_reference_no_infinite_loop(self):
    inner: dict = {"type": "text", "text": "hi"}
    payload: dict = {
      "contents": [
        {"role": "model", "parts": [inner]},
      ],
    }
    payload["self_ref"] = payload
    inner["circular"] = payload

    result = deep_filter_thinking_blocks(payload, is_claude=True)
    # Verify no crash and contents were filtered
    self.assertIsNotNone(result)
    self.assertIs(result["self_ref"], result)
    parts = result["contents"][0]["parts"]
    self.assertEqual(len(parts), 1)
    self.assertEqual(parts[0].get("type"), "text")
    self.assertEqual(parts[0].get("text"), "hi")
    # The circular key remains on the inner part (walker only filters, doesn't strip keys)
    self.assertIn("circular", parts[0])
    self.assertIs(parts[0]["circular"], payload)

  def test_mixed_contents_and_messages(self):
    payload = {
      "contents": [
        {"role": "model", "parts": [
          {"type": "thinking", "thinking": "a"},
          {"type": "text", "text": "b"},
        ]},
      ],
      "messages": [
        {"role": "assistant", "content": [
          {"type": "thinking", "thinking": "c"},
          {"type": "text", "text": "d"},
        ]},
      ],
    }
    result = deep_filter_thinking_blocks(payload, is_claude=True)
    self.assertEqual(result["contents"][0]["parts"], [{"type": "text", "text": "b"}])
    self.assertEqual(result["messages"][0]["content"], [{"type": "text", "text": "d"}])

  def test_mutates_in_place(self):
    payload: dict = {
      "contents": [
        {"role": "model", "parts": [
          {"type": "thinking", "thinking": "..."},
          {"type": "text", "text": "hi"},
        ]},
      ],
    }
    result = deep_filter_thinking_blocks(payload, is_claude=True)
    self.assertIs(result, payload)
    self.assertEqual(
      payload["contents"][0]["parts"],
      [{"type": "text", "text": "hi"}],
    )


if __name__ == "__main__":
  unittest.main()
