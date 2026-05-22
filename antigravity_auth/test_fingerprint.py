import unittest

from antigravity_auth.fingerprint import (
    generate_fingerprint,
    generate_device_id,
    generate_session_token,
    build_fingerprint_headers,
    update_fingerprint_version,
)


class TestGenerateDeviceId(unittest.TestCase):
    def test_returns_uuid_string(self):
        device_id = generate_device_id()
        self.assertIsInstance(device_id, str)
        self.assertEqual(len(device_id), 36)

    def test_unique_across_calls(self):
        ids = {generate_device_id() for _ in range(10)}
        self.assertEqual(len(ids), 10)


class TestGenerateSessionToken(unittest.TestCase):
    def test_returns_hex_string(self):
        token = generate_session_token()
        self.assertIsInstance(token, str)
        self.assertEqual(len(token), 32)
        # Verify it's hex
        int(token, 16)


class TestGenerateFingerprint(unittest.TestCase):
    def test_has_required_keys(self):
        fp = generate_fingerprint()
        required = {"deviceId", "sessionToken", "userAgent", "apiClient",
                     "clientMetadata", "createdAt"}
        self.assertTrue(
            required.issubset(set(fp.keys())),
            f"Missing keys: {required - set(fp.keys())}"
        )

    def test_client_metadata_has_required_keys(self):
        fp = generate_fingerprint()
        metadata = fp["clientMetadata"]
        required = {"ideType", "platform", "pluginType"}
        self.assertTrue(
            required.issubset(set(metadata.keys())),
            f"Missing keys: {required - set(metadata.keys())}"
        )

    def test_user_agent_contains_antigravity(self):
        fp = generate_fingerprint()
        self.assertIn("Antigravity", fp["userAgent"])


class TestBuildFingerprintHeaders(unittest.TestCase):
    def test_none_returns_empty_dict(self):
        result = build_fingerprint_headers(None)
        self.assertEqual(result, {})

    def test_valid_fingerprint_returns_user_agent_header(self):
        fp = {"userAgent": "TestAgent/1.0"}
        result = build_fingerprint_headers(fp)
        self.assertEqual(result, {"User-Agent": "TestAgent/1.0"})

    def test_missing_user_agent_returns_empty_dict(self):
        fp = {"other": "value"}
        result = build_fingerprint_headers(fp)
        self.assertEqual(result, {})

    def test_empty_user_agent_returns_empty_dict(self):
        fp = {"userAgent": ""}
        result = build_fingerprint_headers(fp)
        self.assertEqual(result, {})


class TestUpdateFingerprintVersion(unittest.TestCase):
    def test_adds_missing_created_at(self):
        fp = {"deviceId": "test-id", "apiClient": "test-client"}
        changed = update_fingerprint_version(fp)
        self.assertTrue(changed)
        self.assertIn("createdAt", fp)

    def test_adds_missing_api_client(self):
        fp = {"deviceId": "test-id", "createdAt": 1234567890.0}
        changed = update_fingerprint_version(fp)
        self.assertTrue(changed)
        self.assertIn("apiClient", fp)

    def test_adds_both_missing_fields(self):
        fp = {"deviceId": "test-id"}
        changed = update_fingerprint_version(fp)
        self.assertTrue(changed)
        self.assertIn("createdAt", fp)
        self.assertIn("apiClient", fp)

    def test_complete_fingerprint_no_change(self):
        fp = {
            "deviceId": "test-id",
            "apiClient": "test-client",
            "createdAt": 1234567890.0,
        }
        changed = update_fingerprint_version(fp)
        self.assertFalse(changed)
