import os
import tempfile
import unittest
from unittest.mock import patch


class TestDoctor(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_hermes_home = os.environ.get("HERMES_HOME")
        os.environ["HERMES_HOME"] = self.temp_dir.name

    def tearDown(self):
        if self.original_hermes_home is not None:
            os.environ["HERMES_HOME"] = self.original_hermes_home
        else:
            os.environ.pop("HERMES_HOME", None)
        self.temp_dir.cleanup()

    def test_doctor_output_redacts_refresh_access_and_bearer_tokens(self):
        from antigravity_auth.doctor import format_doctor_rows, run_doctor
        from antigravity_auth.storage import save_accounts, sync_token_to_auth_json

        save_accounts({
            "version": 4,
            "accounts": [{
                "email": "redact@example.com",
                "refreshToken": "raw-refresh-secret",
                "projectId": "project-secret",
                "accessToken": "raw-access-secret",
                "accessTokenExpiresAt": 9999999999999,
            }],
            "activeIndex": 0,
            "cursor": 0,
            "activeIndexByFamily": {"claude": 0, "gemini": 0},
        })
        sync_token_to_auth_json(
            "raw-access-secret",
            "raw-refresh-secret|project-secret",
            "project-secret",
            "redact@example.com",
        )

        with patch("antigravity_auth.doctor.refresh_access_token", side_effect=RuntimeError(
            "Authorization: Bearer raw-access-secret refresh_token=raw-refresh-secret code=oauth-code-secret"
        )):
            output = format_doctor_rows(run_doctor())

        self.assertNotIn("raw-access-secret", output)
        self.assertNotIn("raw-refresh-secret", output)
        self.assertNotIn("oauth-code-secret", output)
        self.assertIn("[REDACTED]", output)

    def test_doctor_reports_missing_hermes_adapter_as_fail(self):
        from antigravity_auth.doctor import run_doctor

        def fake_import(name):
            if name == "agent.gemini_cloudcode_adapter":
                raise ImportError("missing adapter")
            return __import__(name)

        with patch("antigravity_auth.doctor.importlib.import_module", side_effect=fake_import):
            rows = run_doctor()

        adapter_rows = [row for row in rows if row.check == "Hermes adapter import"]
        self.assertTrue(adapter_rows)
        self.assertEqual(adapter_rows[0].status, "FAIL")

    def test_doctor_reports_retry_streaming_limitation(self):
        from antigravity_auth.doctor import format_doctor_rows, run_doctor

        output = format_doctor_rows(run_doctor())

        self.assertIn("automatic retry", output)
        self.assertIn("streaming responses cannot be replayed", output)

    def test_doctor_reports_process_lock_backend(self):
        from antigravity_auth.doctor import format_doctor_rows, run_doctor

        output = format_doctor_rows(run_doctor())

        self.assertIn("account store locking", output)
