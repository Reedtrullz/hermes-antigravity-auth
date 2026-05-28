from __future__ import annotations

import json
import unittest

from antigravity_auth.transform.response import (
    extract_retry_info,
    extract_usage_from_body,
    inject_debug_thinking,
    rewrite_preview_access_error,
    transform_antigravity_response,
    ANTIGRAVITY_PREVIEW_LINK,
)


class TestExtractUsageFromBody(unittest.TestCase):
    """Tests for extract_usage_from_body."""

    def test_usage_from_response_usage_metadata(self):
        """Extracts usage from response.usageMetadata dict."""
        body = json.dumps({
            "response": {
                "usageMetadata": {
                    "totalTokenCount": 100,
                    "promptTokenCount": 40,
                    "candidatesTokenCount": 60,
                }
            }
        })
        result = extract_usage_from_body(body)
        self.assertIsNotNone(result)
        self.assertEqual(result["totalTokenCount"], 100)
        self.assertEqual(result["promptTokenCount"], 40)
        self.assertEqual(result["candidatesTokenCount"], 60)

    def test_usage_from_top_level_usage_metadata(self):
        """Extracts usage from top-level usageMetadata dict (no response wrapper)."""
        body = json.dumps({
            "usageMetadata": {
                "totalTokenCount": 50,
                "promptTokenCount": 20,
                "candidatesTokenCount": 30,
            }
        })
        result = extract_usage_from_body(body)
        self.assertIsNotNone(result)
        self.assertEqual(result["totalTokenCount"], 50)

    def test_usage_from_list_of_dicts(self):
        """Extracts usage from a list of dicts containing usageMetadata."""
        body = json.dumps([
            {"unrelated": True},
            {
                "usageMetadata": {
                    "totalTokenCount": 42,
                    "candidatesTokenCount": 42,
                }
            },
        ])
        result = extract_usage_from_body(body)
        self.assertIsNotNone(result)
        self.assertEqual(result["totalTokenCount"], 42)

    def test_none_when_no_usage(self):
        """Returns None when body has no usage data."""
        body = json.dumps({"response": {"candidates": [{"content": {"parts": [{"text": "hi"}]}}]}})
        result = extract_usage_from_body(body)
        self.assertIsNone(result)

    def test_none_for_invalid_json(self):
        """Returns None for non-JSON string."""
        result = extract_usage_from_body("not json at all")
        self.assertIsNone(result)

    def test_none_for_non_response_dict(self):
        """Returns None when parsed JSON is not a dict or list of dicts."""
        result = extract_usage_from_body('"just a string"')
        self.assertIsNone(result)

    def test_empty_usage_dict_returns_none(self):
        """Returns None when usageMetadata exists but has no recognized keys."""
        body = json.dumps({"response": {"usageMetadata": {"unknownKey": 1}}})
        result = extract_usage_from_body(body)
        self.assertIsNone(result)

    def test_usage_with_all_token_keys(self):
        """Extracts all recognized token count keys."""
        body = json.dumps({
            "response": {
                "usageMetadata": {
                    "totalTokenCount": 100,
                    "promptTokenCount": 30,
                    "candidatesTokenCount": 70,
                    "cachedContentTokenCount": 5,
                    "thoughtsTokenCount": 20,
                }
            }
        })
        result = extract_usage_from_body(body)
        self.assertEqual(result["totalTokenCount"], 100)
        self.assertEqual(result["cachedContentTokenCount"], 5)
        self.assertEqual(result["thoughtsTokenCount"], 20)

    def test_usage_from_usage_metadata_snake_case(self):
        """Extracts usage from usage_metadata key (snake_case variant)."""
        body = json.dumps({
            "response": {
                "usage_metadata": {
                    "totalTokenCount": 88,
                }
            }
        })
        result = extract_usage_from_body(body)
        self.assertIsNotNone(result)
        self.assertEqual(result["totalTokenCount"], 88)


class TestExtractRetryInfo(unittest.TestCase):
    """Tests for extract_retry_info."""

    def test_valid_retry_delay_30s(self):
        """Parses retryDelay '30s' into retryDelayMs 30000."""
        body = {
            "error": {
                "code": 429,
                "message": "Quota exceeded",
                "details": [
                    {
                        "@type": "type.googleapis.com/google.rpc.RetryInfo",
                        "retryDelay": "30s",
                    }
                ],
            }
        }
        result = extract_retry_info(body)
        self.assertIsNotNone(result)
        self.assertEqual(result["retryDelayMs"], 30000)

    def test_valid_retry_delay_1s(self):
        """Parses retryDelay '1s' into retryDelayMs 1000."""
        body = {
            "error": {
                "details": [
                    {
                        "@type": "type.googleapis.com/google.rpc.RetryInfo",
                        "retryDelay": "1s",
                    }
                ],
            }
        }
        result = extract_retry_info(body)
        self.assertIsNotNone(result)
        self.assertEqual(result["retryDelayMs"], 1000)

    def test_valid_retry_delay_with_decimal(self):
        """Parses retryDelay with decimal like '2.5s'."""
        body = {
            "error": {
                "details": [
                    {
                        "@type": "type.googleapis.com/google.rpc.RetryInfo",
                        "retryDelay": "2.5s",
                    }
                ],
            }
        }
        result = extract_retry_info(body)
        self.assertIsNotNone(result)
        self.assertEqual(result["retryDelayMs"], 2500)

    def test_none_when_no_error(self):
        """Returns None when body has no error key."""
        body = {"response": {"candidates": []}}
        result = extract_retry_info(body)
        self.assertIsNone(result)

    def test_none_when_no_details(self):
        """Returns None when error has no details list."""
        body = {"error": {"code": 500, "message": "Internal error"}}
        result = extract_retry_info(body)
        self.assertIsNone(result)

    def test_none_when_details_not_list(self):
        """Returns None when error.details is not a list."""
        body = {"error": {"details": "not a list"}}
        result = extract_retry_info(body)
        self.assertIsNone(result)

    def test_none_when_no_retry_info_in_details(self):
        """Returns None when details list has no RetryInfo item."""
        body = {
            "error": {
                "details": [
                    {"@type": "type.googleapis.com/google.rpc.BadRequest"},
                ],
            }
        }
        result = extract_retry_info(body)
        self.assertIsNone(result)

    def test_none_when_retry_delay_not_string(self):
        """Returns None when retryDelay is not a string."""
        body = {
            "error": {
                "details": [
                    {
                        "@type": "type.googleapis.com/google.rpc.RetryInfo",
                        "retryDelay": 30,
                    }
                ],
            }
        }
        result = extract_retry_info(body)
        self.assertIsNone(result)

    def test_none_for_invalid_retry_delay_format(self):
        """Returns None for invalid retryDelay format (e.g. '30ms')."""
        body = {
            "error": {
                "details": [
                    {
                        "@type": "type.googleapis.com/google.rpc.RetryInfo",
                        "retryDelay": "30ms",
                    }
                ],
            }
        }
        result = extract_retry_info(body)
        self.assertIsNone(result)

    def test_skips_non_dict_details(self):
        """Skips non-dict items in details list."""
        body = {
            "error": {
                "details": [
                    "not a dict",
                    None,
                    42,
                    {
                        "@type": "type.googleapis.com/google.rpc.RetryInfo",
                        "retryDelay": "10s",
                    },
                ],
            }
        }
        result = extract_retry_info(body)
        self.assertIsNotNone(result)
        self.assertEqual(result["retryDelayMs"], 10000)


class TestRewritePreviewAccessError(unittest.TestCase):
    """Tests for rewrite_preview_access_error."""

    def test_rewrites_404_for_claude_model(self):
        """Rewrites 404 error for a Claude model with preview access message."""
        body = {"error": {"code": 404, "message": "model not found"}}
        result = rewrite_preview_access_error(body, 404, "claude-opus-4-6-thinking")
        self.assertIsNotNone(result)
        self.assertIn("preview access", result["error"]["message"])
        self.assertIn(ANTIGRAVITY_PREVIEW_LINK, result["error"]["message"])

    def test_rewrites_404_for_antigravity_model(self):
        """Rewrites 404 for 'antigravity' in requested model name."""
        body = {"error": {"code": 404, "message": "not found"}}
        result = rewrite_preview_access_error(body, 404, "antigravity-experimental")
        self.assertIsNotNone(result)
        self.assertIn("preview access", result["error"]["message"])

    def test_rewrites_404_for_opus(self):
        """Rewrites 404 error for model name containing 'opus'."""
        body = {"error": {"message": "model missing"}}
        result = rewrite_preview_access_error(body, 404, "gemini-opus-alpha")
        self.assertIsNotNone(result)
        self.assertIn("preview access", result["error"]["message"])

    def test_no_rewrite_for_200(self):
        """Does not rewrite when status code is 200."""
        body = {"candidates": [{"content": {"parts": []}}]}
        result = rewrite_preview_access_error(body, 200, "claude-opus-4-6")
        self.assertIsNone(result)

    def test_no_rewrite_for_non_claude_404(self):
        """Does not rewrite 404 for a non-Claude/Antigravity model."""
        body = {"error": {"code": 404, "message": "model not found"}}
        result = rewrite_preview_access_error(body, 404, "gemini-2.5-flash")
        self.assertIsNone(result)

    def test_no_rewrite_for_non_404_claude(self):
        """Does not rewrite non-404 errors even for Claude models."""
        body = {"error": {"code": 500, "message": "internal error"}}
        result = rewrite_preview_access_error(body, 500, "claude-sonnet-4-6")
        self.assertIsNone(result)

    def test_rewrites_when_model_is_none_but_error_mentions_claude(self):
        """Rewrites when requested_model is None but error message contains 'claude'."""
        body = {"error": {"code": 404, "message": "claude model not available"}}
        result = rewrite_preview_access_error(body, 404, None)
        self.assertIsNotNone(result)
        self.assertIn("preview access", result["error"]["message"])

    def test_preserves_existing_error_message(self):
        """Preserves the original error message as prefix."""
        body = {"error": {"code": 404, "message": "Claude requires preview"}}
        result = rewrite_preview_access_error(body, 404, "claude-opus-4-6")
        self.assertIsNotNone(result)
        self.assertTrue(result["error"]["message"].startswith("Claude requires preview"))


class TestInjectDebugThinking(unittest.TestCase):
    """Tests for inject_debug_thinking."""

    def test_injects_into_candidates_content_parts(self):
        """Injects debug text as thought=True into candidates[0].content.parts."""
        body = {
            "candidates": [
                {
                    "content": {
                        "role": "model",
                        "parts": [{"text": "Hello world"}],
                    }
                }
            ]
        }
        result = inject_debug_thinking(body, "DEBUG: thinking trace")
        parts = result["candidates"][0]["content"]["parts"]
        self.assertEqual(len(parts), 2)
        self.assertEqual(parts[0], {"thought": True, "text": "DEBUG: thinking trace"})
        self.assertEqual(parts[1], {"text": "Hello world"})

    def test_injects_into_content_list(self):
        """Injects debug text as thinking block into content list (Anthropic format)."""
        body = {
            "id": "msg_123",
            "content": [
                {"type": "text", "text": "Response"},
            ],
        }
        result = inject_debug_thinking(body, "debug thinking")
        self.assertEqual(len(result["content"]), 2)
        self.assertEqual(
            result["content"][0],
            {"type": "thinking", "thinking": "debug thinking"},
        )
        self.assertEqual(result["content"][1], {"type": "text", "text": "Response"})

    def test_injects_as_reasoning_content_fallback(self):
        """Adds reasoning_content when no candidates or content list."""
        body = {"id": "msg_1", "model": "gemini"}
        result = inject_debug_thinking(body, "fallback debug")
        self.assertEqual(result["reasoning_content"], "fallback debug")

    def test_does_not_override_existing_reasoning_content(self):
        """Does not overwrite reasoning_content if already present."""
        body = {"id": "msg_1", "reasoning_content": "existing reasoning"}
        result = inject_debug_thinking(body, "new debug")
        self.assertEqual(result["reasoning_content"], "existing reasoning")

    def test_no_candidates_returns_body_unchanged(self):
        """Returns body unchanged when candidates list exists but first item not dict."""
        body = {"candidates": ["not a dict"]}
        result = inject_debug_thinking(body, "debug")
        self.assertEqual(result, body)

    def test_multiple_candidates_only_first_gets_injected(self):
        """Only prepends debug to the first candidate's parts."""
        body = {
            "candidates": [
                {"content": {"parts": [{"text": "A"}]}},
                {"content": {"parts": [{"text": "B"}]}},
            ]
        }
        result = inject_debug_thinking(body, "debug")
        first_parts = result["candidates"][0]["content"]["parts"]
        second_parts = result["candidates"][1]["content"]["parts"]
        self.assertEqual(len(first_parts), 2)
        self.assertEqual(len(second_parts), 1)


class TestTransformAntigravityResponse(unittest.TestCase):
    """Tests for transform_antigravity_response."""

    def test_non_json_passthrough(self):
        """Returns body unchanged for non-JSON/non-SSE content type."""
        body, extra_headers, error = transform_antigravity_response(
            "plain text response", streaming=False,
            headers={"content-type": "text/html"},
        )
        self.assertEqual(body, "plain text response")
        self.assertIsNone(extra_headers)
        self.assertIsNone(error)

    def test_non_json_bytes_passthrough(self):
        """Handles bytes body with non-JSON content type."""
        body, extra_headers, error = transform_antigravity_response(
            b"binary data", streaming=False,
            headers={"content-type": "application/octet-stream"},
        )
        self.assertEqual(body, "binary data")
        self.assertIsNone(extra_headers)
        self.assertIsNone(error)

    def test_error_response_thinking_block_order(self):
        """Returns recoveryType for thinking block order error."""
        error_body = json.dumps({
            "error": {
                "code": 400,
                "message": "Expected a thinking block first but found text",
            }
        })
        body, extra_headers, error = transform_antigravity_response(
            error_body, streaming=False, status_code=400,
            headers={"content-type": "application/json"},
            requested_model="claude-opus-4-6",
            effective_model="claude-opus-4-6",
            project_id="test-project",
            endpoint="/v1/chat/completions",
        )
        self.assertIsNotNone(error)
        self.assertEqual(error["recoveryType"], "thinking_block_order")

    def test_error_response_thinking_must_start_with(self):
        """Detects thinking order error with 'must start with' pattern."""
        error_body = json.dumps({
            "error": {
                "message": "thinking must start with first block, preceeding text found",
            }
        })
        body, extra_headers, error = transform_antigravity_response(
            error_body, streaming=False, status_code=400,
            headers={"content-type": "application/json"},
        )
        self.assertIsNotNone(error)
        self.assertEqual(error["recoveryType"], "thinking_block_order")

    def test_error_response_context_length_exceeded(self):
        """Sets context error header for prompt too long."""
        error_body = json.dumps({
            "error": {"message": "context_length_exceeded: prompt is too long"}
        })
        body, extra_headers, error = transform_antigravity_response(
            error_body, streaming=False, status_code=400,
            headers={"content-type": "application/json"},
        )
        self.assertIsNone(error)
        self.assertIsNotNone(extra_headers)
        self.assertEqual(
            extra_headers.get("x-antigravity-context-error"),
            "prompt_too_long",
        )

    def test_sse_body_passthrough(self):
        """Returns SSE body unchanged for streaming responses."""
        sse_body = 'data: {"response": {"candidates": [{"content": {"parts": [{"text": "hi"}]}}]}}\n\n'
        body, extra_headers, error = transform_antigravity_response(
            sse_body, streaming=True,
            headers={"content-type": "text/event-stream"},
        )
        self.assertEqual(body, sse_body)
        self.assertIsNone(error)

    def test_sse_with_usage_extraction(self):
        """Extracts usage headers from SSE body."""
        sse_body = (
            'data: {"response": {"usageMetadata": {"totalTokenCount": 42}}}\n\n'
        )
        body, extra_headers, error = transform_antigravity_response(
            sse_body, streaming=True,
            headers={"content-type": "text/event-stream"},
        )
        self.assertEqual(body, sse_body)
        self.assertIsNotNone(extra_headers)
        self.assertIn("x-antigravity-total-token-count", extra_headers)
        self.assertEqual(extra_headers["x-antigravity-total-token-count"], "42")

    def test_sse_passthrough_is_documented_contract(self):
        body = 'data: {"response":{"usageMetadata":{"totalTokenCount":1}}}\n\n'
        transformed, headers, extra = transform_antigravity_response(
            body,
            streaming=True,
            status_code=200,
            headers={"Content-Type": "text/event-stream"},
        )
        self.assertEqual(transformed, body)
        self.assertIsNotNone(headers)
        self.assertIsNone(extra)

    def test_sse_with_usage_extraction_without_trailing_blank_line(self):
        """Extracts usage from a final SSE event even without a blank-line terminator."""
        sse_body = 'data: {"response": {"usageMetadata": {"totalTokenCount": 43}}}'
        body, extra_headers, error = transform_antigravity_response(
            sse_body, streaming=True,
            headers={"content-type": "text/event-stream"},
        )
        self.assertEqual(body, sse_body)
        self.assertIsNone(error)
        assert extra_headers is not None
        self.assertEqual(extra_headers["x-antigravity-total-token-count"], "43")

    def test_successful_response_transformed(self):
        """Transforms successful JSON response with thinking parts."""
        response_body = json.dumps({
            "response": {
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {"text": "Hello!", "thought": False},
                            ]
                        }
                    }
                ]
            }
        })
        body, extra_headers, error = transform_antigravity_response(
            response_body, streaming=False, status_code=200,
            headers={"content-type": "application/json"},
        )
        self.assertIsNone(error)
        parsed = json.loads(body)
        self.assertIn("candidates", parsed)
        self.assertEqual(
            parsed["candidates"][0]["content"]["parts"][0]["text"],
            "Hello!",
        )

    def test_no_content_type_defaults_to_json(self):
        """Treats body as JSON when no content-type header is set."""
        response_body = json.dumps({"response": {"text": "ok"}})
        body, extra_headers, error = transform_antigravity_response(
            response_body, streaming=False, status_code=200,
        )
        parsed = json.loads(body)
        self.assertEqual(parsed["text"], "ok")

    def test_retry_info_in_error_response(self):
        """Includes Retry-After headers when retry info is present in error."""
        error_body = json.dumps({
            "error": {
                "code": 429,
                "message": "Quota exceeded",
                "details": [
                    {
                        "@type": "type.googleapis.com/google.rpc.RetryInfo",
                        "retryDelay": "30s",
                    }
                ],
            }
        })
        body, extra_headers, error = transform_antigravity_response(
            error_body, streaming=False, status_code=429,
            headers={"content-type": "application/json"},
        )
        self.assertIsNotNone(extra_headers)
        self.assertEqual(extra_headers.get("Retry-After"), "30")
        self.assertEqual(extra_headers.get("retry-after-ms"), "30000")

    def test_preview_access_rewrite_integration(self):
        """Integration: 404 for Claude model gets preview access rewrite."""
        error_body = json.dumps({
            "error": {"code": 404, "message": "not found"}
        })
        body, extra_headers, error = transform_antigravity_response(
            error_body, streaming=False, status_code=404,
            headers={"content-type": "application/json"},
            requested_model="claude-opus-4-6",
        )
        parsed = json.loads(body)
        self.assertIn("preview access", parsed["error"]["message"])

    def test_empty_body_passthrough(self):
        """Passthrough for empty body with non-JSON content type."""
        body, extra_headers, error = transform_antigravity_response(
            "", streaming=False,
            headers={"content-type": "text/plain"},
        )
        self.assertEqual(body, "")
        self.assertIsNone(error)

    def test_tool_result_missing_recovery(self):
        """tool_result_missing errors return recoveryType in error dict."""
        error_body = json.dumps({
            "error": {
                "code": 400,
                "message": "messages.3.content.tool_use without immediately after tool_result"
            }
        })
        body, extra_headers, error = transform_antigravity_response(
            error_body, streaming=False, status_code=400,
            headers={"content-type": "application/json"},
        )
        self.assertIsNotNone(error)
        self.assertEqual(error.get("recoveryType"), "tool_result_missing")
        self.assertIsNotNone(extra_headers)
        self.assertEqual(extra_headers.get("x-antigravity-context-error"), "tool_pairing")
