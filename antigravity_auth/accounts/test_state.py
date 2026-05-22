import unittest

from antigravity_auth.accounts.state import (
    ManagedAccount,
    RateLimitState,
    RefreshParts,
)


class TestRateLimitState(unittest.TestCase):
    def setUp(self):
        self.state = RateLimitState()

    def test_empty(self):
        self.assertIsNone(self.state.get("claude"))
        self.assertIsNone(self.state.get("gemini-antigravity"))

    def test_set_get_builtins(self):
        self.state.set("claude", 1000)
        self.assertEqual(self.state.get("claude"), 1000)

        self.state.set("gemini-antigravity", 2000)
        self.assertEqual(self.state.get("gemini-antigravity"), 2000)

        self.state.set("gemini-cli", 3000)
        self.assertEqual(self.state.get("gemini-cli"), 3000)

    def test_set_get_extras(self):
        self.state.set("custom-key", 4000)
        self.assertEqual(self.state.get("custom-key"), 4000)

    def test_delete(self):
        self.state.set("claude", 1000)
        self.state.delete("claude")
        self.assertIsNone(self.state.get("claude"))

    def test_delete_extras(self):
        self.state.set("custom-key", 4000)
        self.state.delete("custom-key")
        self.assertIsNone(self.state.get("custom-key"))

    def test_keys(self):
        self.state.set("claude", 1000)
        self.state.set("gemini-antigravity", 2000)
        self.state.set("gemini-cli", 3000)

        keys = self.state.keys()
        self.assertIn("claude", keys)
        self.assertIn("gemini-antigravity", keys)
        self.assertIn("gemini-cli", keys)
        self.assertNotIn("unset-key", keys)

    def test_to_dict(self):
        self.state.set("claude", 1000)
        self.state.set("gemini-antigravity", 2000)
        self.state.set("custom", 3000)

        d = self.state.to_dict()
        self.assertEqual(d, {"claude": 1000, "gemini-antigravity": 2000, "custom": 3000})

    def test_from_dict(self):
        state = RateLimitState.from_dict({"claude": 1000, "custom": 2000})
        self.assertEqual(state.get("claude"), 1000)
        self.assertEqual(state.get("custom"), 2000)
        self.assertIsNone(state.get("gemini-antigravity"))

    def test_from_dict_none(self):
        state = RateLimitState.from_dict(None)
        self.assertIsNone(state.get("claude"))
        self.assertIsNone(state.get("gemini-antigravity"))
        self.assertIsNone(state.get("gemini-cli"))
        self.assertEqual(state.to_dict(), {})


class TestManagedAccount(unittest.TestCase):
    def test_defaults(self):
        account = ManagedAccount(
            index=0,
            refresh_parts=RefreshParts(refresh_token="test"),
        )
        self.assertEqual(account.index, 0)
        self.assertIsNone(account.email)
        self.assertTrue(account.enabled)
        self.assertIsInstance(account.rate_limit_reset_times, RateLimitState)

    def test_disabled(self):
        account = ManagedAccount(
            index=1,
            refresh_parts=RefreshParts(refresh_token="test"),
            enabled=False,
        )
        self.assertFalse(account.enabled)

    def test_fingerprint(self):
        account = ManagedAccount(
            index=0,
            refresh_parts=RefreshParts(refresh_token="test"),
            fingerprint={"deviceId": "abc"},
        )
        self.assertEqual(account.fingerprint, {"deviceId": "abc"})
