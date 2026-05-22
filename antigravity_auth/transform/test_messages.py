from __future__ import annotations

import unittest

from antigravity_auth.transform.messages import (
  is_claude_model,
  is_gemini_model,
  is_gpt_oss_model,
  parse_data_url,
  transform_messages_to_contents,
)


class TestIsClaudeModel(unittest.TestCase):
  def test_claude_model(self):
    self.assertTrue(is_claude_model("claude-opus-4-6-thinking"))
    self.assertTrue(is_claude_model("claude-sonnet-4-6"))
    self.assertTrue(is_claude_model("claude-sonnet-4-6-thinking"))

  def test_non_claude_model(self):
    self.assertFalse(is_claude_model("gemini-3-flash"))
    self.assertFalse(is_claude_model("gemini-2.5-pro"))
    self.assertFalse(is_claude_model("gpt-oss-120b-medium"))


class TestIsGeminiModel(unittest.TestCase):
  def test_gemini_model(self):
    self.assertTrue(is_gemini_model("gemini-3-flash"))
    self.assertTrue(is_gemini_model("gemini-2.5-pro"))
    self.assertTrue(is_gemini_model("gemini-3.1-pro-high"))
    self.assertTrue(is_gemini_model("gemini-3.1-pro-low"))
    self.assertTrue(is_gemini_model("gemini-3.5-flash-high"))
    self.assertTrue(is_gemini_model("gemini-3.5-flash-medium"))

  def test_gemini_excludes_claude(self):
    self.assertFalse(is_gemini_model("claude-opus-4-6"))
    self.assertFalse(is_gemini_model("claude-sonnet-4-6-thinking"))

  def test_gemini_name_does_not_contain_claude(self):
    self.assertTrue(is_gemini_model("gemini-3-pro"))

  def test_gemini_excludes_gpt_oss(self):
    self.assertFalse(is_gemini_model("gpt-oss-120b-medium"))


class TestIsGptOssModel(unittest.TestCase):
  def test_gpt_oss_model(self):
    self.assertTrue(is_gpt_oss_model("gpt-oss-120b-medium"))

  def test_non_gpt_oss_model(self):
    self.assertFalse(is_gpt_oss_model("claude-sonnet-4-6"))
    self.assertFalse(is_gpt_oss_model("gemini-3.5-flash-high"))
    self.assertFalse(is_gpt_oss_model("gemini-3.1-pro-low"))


class TestParseDataUrl(unittest.TestCase):
  def test_valid_data_url(self):
    url = "data:image/png;base64,iVBORw0KGgo="
    result = parse_data_url(url)
    self.assertIsNotNone(result)
    mime_type, data = result
    self.assertEqual(mime_type, "image/png")
    self.assertEqual(data, "iVBORw0KGgo=")

  def test_valid_data_url_with_slash_in_mime(self):
    url = "data:application/pdf;base64,JVBERi0xLjc="
    result = parse_data_url(url)
    self.assertIsNotNone(result)
    mime_type, data = result
    self.assertEqual(mime_type, "application/pdf")
    self.assertEqual(data, "JVBERi0xLjc=")

  def test_invalid_data_url(self):
    self.assertIsNone(parse_data_url("not-a-data-url"))
    self.assertIsNone(parse_data_url("https://example.com/image.png"))

  def test_empty_string(self):
    self.assertIsNone(parse_data_url(""))

  def test_missing_base64(self):
    url = "data:text/plain;base64,"
    result = parse_data_url(url)
    self.assertIsNone(result)


class TestTransformMessagesToContents(unittest.TestCase):
  def test_simple_user_message(self):
    messages = [
      {"role": "user", "content": "Hello"}
    ]
    contents, system = transform_messages_to_contents(messages)
    self.assertEqual(len(contents), 1)
    self.assertEqual(contents[0]["role"], "user")
    self.assertEqual(contents[0]["parts"], [{"text": "Hello"}])
    self.assertIsNone(system)

  def test_user_and_assistant(self):
    messages = [
      {"role": "user", "content": "Hi there"},
      {"role": "assistant", "content": "How can I help?"},
    ]
    contents, system = transform_messages_to_contents(messages)
    self.assertEqual(len(contents), 2)
    self.assertEqual(contents[0]["role"], "user")
    self.assertEqual(contents[0]["parts"], [{"text": "Hi there"}])
    self.assertEqual(contents[1]["role"], "model")
    self.assertEqual(contents[1]["parts"], [{"text": "How can I help?"}])
    self.assertIsNone(system)

  def test_system_message_extracted(self):
    messages = [
      {"role": "system", "content": "You are a helpful assistant."},
      {"role": "user", "content": "Hello"},
    ]
    contents, system = transform_messages_to_contents(messages)
    self.assertEqual(len(contents), 1)
    self.assertEqual(contents[0]["role"], "user")
    self.assertIsNotNone(system)
    self.assertEqual(system["parts"][0]["text"], "You are a helpful assistant.")

  def test_system_message_with_user_first(self):
    """System message should be extracted to system instruction even if it comes first."""
    messages = [
      {"role": "system", "content": "Be concise."},
      {"role": "user", "content": "Tell me about Python"},
      {"role": "assistant", "content": "Python is a language."},
    ]
    contents, system = transform_messages_to_contents(messages)
    self.assertEqual(len(contents), 2)
    self.assertIsNotNone(system)
    self.assertEqual(system["parts"][0]["text"], "Be concise.")

  def test_consecutive_user_merged(self):
    messages = [
      {"role": "user", "content": "First question"},
      {"role": "user", "content": "Second question"},
    ]
    contents, system = transform_messages_to_contents(messages)
    self.assertEqual(len(contents), 1)
    self.assertEqual(contents[0]["role"], "user")
    self.assertEqual(contents[0]["parts"], [
      {"text": "First question"},
      {"text": "Second question"},
    ])

  def test_consecutive_model_not_merged_with_function_response(self):
    """Consecutive tool messages (user role) with functionResponse should not merge
    with a user text message."""
    messages = [
      {"role": "user", "content": "What's the weather?"},
      {"role": "assistant", "content": "", "tool_calls": [
        {"id": "call_1", "type": "function", "function": {"name": "get_weather", "arguments": '{"city": "NYC"}'}}
      ]},
      {"role": "tool", "tool_call_id": "call_1", "name": "get_weather", "content": "72°F"},
      {"role": "user", "content": "Thanks"},
    ]
    contents, system = transform_messages_to_contents(messages)
    self.assertEqual(len(contents), 4)
    self.assertEqual(contents[2]["role"], "user")
    self.assertIn("functionResponse", contents[2]["parts"][0])
    self.assertEqual(contents[3]["role"], "user")
    self.assertEqual(contents[3]["parts"][0]["text"], "Thanks")

  def test_consecutive_same_role_with_fr_does_merge(self):
    """Two consecutive tool messages both produce functionResponse - they SHOULD merge."""
    messages = [
      {"role": "user", "content": "Check both tools"},
      {"role": "assistant", "content": "Running tools", "tool_calls": [
        {"id": "c1", "type": "function", "function": {"name": "get_weather", "arguments": '{"city": "NYC"}'}},
        {"id": "c2", "type": "function", "function": {"name": "get_time", "arguments": '{"tz": "EST"}'}},
      ]},
      {"role": "tool", "tool_call_id": "c1", "name": "get_weather", "content": "72°F"},
      {"role": "tool", "tool_call_id": "c2", "name": "get_time", "content": "3pm"},
    ]
    contents, system = transform_messages_to_contents(messages)
    self.assertEqual(len(contents), 3)
    self.assertEqual(contents[2]["role"], "user")
    self.assertEqual(len(contents[2]["parts"]), 2)

  def test_assistant_with_tool_calls(self):
    messages = [
      {"role": "user", "content": "Weather in Paris?"},
      {"role": "assistant", "content": "Let me check", "tool_calls": [
        {"id": "call_abc", "type": "function", "function": {
          "name": "get_weather",
          "arguments": '{"city": "Paris"}',
        }}
      ]},
    ]
    contents, system = transform_messages_to_contents(messages)
    self.assertEqual(len(contents), 2)
    self.assertEqual(contents[0]["role"], "user")
    self.assertEqual(contents[1]["role"], "model")
    parts = contents[1]["parts"]
    self.assertEqual(len(parts), 2)
    self.assertEqual(parts[0], {"text": "Let me check"})
    self.assertEqual(parts[1], {"functionCall": {"name": "get_weather", "args": {"city": "Paris"}}})

  def test_tool_result_message(self):
    messages = [
      {"role": "user", "content": "What's NYC weather?"},
      {"role": "assistant", "content": "", "tool_calls": [
        {"id": "call_1", "type": "function", "function": {"name": "get_weather", "arguments": '{"city": "NYC"}'}}
      ]},
      {"role": "tool", "tool_call_id": "call_1", "name": "get_weather", "content": "Sunny, 72°F"},
    ]
    contents, system = transform_messages_to_contents(messages)
    self.assertEqual(len(contents), 3)
    tool_content = contents[2]
    self.assertEqual(tool_content["role"], "user")
    fr_part = tool_content["parts"][0]
    self.assertEqual(fr_part["functionResponse"]["name"], "get_weather")
    self.assertEqual(fr_part["functionResponse"]["response"]["content"], "Sunny, 72°F")

  def test_multi_part_content_text_and_image(self):
    messages = [
      {
        "role": "user",
        "content": [
          {"type": "text", "text": "What's in this image?"},
          {"type": "image_url", "image_url": {
            "url": "data:image/jpeg;base64,/9j/4AAQSkZJRg=="
          }},
        ]
      },
    ]
    contents, system = transform_messages_to_contents(messages)
    self.assertEqual(len(contents), 1)
    parts = contents[0]["parts"]
    self.assertEqual(len(parts), 2)
    self.assertEqual(parts[0], {"text": "What's in this image?"})
    self.assertEqual(parts[1], {"inlineData": {"mimeType": "image/jpeg", "data": "/9j/4AAQSkZJRg=="}})

  def test_multi_turn_conversation(self):
    messages = [
      {"role": "user", "content": "Hello"},
      {"role": "assistant", "content": "Hi there!"},
      {"role": "user", "content": "How do I sort a list?"},
      {"role": "assistant", "content": "Use sorted() or list.sort()"},
    ]
    contents, system = transform_messages_to_contents(messages)
    self.assertEqual(len(contents), 4)
    self.assertEqual(contents[0]["role"], "user")
    self.assertEqual(contents[1]["role"], "model")
    self.assertEqual(contents[2]["role"], "user")
    self.assertEqual(contents[3]["role"], "model")

  def test_empty_messages(self):
    contents, system = transform_messages_to_contents([])
    self.assertEqual(contents, [])
    self.assertIsNone(system)

  def test_none_content(self):
    messages = [
      {"role": "user", "content": None},
    ]
    contents, system = transform_messages_to_contents(messages)
    self.assertEqual(len(contents), 0)
    self.assertIsNone(system)

  def test_assistant_with_tool_calls_and_no_content(self):
    messages = [
      {"role": "user", "content": "Go"},
      {"role": "assistant", "tool_calls": [
        {"id": "c1", "type": "function", "function": {"name": "bash", "arguments": '{"cmd": "ls"}'}}
      ]},
    ]
    contents, system = transform_messages_to_contents(messages)
    self.assertEqual(len(contents), 2)
    self.assertEqual(contents[1]["role"], "model")
    self.assertEqual(len(contents[1]["parts"]), 1)
    self.assertEqual(contents[1]["parts"][0], {"functionCall": {"name": "bash", "args": {"cmd": "ls"}}})

  def test_tool_calls_arguments_as_dict(self):
    """arguments may already be parsed as dict by some SDKs."""
    messages = [
      {"role": "user", "content": "Run command"},
      {"role": "assistant", "content": "", "tool_calls": [
        {"id": "c1", "type": "function", "function": {"name": "bash", "arguments": {"cmd": "df -h"}}}
      ]},
    ]
    contents, system = transform_messages_to_contents(messages)
    self.assertEqual(contents[1]["parts"][0]["functionCall"]["args"], {"cmd": "df -h"})

  def test_system_message_with_list_content(self):
    messages = [
      {"role": "system", "content": [{"type": "text", "text": "Be concise."}]},
      {"role": "user", "content": "Hello"},
    ]
    contents, system = transform_messages_to_contents(messages)
    self.assertIsNotNone(system)
    self.assertEqual(system["parts"][0]["text"], "Be concise.")

  def test_malformed_message_skipped(self):
    messages = [
      None,
      "not a dict",
      42,
      {"role": "user", "content": "Hello"},
    ]
    contents, system = transform_messages_to_contents(messages)
    self.assertEqual(len(contents), 1)
    self.assertEqual(contents[0]["parts"], [{"text": "Hello"}])

  def test_tool_use_content_part_type(self):
    """Supports Anthropic-style tool_use parts within content arrays."""
    messages = [
      {"role": "user", "content": "What's the weather?"},
      {"role": "assistant", "content": [
        {"type": "text", "text": "Checking weather..."},
        {"type": "tool_use", "id": "tu1", "name": "get_weather", "input": {"city": "Tokyo"}},
      ]},
    ]
    contents, system = transform_messages_to_contents(messages)
    self.assertEqual(len(contents[1]["parts"]), 2)
    self.assertEqual(contents[1]["parts"][0], {"text": "Checking weather..."})
    self.assertEqual(
      contents[1]["parts"][1],
      {"functionCall": {"name": "get_weather", "args": {"city": "Tokyo"}}},
    )

  def test_tool_result_content_part_type(self):
    """Supports Anthropic-style tool_result parts within content arrays."""
    messages = [
      {"role": "user", "content": "Do it"},
      {"role": "assistant", "content": "", "tool_calls": [
        {"id": "tu1", "type": "function", "function": {"name": "get_data", "arguments": '{"key": "val"}'}}
      ]},
      {"role": "tool", "tool_call_id": "tu1", "name": "get_data", "content": "result data"},
    ]
    contents, system = transform_messages_to_contents(messages)
    tool_entry = contents[2]
    self.assertEqual(tool_entry["role"], "user")
    self.assertEqual(
      tool_entry["parts"][0],
      {"functionResponse": {"name": "get_data", "response": {"content": "result data"}}},
    )
