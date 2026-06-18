"""Tests for _apply_claude_transforms in interceptor.py."""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from antigravity_auth.interceptor import _apply_claude_transforms


class TestApplyClaudeTransforms(unittest.TestCase):
  """Unit tests for Claude-specific request transforms."""

  def setUp(self):
    self.get_config_patcher = patch(
      "antigravity_auth.interceptor.get_config",
      return_value=SimpleNamespace(keep_thinking=False),
    )
    self.mock_get_config = self.get_config_patcher.start()

  def tearDown(self):
    self.get_config_patcher.stop()

  def _make_request(self, **overrides):
    req = {
      "contents": [],
      "generationConfig": {
        "temperature": 0.7,
        "thinkingConfig": {
          "thinkingBudget": 16000,
          "includeThoughts": True,
        },
      },
      "toolConfig": {"functionCallingConfig": {"mode": "AUTO"}},
      "tools": [{"functionDeclarations": []}],
    }
    req.update(overrides)
    return req

  def test_sets_validated_mode(self):
    req = self._make_request()
    _apply_claude_transforms(req)
    self.assertEqual(
      req["toolConfig"]["functionCallingConfig"]["mode"], "VALIDATED")

  def test_sets_validated_when_no_existing_config(self):
    req = self._make_request(toolConfig={"functionCallingConfig": {}})
    _apply_claude_transforms(req)
    self.assertEqual(
      req["toolConfig"]["functionCallingConfig"]["mode"], "VALIDATED")

  def test_converts_thinking_config_to_snake_case(self):
    req = self._make_request()
    _apply_claude_transforms(req)
    tc = req["generationConfig"]["thinkingConfig"]
    self.assertNotIn("thinkingBudget", tc)
    self.assertNotIn("includeThoughts", tc)
    self.assertEqual(tc["thinking_budget"], 16000)
    self.assertEqual(tc["include_thoughts"], True)

  def test_no_thinking_config_is_safe(self):
    req = self._make_request(generationConfig={"temperature": 0.7})
    _apply_claude_transforms(req)
    # No crash — just toolConfig was updated (VALIDATED mode)
    self.assertEqual(req["generationConfig"], {"temperature": 0.7})

  def test_adds_placeholder_for_missing_required(self):
    req = self._make_request(tools=[{"functionDeclarations": [{
      "name": "search",
      "parameters": {
        "type": "object",
        "properties": {"query": {"type": "string"}},
        # No required field
      },
    }]}])
    _apply_claude_transforms(req)
    params = req["tools"][0]["functionDeclarations"][0]["parameters"]
    self.assertEqual(params["required"], ["_placeholder"])
    self.assertIn("_placeholder", params["properties"])
    self.assertEqual(params["properties"]["_placeholder"]["type"], "boolean")

  def test_adds_placeholder_for_empty_required(self):
    req = self._make_request(tools=[{"functionDeclarations": [{
      "name": "search",
      "parameters": {
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": [],  # Empty list
      },
    }]}])
    _apply_claude_transforms(req)
    params = req["tools"][0]["functionDeclarations"][0]["parameters"]
    self.assertEqual(params["required"], ["_placeholder"])

  def test_adds_placeholder_for_truly_empty_object_schema(self):
    req = self._make_request(tools=[{"functionDeclarations": [{
      "name": "noop",
      "parameters": {"type": "object"},
    }]}])
    _apply_claude_transforms(req)
    params = req["tools"][0]["functionDeclarations"][0]["parameters"]
    self.assertEqual(params["required"], ["_placeholder"])
    self.assertEqual(
      params["properties"]["_placeholder"],
      {"type": "boolean", "description": "Placeholder. Always pass true."},
    )

  def test_preserves_existing_required(self):
    req = self._make_request(tools=[{"functionDeclarations": [{
      "name": "read_file",
      "parameters": {
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"],
      },
    }]}])
    _apply_claude_transforms(req)
    params = req["tools"][0]["functionDeclarations"][0]["parameters"]
    self.assertEqual(params["required"], ["path"])
    self.assertNotIn("_placeholder", params["properties"])

  def test_sanitizes_tool_parameters_before_validated_mode(self):
    req = self._make_request(tools=[{"functionDeclarations": [{
      "name": "search",
      "parameters": {
        "type": "object",
        "additionalProperties": False,
        "$defs": {"Unused": {"type": "object"}},
        "properties": {
          "mode": {
            "const": "web",
            "default": "web",
            "minLength": 1,
          },
        },
        "required": ["mode"],
      },
    }]}])

    _apply_claude_transforms(req)

    params = req["tools"][0]["functionDeclarations"][0]["parameters"]
    rendered = str(params)
    self.assertNotIn("additionalProperties", params)
    self.assertNotIn("$defs", params)
    self.assertNotIn("const", rendered)
    self.assertNotIn("default", params["properties"]["mode"])
    self.assertNotIn("minLength", params["properties"]["mode"])
    self.assertEqual(params["required"], ["mode"])
    self.assertEqual(params["properties"]["mode"]["enum"], ["web"])

  def test_no_tools_is_safe(self):
    req = self._make_request()
    del req["tools"]
    _apply_claude_transforms(req)
    # toolConfig and thinkingConfig were updated (always applies for Claude)
    # tools missing is fine — no crash
    self.assertNotIn("tools", req)
    self.assertEqual(
      req["toolConfig"]["functionCallingConfig"]["mode"], "VALIDATED")

  def test_apply_claude_transforms_creates_validated_tool_config_when_tools_exist(self):
    inner: dict[str, object] = {
      "tools": [{
        "functionDeclarations": [{
          "name": "x",
          "parameters": {"type": "object", "properties": {}},
        }],
      }],
    }
    _apply_claude_transforms(inner)
    tool_config = inner.get("toolConfig")
    if not isinstance(tool_config, dict):
      self.fail("toolConfig was not created")
    function_calling_config = tool_config.get("functionCallingConfig")
    if not isinstance(function_calling_config, dict):
      self.fail("functionCallingConfig was not created")
    self.assertEqual(function_calling_config["mode"], "VALIDATED")

  def test_apply_claude_transforms_strips_stale_thinking_parts_by_default(self):
    inner = {
      "contents": [{
        "role": "model",
        "parts": [
          {"thought": True, "text": "old reasoning", "thoughtSignature": "sig"},
          {"text": "visible"},
        ],
      }],
    }
    _apply_claude_transforms(inner)
    self.assertEqual(inner["contents"][0]["parts"], [{"text": "visible"}])
    self.mock_get_config.assert_called_once()

  def test_apply_claude_transforms_preserves_thinking_parts_when_configured(self):
    self.mock_get_config.return_value = SimpleNamespace(keep_thinking=True)
    inner = {
      "contents": [{
        "role": "model",
        "parts": [
          {"thought": True, "text": "old reasoning", "thoughtSignature": "sig"},
          {"text": "visible"},
        ],
      }],
    }
    _apply_claude_transforms(inner)
    self.assertEqual(inner["contents"][0]["parts"], [
      {"thought": True, "text": "old reasoning", "thoughtSignature": "sig"},
      {"text": "visible"},
    ])
