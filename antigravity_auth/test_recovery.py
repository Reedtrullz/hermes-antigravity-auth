import unittest

from antigravity_auth.recovery import (
    detect_error_type,
    is_recoverable_error,
    get_recovery_toast_content,
    get_recovery_success_toast,
    get_recovery_failure_toast,
    extract_message_index,
    get_error_message,
)


class TestDetectErrorType(unittest.TestCase):
    def test_tool_result_missing(self):
        msg = "Some error about tool_use without corresponding tool_result"
        self.assertEqual(detect_error_type(msg), "tool_result_missing")

    def test_tool_result_missing_dict(self):
        err = {"message": "Tool tool_use without corresponding tool_result"}
        self.assertEqual(detect_error_type(err), "tool_result_missing")

    def test_thinking_block_order_first_block(self):
        self.assertEqual(
            detect_error_type("thinking block must be the first block"),
            "thinking_block_order",
        )

    def test_thinking_block_order_must_start_with(self):
        self.assertEqual(
            detect_error_type("thinking block must start with"),
            "thinking_block_order",
        )

    def test_thinking_block_order_preceeding(self):
        self.assertEqual(
            detect_error_type("preceeding thinking block"),
            "thinking_block_order",
        )

    def test_thinking_block_order_preceding(self):
        self.assertEqual(
            detect_error_type("preceding thinking block"),
            "thinking_block_order",
        )

    def test_thinking_block_order_expected_found(self):
        self.assertEqual(
            detect_error_type("expected thinking but found text"),
            "thinking_block_order",
        )

    def test_thinking_block_order_expected_a_thinking(self):
        self.assertEqual(
            detect_error_type("expected a thinking block but found text"),
            "thinking_block_order",
        )

    def test_thinking_disabled_violation(self):
        self.assertEqual(
            detect_error_type("thinking is disabled and content cannot contain thinking blocks"),
            "thinking_disabled_violation",
        )

    def test_thinking_disabled_no_cannot_contain(self):
        self.assertIsNone(detect_error_type("thinking is disabled but still thinking"))

    def test_unrelated_error_returns_none(self):
        self.assertIsNone(detect_error_type("some random error message"))

    def test_empty_string_returns_none(self):
        self.assertIsNone(detect_error_type(""))

    def test_none_returns_none(self):
        self.assertIsNone(detect_error_type(None))

    def test_case_insensitivity(self):
        self.assertEqual(
            detect_error_type("TOOL_USE without corresponding TOOL_RESULT"),
            "tool_result_missing",
        )


class TestIsRecoverableError(unittest.TestCase):
    def test_recoverable_error(self):
        self.assertTrue(is_recoverable_error("tool_use without corresponding tool_result"))

    def test_non_recoverable_error(self):
        self.assertFalse(is_recoverable_error("random error"))


class TestGetRecoveryToastContent(unittest.TestCase):
    def test_tool_result_missing(self):
        toast = get_recovery_toast_content("tool_result_missing")
        self.assertEqual(toast["title"], "Tool Crash Recovery")
        self.assertEqual(toast["message"], "Injecting cancelled tool results...")

    def test_thinking_block_order(self):
        toast = get_recovery_toast_content("thinking_block_order")
        self.assertEqual(toast["title"], "Thinking Block Recovery")
        self.assertEqual(toast["message"], "Fixing message structure...")

    def test_thinking_disabled_violation(self):
        toast = get_recovery_toast_content("thinking_disabled_violation")
        self.assertEqual(toast["title"], "Thinking Strip Recovery")
        self.assertEqual(toast["message"], "Stripping thinking blocks...")

    def test_unknown_type(self):
        toast = get_recovery_toast_content("unknown_type")
        self.assertEqual(toast["title"], "Session Recovery")
        self.assertEqual(toast["message"], "Attempting to recover session...")

    def test_none_type(self):
        toast = get_recovery_toast_content(None)
        self.assertEqual(toast["title"], "Session Recovery")
        self.assertEqual(toast["message"], "Attempting to recover session...")


class TestGetRecoveryToasts(unittest.TestCase):
    def test_success_toast(self):
        toast = get_recovery_success_toast()
        self.assertEqual(toast["title"], "Session Recovered")
        self.assertEqual(toast["message"], "Continuing where you left off...")

    def test_failure_toast(self):
        toast = get_recovery_failure_toast()
        self.assertEqual(toast["title"], "Recovery Failed")
        self.assertEqual(toast["message"], "Please retry or start a new session.")


class TestExtractMessageIndex(unittest.TestCase):
    def test_extracts_index_from_string(self):
        self.assertEqual(extract_message_index("messages.79"), 79)

    def test_extracts_index_from_dict(self):
        self.assertEqual(extract_message_index({"message": "error at messages.123"}), 123)

    def test_no_match_returns_none(self):
        self.assertIsNone(extract_message_index("no index here"))

    def test_empty_string(self):
        self.assertIsNone(extract_message_index(""))


class TestGetErrorMessage(unittest.TestCase):
    def test_none(self):
        self.assertEqual(get_error_message(None), "")

    def test_string(self):
        self.assertEqual(get_error_message("Hello World"), "hello world")

    def test_dict_with_data_message(self):
        err = {"data": {"message": "Something went wrong"}}
        self.assertEqual(get_error_message(err), "something went wrong")

    def test_dict_with_error_message(self):
        err = {"error": {"message": "Forbidden"}}
        self.assertEqual(get_error_message(err), "forbidden")

    def test_dict_with_direct_message(self):
        err = {"message": "Direct error"}
        self.assertEqual(get_error_message(err), "direct error")

    def test_dict_with_data_containing_error(self):
        err = {"data": {"error": {"message": "Nested error"}}}
        self.assertEqual(get_error_message(err), "nested error")

    def test_empty_dict(self):
        self.assertEqual(get_error_message({}), "")


if __name__ == "__main__":
    unittest.main()
