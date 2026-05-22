"""Tests for _inject_tool_call_ids in interceptor.py."""

import copy
import unittest

from antigravity_auth.interceptor import _inject_tool_call_ids


class TestInjectToolCallIds(unittest.TestCase):
  """Unit tests for the Claude tool_call ID injection."""

  def _make_request(self, contents):
    return {"contents": contents, "generationConfig": {"temperature": 0.7}}

  def test_no_contents_key_does_nothing(self):
    req = {"generationConfig": {}}
    orig = copy.deepcopy(req)
    _inject_tool_call_ids(req)
    self.assertEqual(req, orig)

  def test_empty_contents_does_nothing(self):
    req = self._make_request([])
    orig = copy.deepcopy(req)
    _inject_tool_call_ids(req)
    self.assertEqual(req, orig)

  def test_contents_without_function_calls_unchanged(self):
    req = self._make_request([
      {"role": "user", "parts": [{"text": "hello"}]},
    ])
    orig = copy.deepcopy(req)
    _inject_tool_call_ids(req)
    self.assertEqual(req, orig)

  def test_assigns_ids_to_function_calls(self):
    req = self._make_request([
      {"role": "model", "parts": [
        {"functionCall": {"name": "read_file", "args": {"path": "/x"}}},
      ]},
    ])
    _inject_tool_call_ids(req)
    fc = req["contents"][0]["parts"][0]["functionCall"]
    self.assertEqual(fc["id"], "tool-call-1")

  def test_assigns_sequential_ids(self):
    req = self._make_request([
      {"role": "model", "parts": [
        {"functionCall": {"name": "read_file", "args": {}}},
        {"functionCall": {"name": "search", "args": {}}},
      ]},
    ])
    _inject_tool_call_ids(req)
    self.assertEqual(
      req["contents"][0]["parts"][0]["functionCall"]["id"], "tool-call-1")
    self.assertEqual(
      req["contents"][0]["parts"][1]["functionCall"]["id"], "tool-call-2")

  def test_matches_function_responses_by_name_fifo(self):
    req = self._make_request([
      {"role": "model", "parts": [
        {"functionCall": {"name": "read_file", "args": {"path": "a"}}},
        {"functionCall": {"name": "read_file", "args": {"path": "b"}}},
      ]},
      {"role": "user", "parts": [
        {"functionResponse": {"name": "read_file", "response": {"output": "a"}}},
        {"functionResponse": {"name": "read_file", "response": {"output": "b"}}},
      ]},
    ])
    _inject_tool_call_ids(req)
    # First call gets tool-call-1, second gets tool-call-2
    self.assertEqual(
      req["contents"][0]["parts"][0]["functionCall"]["id"], "tool-call-1")
    self.assertEqual(
      req["contents"][0]["parts"][1]["functionCall"]["id"], "tool-call-2")
    # Responses match FIFO: first response gets tool-call-1, second gets tool-call-2
    self.assertEqual(
      req["contents"][1]["parts"][0]["functionResponse"]["id"], "tool-call-1")
    self.assertEqual(
      req["contents"][1]["parts"][1]["functionResponse"]["id"], "tool-call-2")

  def test_does_not_overwrite_existing_ids(self):
    req = self._make_request([
      {"role": "model", "parts": [
        {"functionCall": {"name": "read", "args": {}, "id": "existing-id"}},
      ]},
    ])
    _inject_tool_call_ids(req)
    self.assertEqual(
      req["contents"][0]["parts"][0]["functionCall"]["id"], "existing-id")

  def test_response_without_matching_call_gets_no_id(self):
    req = self._make_request([
      {"role": "user", "parts": [
        {"functionResponse": {"name": "orphan", "response": {}}},
      ]},
    ])
    _inject_tool_call_ids(req)
    self.assertNotIn("id",
      req["contents"][0]["parts"][0]["functionResponse"])

  def test_mixed_non_dict_parts_ignored(self):
    req = self._make_request([
      {"role": "model", "parts": [
        "not-a-dict",
        {"functionCall": {"name": "read", "args": {}}},
      ]},
    ])
    _inject_tool_call_ids(req)
    # String part is preserved
    self.assertEqual(req["contents"][0]["parts"][0], "not-a-dict")
    # functionCall gets an ID
    self.assertEqual(
      req["contents"][0]["parts"][1]["functionCall"]["id"], "tool-call-1")
