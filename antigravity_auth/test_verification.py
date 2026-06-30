import os
import json
import tempfile
import urllib.error
import unittest
from unittest.mock import MagicMock, patch

from antigravity_auth.verification import (
    VerificationProbeResult,
    extract_verification_error_details,
    decode_escaped_text,
    _collect_urls_from_text,
    _normalize_google_verification_url,
    _select_best_verification_url,
)


class TestVerificationProbeResult(unittest.TestCase):
    def test_default_creation(self):
        result = VerificationProbeResult(status="ok", message="All good")
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.message, "All good")
        self.assertIsNone(result.verify_url)

    def test_with_verify_url(self):
        result = VerificationProbeResult(
            status="blocked",
            message="Verification needed",
            verify_url="https://accounts.google.com/signin/continue",
        )
        self.assertEqual(result.status, "blocked")
        self.assertEqual(result.verify_url, "https://accounts.google.com/signin/continue")


class TestDecodeEscapedText(unittest.TestCase):
    def test_no_escaping(self):
        self.assertEqual(decode_escaped_text("hello world"), "hello world")

    def test_ampersand_escaping(self):
        self.assertEqual(decode_escaped_text("foo&amp;bar"), "foo&bar")

    def test_unicode_escaping(self):
        self.assertEqual(decode_escaped_text("\\u0041\\u0042"), "AB")


class TestCollectUrlsFromText(unittest.TestCase):
    def test_no_urls(self):
        self.assertEqual(_collect_urls_from_text("some random text"), [])

    def test_accounts_google_url(self):
        urls = _collect_urls_from_text("Visit https://accounts.google.com/signin/continue to verify")
        self.assertEqual(len(urls), 1)
        self.assertIn("accounts.google.com", urls[0])

    def test_multiple_urls(self):
        text = "First: https://accounts.google.com/ServiceLogin Second: https://accounts.google.com/signin/continue"
        urls = _collect_urls_from_text(text)
        self.assertEqual(len(urls), 2)

    def test_duplicate_urls_deduplicated(self):
        text = "https://accounts.google.com/signin and https://accounts.google.com/signin"
        urls = _collect_urls_from_text(text)
        self.assertEqual(len(urls), 1)


class TestNormalizeGoogleVerificationUrl(unittest.TestCase):
    def test_valid_url(self):
        url = "https://accounts.google.com/signin/continue?pli=1"
        result = _normalize_google_verification_url(url)
        self.assertEqual(result, url)

    def test_non_google_url(self):
        result = _normalize_google_verification_url("https://evil.com/phish")
        self.assertIsNone(result)

    def test_empty_string(self):
        self.assertIsNone(_normalize_google_verification_url(""))

    def test_escaped_url(self):
        url = "https://accounts.google.com/signin?continue=https%3A%2F%2Fexample.com"
        result = _normalize_google_verification_url(url)
        self.assertEqual(result, url)


class TestSelectBestVerificationUrl(unittest.TestCase):
    def test_empty_list(self):
        self.assertIsNone(_select_best_verification_url([]))

    def test_single_url(self):
        url = "https://accounts.google.com/signin"
        self.assertEqual(_select_best_verification_url([url]), url)

    def test_url_with_plt_is_preferred(self):
        urls = [
            "https://accounts.google.com/signin",
            "https://accounts.google.com/signin?plt=123",
        ]
        best = _select_best_verification_url(urls)
        self.assertIn("plt=", best)

    def test_signin_continue_preferred(self):
        urls = [
            "https://accounts.google.com/signin",
            "https://accounts.google.com/signin/continue",
        ]
        best = _select_best_verification_url(urls)
        self.assertIn("/signin/continue", best)

    def test_invalid_urls_filtered_out(self):
        urls = [
            "not-a-url",
            "https://accounts.google.com/signin",
        ]
        best = _select_best_verification_url(urls)
        self.assertEqual(best, "https://accounts.google.com/signin")


class TestExtractVerificationErrorDetails(unittest.TestCase):
    def test_clean_body(self):
        result = extract_verification_error_details("some normal text")
        self.assertFalse(result["validationRequired"])
        self.assertIsNone(result["verifyUrl"])

    def test_validation_required_in_body(self):
        result = extract_verification_error_details(
            '{"error": {"message": "validation_required"}}'
        )
        self.assertTrue(result["validationRequired"])

    def test_validation_required_in_lower_body(self):
        result = extract_verification_error_details(
            'The request requires Validation_Required check'
        )
        self.assertTrue(result["validationRequired"])

    def test_verify_url_extracted(self):
        result = extract_verification_error_details(
            '{"url": "https://accounts.google.com/signin/continue?plt=abc"}'
        )
        self.assertIsNotNone(result["verifyUrl"])
        self.assertIn("accounts.google.com", result["verifyUrl"])

    def test_sse_data_parsed(self):
        sse_body = (
            "data: {\"text\": \"streaming response\"}\n\n"
            "data: {\"error\": {\"message\": \"validation_required\"}}\n\n"
            "data: [DONE]"
        )
        result = extract_verification_error_details(sse_body)
        self.assertTrue(result["validationRequired"])

    def test_verification_required_phrases(self):
        result = extract_verification_error_details("Verification Required for account access")
        self.assertTrue(result["validationRequired"])

    def test_verify_your_account_phrase(self):
        result = extract_verification_error_details("Please verify your account to continue")
        self.assertTrue(result["validationRequired"])

    def test_account_verification_phrase(self):
        result = extract_verification_error_details("Account verification is needed")
        self.assertTrue(result["validationRequired"])

    def test_message_extracted_from_json(self):
        result = extract_verification_error_details(
            '{"error": {"message": "Access denied", "status": "PERMISSION_DENIED"}}'
        )
        self.assertEqual(result["message"], "Access denied")

    def test_verify_url_from_sse_line(self):
        body = (
            "data: {\"error\": {\"message\": \"validation_required\", "
            '"verification_url": "https://accounts.google.com/signin/continue?plt=123"}}'
        )
        result = extract_verification_error_details(body)
        self.assertTrue(result["validationRequired"])
        self.assertIsNotNone(result["verifyUrl"])

    def test_non_json_body_fallback_message(self):
        body = (
            "An error occurred: verification required. "
            "Please check your account."
        )
        result = extract_verification_error_details(body)
        self.assertTrue(result["validationRequired"])
        self.assertIsNotNone(result["message"])


class TestProbeAccountHealth(unittest.TestCase):
    @patch("antigravity_auth.verification.urllib.request.urlopen")
    def test_verify_account_access_includes_project_in_header_and_envelope(self, mock_urlopen):
        from antigravity_auth.verification import verify_account_access

        mock_response = MagicMock()
        mock_response.read.return_value = b""
        mock_urlopen.return_value.__enter__.return_value = mock_response

        result = verify_account_access(
            {"email": "user@example.com"},
            "access-token",
            project_id="managed-project",
        )

        self.assertEqual(result.status, "ok")
        req = mock_urlopen.call_args.args[0]
        body = json.loads(req.data.decode("utf-8"))
        self.assertEqual(body["project"], "managed-project")
        self.assertEqual(body["requestType"], "agent")
        self.assertEqual(body["userAgent"], "antigravity")
        self.assertTrue(body["requestId"].startswith("agent-"))
        self.assertEqual(body["model"], "gemini-3.5-flash-low")
        self.assertEqual(body["request"]["model"], "gemini-3.5-flash-low")
        self.assertEqual(req.headers["X-goog-user-project"], "managed-project")
        self.assertEqual(req.headers["Accept"], "text/event-stream")

    @patch("antigravity_auth.verification.urllib.request.urlopen")
    def test_verify_account_access_detects_http_200_validation_error_sse(self, mock_urlopen):
        from antigravity_auth.verification import verify_account_access

        mock_response = MagicMock()
        mock_response.read.return_value = (
            b'data: {"error": {"message": "validation_required clientSecret=blocked-secret", '
            b'"verification_url": "https://accounts.google.com/signin/continue?plt=123"}}\n\n'
        )
        mock_urlopen.return_value.__enter__.return_value = mock_response

        result = verify_account_access(
            {"email": "user@example.com"},
            "access-token",
            project_id="managed-project",
        )

        self.assertEqual(result.status, "blocked")
        self.assertNotIn("blocked-secret", result.message)
        self.assertIn("[REDACTED]", result.message)
        self.assertIsNotNone(result.verify_url)
        self.assertIn("accounts.google.com", result.verify_url)

    @patch("antigravity_auth.verification.urllib.request.urlopen")
    def test_verify_account_access_detects_http_200_inband_error_sse(self, mock_urlopen):
        from antigravity_auth.verification import verify_account_access

        mock_response = MagicMock()
        mock_response.read.return_value = b'data: {"error": {"message": "quota exhausted"}}\n\n'
        mock_urlopen.return_value.__enter__.return_value = mock_response

        result = verify_account_access(
            {"email": "user@example.com"},
            "access-token",
            project_id="managed-project",
        )

        self.assertEqual(result.status, "error")
        self.assertIn("quota exhausted", result.message)

    @patch("antigravity_auth.verification.urllib.request.urlopen")
    def test_verify_account_access_redacts_error_message(self, mock_urlopen):
        from antigravity_auth.verification import verify_account_access

        class ErrorBody:
            def read(self):
                return b'{"error":{"message":"client_secret=verify-secret refresh_token=verify-refresh"}}'

            def close(self):
                pass

        mock_urlopen.side_effect = urllib.error.HTTPError(
            "https://example.invalid",
            500,
            "Internal Server Error",
            {},
            ErrorBody(),
        )

        result = verify_account_access(
            {"email": "user@example.com"},
            "access-token",
            project_id="managed-project",
        )

        self.assertEqual(result.status, "error")
        self.assertNotIn("verify-secret", result.message)
        self.assertNotIn("verify-refresh", result.message)
        self.assertIn("[REDACTED]", result.message)

    @patch("antigravity_auth.verification.urllib.request.urlopen")
    def test_verify_account_access_redacts_validation_required_message(self, mock_urlopen):
        from antigravity_auth.verification import verify_account_access

        class ErrorBody:
            def read(self):
                return b'{"error":{"message":"validation_required clientSecret=blocked-secret"}}'

            def close(self):
                pass

        mock_urlopen.side_effect = urllib.error.HTTPError(
            "https://example.invalid",
            403,
            "Forbidden",
            {},
            ErrorBody(),
        )

        result = verify_account_access(
            {"email": "user@example.com"},
            "access-token",
            project_id="managed-project",
        )

        self.assertEqual(result.status, "blocked")
        self.assertNotIn("blocked-secret", result.message)
        self.assertIn("[REDACTED]", result.message)

    def test_probe_uses_refreshed_access_without_rewriting_auth_stores(self):
        from antigravity_auth import verification
        from antigravity_auth.storage import get_active_token_from_auth_json, sync_token_to_auth_json
        from antigravity_auth.verification import probe_account_health

        account = {
            "email": "user@example.com",
            "refreshToken": "old-refresh",
            "projectId": "proj-1",
            "managedProjectId": "managed-1",
        }
        refresh_calls = []
        verify_calls = []

        def fake_refresh(auth, **kwargs):
            refresh_calls.append(auth["refresh"])
            return {
                "access": "new-access",
                "refresh": "new-refresh|proj-1|managed-1",
                "expires": 123,
            }

        def fake_verify(account, access_token, project_id=None):
            verify_calls.append({
                "account": account,
                "access_token": access_token,
                "project_id": project_id,
            })
            return VerificationProbeResult("ok", "ok")

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {"HERMES_HOME": tmpdir}):
                sync_token_to_auth_json(
                    "active-access",
                    "active-refresh|active-proj",
                    "active-proj",
                    "active@example.com",
                    set_active=True,
                )

                with patch("antigravity_auth.verification.refresh_access_token", side_effect=fake_refresh), \
                     patch("antigravity_auth.verification.verify_account_access", side_effect=fake_verify), \
                     patch("antigravity_auth.verification.sync_token_to_auth_json", wraps=verification.sync_token_to_auth_json) as auth_json_sync, \
                     patch("antigravity_auth.auth_sync.sync_token_to_google_oauth") as google_oauth_sync:
                    result = probe_account_health(account)

                active = get_active_token_from_auth_json()

        self.assertEqual(result.status, "ok")
        self.assertEqual(refresh_calls, ["old-refresh|proj-1|managed-1"])
        self.assertEqual(verify_calls[0]["access_token"], "new-access")
        self.assertEqual(verify_calls[0]["project_id"], "managed-1")
        auth_json_sync.assert_not_called()
        google_oauth_sync.assert_not_called()
        self.assertEqual(active["access_token"], "active-access")
        self.assertEqual(active["refresh_token"], "active-refresh|active-proj")
        self.assertEqual(active["project_id"], "active-proj")

    def test_probe_does_not_call_auth_store_sync_even_when_sync_would_fail(self):
        from antigravity_auth.verification import probe_account_health

        account = {
            "email": "user@example.com",
            "refreshToken": "old-refresh",
            "projectId": "proj-1",
            "managedProjectId": "managed-1",
        }
        refreshed = {
            "access": "new-access",
            "refresh": "new-refresh|proj-1|managed-1",
            "expires": 123,
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {"HERMES_HOME": tmpdir}), \
                 patch("antigravity_auth.verification.refresh_access_token", return_value=refreshed), \
                 patch("antigravity_auth.verification.verify_account_access", return_value=VerificationProbeResult("ok", "ok")), \
                 patch("antigravity_auth.verification.sync_token_to_auth_json", side_effect=AssertionError("auth.json sync must not be called")), \
                 patch("antigravity_auth.auth_sync.sync_token_to_google_oauth", side_effect=AssertionError("google_oauth sync must not be called")):
                result = probe_account_health(account)

        self.assertEqual(result.status, "ok")

    def test_probe_account_health_redacts_refresh_error(self):
        from antigravity_auth.verification import probe_account_health

        account = {
            "email": "user@example.com",
            "refreshToken": "old-refresh",
            "projectId": "proj-1",
        }

        with patch(
            "antigravity_auth.verification.refresh_access_token",
            side_effect=RuntimeError("client_secret=verify-secret refresh_token=verify-refresh"),
        ):
            result = probe_account_health(account)

        self.assertEqual(result.status, "error")
        self.assertNotIn("verify-secret", result.message)
        self.assertNotIn("verify-refresh", result.message)
        self.assertIn("[REDACTED]", result.message)


if __name__ == "__main__":
    unittest.main()
