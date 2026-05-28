import os
import tempfile
import unittest

from antigravity_auth.debug import (
    createLogger,
    truncate_text,
    format_body_preview,
    format_error_for_log,
    _mask_headers,
    initialize_debug,
    is_debug_enabled,
    get_log_file_path,
)


class TestCreateLogger(unittest.TestCase):
    def test_returns_logger_with_methods(self):
        logger = createLogger("test-module")
        self.assertTrue(hasattr(logger, "debug"))
        self.assertTrue(hasattr(logger, "info"))
        self.assertTrue(hasattr(logger, "warn"))
        self.assertTrue(hasattr(logger, "error"))

    def test_logger_info_is_callable(self):
        logger = createLogger("test-module")
        self.assertTrue(callable(logger.info))

    def test_logger_error_is_callable(self):
        logger = createLogger("test-module")
        self.assertTrue(callable(logger.error))


class TestTruncateText(unittest.TestCase):
    def test_short_text_not_truncated(self):
        text = "Hello, world!"
        self.assertEqual(truncate_text(text, 100), text)

    def test_long_text_truncated(self):
        text = "a" * 1000
        result = truncate_text(text, 100)
        self.assertEqual(len(result), 100 + len("... (truncated 900 chars)"))
        self.assertTrue(result.endswith("... (truncated 900 chars)"))

    def test_boundary_exact_length(self):
        text = "a" * 100
        result = truncate_text(text, 100)
        self.assertEqual(result, text)


class TestFormatBodyPreview(unittest.TestCase):
    def test_none_returns_empty(self):
        self.assertEqual(format_body_preview(None), "")

    def test_string_is_truncated(self):
        body = "x" * 50
        result = format_body_preview(body, 100)
        self.assertEqual(result, body)

    def test_long_string_is_truncated(self):
        body = "x" * 200
        result = format_body_preview(body, 50)
        self.assertIn("... (truncated", result)


class TestFormatErrorForLog(unittest.TestCase):
    def test_none_returns_empty(self):
        self.assertEqual(format_error_for_log(None), "")

    def test_string_returns_itself(self):
        self.assertEqual(format_error_for_log("error message"), "error message")

    def test_exception_returns_str(self):
        err = ValueError("something broke")
        self.assertEqual(format_error_for_log(err), "something broke")


class TestMaskHeaders(unittest.TestCase):
    def test_none_returns_empty(self):
        self.assertEqual(_mask_headers(None), {})

    def test_authorization_header_redacted(self):
        headers = {"Authorization": "Bearer secret123", "Content-Type": "application/json"}
        result = _mask_headers(headers)
        self.assertEqual(result["Authorization"], "[redacted]")
        self.assertEqual(result["Content-Type"], "application/json")

    def test_authorization_case_insensitive(self):
        headers = {"authorization": "Bearer secret"}
        result = _mask_headers(headers)
        self.assertEqual(result["authorization"], "[redacted]")

    def test_empty_dict(self):
        self.assertEqual(_mask_headers({}), {})


class TestInitializeDebug(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_log_dir = os.environ.get("HERMES_ANTIGRAVITY_LOG_DIR")
        os.environ["HERMES_ANTIGRAVITY_LOG_DIR"] = self.temp_dir.name

    def tearDown(self):
        initialize_debug(False)
        if self.original_log_dir is not None:
            os.environ["HERMES_ANTIGRAVITY_LOG_DIR"] = self.original_log_dir
        else:
            os.environ.pop("HERMES_ANTIGRAVITY_LOG_DIR", None)
        self.temp_dir.cleanup()

    def test_debug_disabled(self):
        initialize_debug(False)
        self.assertFalse(is_debug_enabled())
        self.assertIsNone(get_log_file_path())

    def test_debug_enabled(self):
        initialize_debug(True, log_dir=self.temp_dir.name)
        self.assertTrue(is_debug_enabled())
        path = get_log_file_path()
        self.assertIsNotNone(path)
        self.assertIn("antigravity-debug-", path)

    def test_debug_enabled_creates_log_file(self):
        initialize_debug(True, log_dir=self.temp_dir.name)
        path = get_log_file_path()
        self.assertTrue(os.path.exists(path))

    def test_debug_tui(self):
        initialize_debug(False, config_debug_tui=True)

    def test_logger_writes_when_debug_enabled(self):
        initialize_debug(True, log_dir=self.temp_dir.name)
        logger = createLogger("test-module")
        logger.info("test message")
        path = get_log_file_path()
        with open(path, "r") as f:
            content = f.read()
        self.assertIn("[antigravity.test-module] info: test message", content)

    def test_debug_log_file_is_private(self):
        import os
        import stat
        import tempfile
        from antigravity_auth.debug import initialize_debug, get_log_file_path

        with tempfile.TemporaryDirectory() as tmp:
            old_umask = os.umask(0o022)
            try:
                initialize_debug(True, log_dir=tmp)
            finally:
                os.umask(old_umask)
            path = get_log_file_path()
            self.assertIsNotNone(path)
            self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)


class TestSanitizeBody(unittest.TestCase):
    def test_sanitize_body_redacts_camel_case_and_bearer(self):
        from antigravity_auth.debug import _sanitize_body

        body = '{"accessToken":"abc","refreshToken":"def","authorization":"Bearer xyz"}'
        sanitized = _sanitize_body(body)
        self.assertNotIn("abc", sanitized)
        self.assertNotIn("def", sanitized)
        self.assertNotIn("Bearer xyz", sanitized)


if __name__ == "__main__":
    unittest.main()
