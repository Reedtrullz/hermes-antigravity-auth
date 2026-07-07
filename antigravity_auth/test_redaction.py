import unittest


class TestRedaction(unittest.TestCase):
    def test_redact_secrets_removes_nested_tokens_but_keeps_metadata(self):
        from antigravity_auth.redaction import REDACTED, redact_secrets

        raw = {
            "Authorization": "Bearer raw-access-token",
            "tokens": {
                "access_token": "raw-access-token",
                "refreshToken": "raw-refresh-token",
                "accessTokenExpiresAt": 123,
            },
            "snapshot": {
                "access_token_cached": True,
                "access_token_expires_at": 456,
                "lastRefreshAt": 789,
            },
            "url": "https://example.test/callback?" + "code=" + "oauth-code-secret" + "&client_secret=" + "client-secret",
        }

        redacted = redact_secrets(raw)
        rendered = str(redacted)
        self.assertNotIn("raw-access-token", rendered)
        self.assertNotIn("raw-refresh-token", rendered)
        self.assertNotIn("oauth-code-secret", rendered)
        self.assertNotIn("client-secret", rendered)
        self.assertEqual(redacted["tokens"]["access_token"], REDACTED)
        self.assertEqual(redacted["tokens"]["refreshToken"], REDACTED)
        self.assertEqual(redacted["tokens"]["accessTokenExpiresAt"], 123)
        self.assertTrue(redacted["snapshot"]["access_token_cached"])
        self.assertEqual(redacted["snapshot"]["access_token_expires_at"], 456)


class TestSessionTokenRedaction(unittest.TestCase):
    def test_session_token_snake_case_is_secret(self):
        from antigravity_auth.redaction import _is_secret_key
        self.assertTrue(_is_secret_key("session_token"))

    def test_sessionToken_camelCase_is_secret(self):
        from antigravity_auth.redaction import _is_secret_key
        self.assertTrue(_is_secret_key("sessionToken"))

    def test_device_session_token_is_secret(self):
        from antigravity_auth.redaction import _is_secret_key
        self.assertTrue(_is_secret_key("device_session_token"))

    def test_fingerprint_session_token_redacted_in_dict(self):
        from antigravity_auth.redaction import redact_secrets
        fp = {
            "deviceId": "abc-123",
            "sessionToken": "deadbeef1234567890abcdef",
            "userAgent": "Mozilla/5.0",
        }
        redacted = redact_secrets(fp)
        self.assertEqual(redacted["deviceId"], "abc-123")
        self.assertEqual(redacted["sessionToken"], "[REDACTED]")
        self.assertEqual(redacted["userAgent"], "Mozilla/5.0")

    def test_session_token_free_form_shapes_are_redacted(self):
        from antigravity_auth.redaction import redact_secret_text

        rendered = redact_secret_text(
            'url=https://example.test/?sessionToken=query-secret '
            'json={"sessionToken":"json-secret"} '
            "repr={'session_token': 'repr-secret'} "
            "form=session_token=form-secret"
        )

        for secret in ("query-secret", "json-secret", "repr-secret", "form-secret"):
            self.assertNotIn(secret, rendered)
        self.assertIn("[REDACTED]", rendered)

    def test_camel_case_url_and_form_secret_names_are_redacted(self):
        from antigravity_auth.redaction import redact_secret_text

        rendered = redact_secret_text(
            "https://example.test/?accessToken=query-access&refreshToken=query-refresh&clientSecret=query-secret "
            "accessToken=form-access refreshToken=form-refresh clientSecret=form-secret"
        )

        for secret in (
            "query-access",
            "query-refresh",
            "query-secret",
            "form-access",
            "form-refresh",
            "form-secret",
        ):
            self.assertNotIn(secret, rendered)
        self.assertIn("[REDACTED]", rendered)


class TestApiKeyRedaction(unittest.TestCase):
    def test_common_api_key_shapes_are_redacted(self):
        from antigravity_auth.redaction import REDACTED, redact_secrets

        raw = {
            "X-Goog-Api-Key": "header-secret",
            "apiKey": "camel-secret",
            "url": "https://example.test/?key=query-secret&api_key=api-secret",
            "json": '{"x-api-key":"json-secret","apiKey":"json-camel-secret"}',
            "form": "api_key=form-secret",
            "headers": "x-goog-api-key: text-header-secret",
        }

        redacted = redact_secrets(raw)
        rendered = str(redacted)
        for secret in (
            "header-secret",
            "camel-secret",
            "query-secret",
            "api-secret",
            "json-secret",
            "json-camel-secret",
            "form-secret",
            "text-header-secret",
        ):
            self.assertNotIn(secret, rendered)
        self.assertEqual(redacted["X-Goog-Api-Key"], REDACTED)
        self.assertEqual(redacted["apiKey"], REDACTED)

    def test_python_repr_secret_shapes_are_redacted(self):
        from antigravity_auth.redaction import redact_secret_text

        rendered = redact_secret_text(
            "{'refreshToken': 'refresh-secret', 'client_secret': 'client-secret'}"
        )
        self.assertNotIn("refresh-secret", rendered)
        self.assertNotIn("client-secret", rendered)
        self.assertIn("[REDACTED]", rendered)

    def test_cookie_and_broad_secret_header_shapes_are_redacted(self):
        from antigravity_auth.redaction import REDACTED, redact_secrets

        raw = {
            "Set-Cookie": "session=raw-cookie",
            "headers": (
                "Proxy-Authorization: proxy-secret\n"
                "X-Api-Token: provider-token\n"
                "X-Credential: provider-credential\n"
                "X-Password: provider-password\n"
                "Cookie: inline-cookie"
            ),
            "url": "https://example.test/?cookie=query-cookie&setCookie=query-set-cookie",
            "body": '{"cookie":"json-cookie","set_cookie":"json-set-cookie"} cookie=form-cookie',
        }

        redacted = redact_secrets(raw)
        rendered = str(redacted)

        for secret in (
            "raw-cookie",
            "proxy-secret",
            "provider-token",
            "provider-credential",
            "provider-password",
            "inline-cookie",
            "query-cookie",
            "query-set-cookie",
            "json-cookie",
            "json-set-cookie",
            "form-cookie",
        ):
            self.assertNotIn(secret, rendered)
        self.assertEqual(redacted["Set-Cookie"], REDACTED)
