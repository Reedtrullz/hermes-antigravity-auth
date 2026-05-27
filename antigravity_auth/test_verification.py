import os
import tempfile
import unittest
from unittest.mock import patch

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
    def test_syncs_rotated_refresh_token_to_google_oauth(self):
        from antigravity_auth.verification import probe_account_health

        account = {
            "email": "user@example.com",
            "refreshToken": "old-refresh",
            "projectId": "proj-1",
            "managedProjectId": "managed-1",
        }
        refresh_calls = []
        synced = []

        def fake_refresh(auth, **kwargs):
            refresh_calls.append(auth["refresh"])
            return {
                "access": "new-access",
                "refresh": "new-refresh|proj-1|managed-1",
                "expires": 123,
            }

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {"HERMES_HOME": tmpdir}), \
                 patch("antigravity_auth.verification.refresh_access_token", side_effect=fake_refresh), \
                 patch("antigravity_auth.verification.verify_account_access", return_value=VerificationProbeResult("ok", "ok")), \
                 patch("antigravity_auth.auth_sync.sync_token_to_google_oauth", side_effect=lambda **kw: synced.append(kw) or True):
                result = probe_account_health(account)

        self.assertEqual(result.status, "ok")
        self.assertEqual(refresh_calls, ["old-refresh|proj-1|managed-1"])
        self.assertEqual(synced[0]["refresh_token"], "new-refresh|proj-1|managed-1")
        self.assertEqual(synced[0]["project_id"], "proj-1")
        self.assertEqual(synced[0]["email"], "user@example.com")
        self.assertEqual(synced[0]["expires_ms"], 123)

    def test_google_oauth_sync_failure_is_non_fatal(self):
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
                 patch("antigravity_auth.auth_sync.sync_token_to_google_oauth", side_effect=RuntimeError("sync boom")):
                result = probe_account_health(account)

        self.assertEqual(result.status, "ok")


if __name__ == "__main__":
    unittest.main()
