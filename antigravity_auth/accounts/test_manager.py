import json
import tempfile
import time
import os
import unittest
from unittest import mock
from pathlib import Path

from antigravity_auth.accounts.manager import AccountManager


# ---------------------------------------------------------------------------
# TestAccountManagerEmpty
# ---------------------------------------------------------------------------

class TestAccountManagerEmpty(unittest.TestCase):
    """Core scenario tests for an empty AccountManager (not loaded from disk)."""

    def setUp(self) -> None:
        self.manager = AccountManager()

    def test_empty_count(self) -> None:
        """Empty manager reports zero enabled and total accounts."""
        self.assertEqual(self.manager.get_account_count(), 0)
        self.assertEqual(self.manager.get_total_account_count(), 0)

    def test_empty_family(self) -> None:
        """get_current_account_for_family returns None for all families."""
        self.assertIsNone(self.manager.get_current_account_for_family("gemini"))
        self.assertIsNone(self.manager.get_current_account_for_family("claude"))

    def test_empty_get_next(self) -> None:
        """get_current_or_next_for_family returns None when no accounts exist."""
        self.assertIsNone(self.manager.get_current_or_next_for_family("gemini"))


# ---------------------------------------------------------------------------
# TestAccountManagerWithAccounts
# ---------------------------------------------------------------------------

class TestAccountManagerWithAccounts(unittest.TestCase):
    """Core scenario tests for AccountManager loaded from disk with accounts."""

    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp()
        self.accounts_path = Path(self.tmpdir) / "antigravity-accounts.json"

    def tearDown(self) -> None:
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _write_accounts(self, data: dict) -> None:
        with open(self.accounts_path, "w") as f:
            json.dump(data, f)

    def _make_manager(self, accounts_data: dict) -> AccountManager:
        self._write_accounts(accounts_data)
        with mock.patch(
            "antigravity_auth.storage.get_accounts_json_path",
            return_value=self.accounts_path,
        ):
            return AccountManager.load_from_disk()

    # ------------------------------------------------------------------
    # Tests
    # ------------------------------------------------------------------

    def test_loads_accounts(self) -> None:
        """Loading a single account yields count==1, total==1."""
        data = {
            "version": 4,
            "accounts": [
                {
                    "email": "alice@example.com",
                    "refreshToken": "refresh-alice",
                    "projectId": "proj-a",
                }
            ],
            "activeIndex": 0,
            "cursor": 0,
            "activeIndexByFamily": {"claude": 0, "gemini": 0},
        }
        manager = self._make_manager(data)
        self.assertEqual(manager.get_account_count(), 1)
        self.assertEqual(manager.get_total_account_count(), 1)

    def test_gets_current_for_family(self) -> None:
        """get_current_account_for_family returns the active account."""
        data = {
            "version": 4,
            "accounts": [
                {
                    "email": "alice@example.com",
                    "refreshToken": "refresh-alice",
                    "projectId": "proj-a",
                }
            ],
            "activeIndex": 0,
            "cursor": 0,
            "activeIndexByFamily": {"claude": 0, "gemini": 0},
        }
        manager = self._make_manager(data)
        current = manager.get_current_account_for_family("gemini")
        self.assertIsNotNone(current)
        self.assertEqual(current.email, "alice@example.com")

    def test_skips_disabled(self) -> None:
        """Disabled accounts are excluded from count and current selection."""
        data = {
            "version": 4,
            "accounts": [
                {
                    "email": "alice@example.com",
                    "refreshToken": "refresh-alice",
                    "projectId": "proj-a",
                    "enabled": False,
                }
            ],
            "activeIndex": 0,
            "cursor": 0,
            "activeIndexByFamily": {"claude": 0, "gemini": 0},
        }
        manager = self._make_manager(data)
        self.assertEqual(manager.get_account_count(), 0)
        self.assertIsNone(manager.get_current_account_for_family("gemini"))

    def test_sticky_returns_current(self) -> None:
        """Sticky strategy returns the current account when not rate-limited."""
        data = {
            "version": 4,
            "accounts": [
                {
                    "email": "alice@example.com",
                    "refreshToken": "refresh-alice",
                    "projectId": "proj-a",
                }
            ],
            "activeIndex": 0,
            "cursor": 0,
            "activeIndexByFamily": {"claude": 0, "gemini": 0},
        }
        manager = self._make_manager(data)
        result = manager.get_current_or_next_for_family("gemini", strategy="sticky")
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.email, "alice@example.com")

    def test_skips_rate_limited(self) -> None:
        """Sticky selection skips a rate-limited account and falls through to the next."""
        now_ms = time.time() * 1000
        data = {
            "version": 4,
            "accounts": [
                {
                    "email": "alice@example.com",
                    "refreshToken": "refresh-alice",
                    "projectId": "proj-a",
                    "rateLimitResetTimes": {
                        "gemini-antigravity": now_ms + 60000,
                        "gemini-cli": now_ms + 60000,
                    },
                },
                {
                    "email": "bob@example.com",
                    "refreshToken": "refresh-bob",
                    "projectId": "proj-b",
                },
            ],
            "activeIndex": 0,
            "cursor": 0,
            "activeIndexByFamily": {"claude": 0, "gemini": 0},
        }
        manager = self._make_manager(data)
        result = manager.get_current_or_next_for_family("gemini", strategy="sticky")
        self.assertIsNotNone(result, "Expected a non-rate-limited account to be selected")
        assert result is not None
        self.assertEqual(result.email, "bob@example.com")

    def test_sticky_skips_gemini_antigravity_model_limited_current_account(self) -> None:
        """Sticky Gemini selection honors the concrete Antigravity header-style pool."""
        now_ms = time.time() * 1000
        data = {
            "version": 4,
            "accounts": [
                {
                    "email": "alice@example.com",
                    "refreshToken": "refresh-alice",
                    "projectId": "proj-a",
                    "rateLimitResetTimes": {
                        "gemini-antigravity:gemini-3.1-pro-high": now_ms + 60000,
                    },
                },
                {
                    "email": "bob@example.com",
                    "refreshToken": "refresh-bob",
                    "projectId": "proj-b",
                },
            ],
            "activeIndex": 0,
            "cursor": 0,
            "activeIndexByFamily": {"claude": 0, "gemini": 0},
        }
        manager = self._make_manager(data)
        result = manager.get_current_or_next_for_family(
            "gemini",
            model="gemini-3.1-pro-high",
            strategy="sticky",
            header_style="antigravity",
        )
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.email, "bob@example.com")

    def test_sticky_gemini_cli_can_use_account_limited_only_for_antigravity(self) -> None:
        """Gemini CLI selection remains independent from the Antigravity quota pool."""
        now_ms = time.time() * 1000
        data = {
            "version": 4,
            "accounts": [
                {
                    "email": "alice@example.com",
                    "refreshToken": "refresh-alice",
                    "projectId": "proj-a",
                    "rateLimitResetTimes": {
                        "gemini-antigravity:gemini-3.1-pro-high": now_ms + 60000,
                    },
                },
                {
                    "email": "bob@example.com",
                    "refreshToken": "refresh-bob",
                    "projectId": "proj-b",
                },
            ],
            "activeIndex": 0,
            "cursor": 0,
            "activeIndexByFamily": {"claude": 0, "gemini": 0},
        }
        manager = self._make_manager(data)
        result = manager.get_current_or_next_for_family(
            "gemini",
            model="gemini-3.1-pro-high",
            strategy="sticky",
            header_style="gemini-cli",
        )
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.email, "alice@example.com")

    def test_round_robin_skips_gemini_antigravity_model_limited_candidate(self) -> None:
        """Round-robin Gemini candidate filtering uses the concrete header-style pool."""
        now_ms = time.time() * 1000
        data = {
            "version": 4,
            "accounts": [
                {
                    "email": "alice@example.com",
                    "refreshToken": "refresh-alice",
                    "projectId": "proj-a",
                    "rateLimitResetTimes": {
                        "gemini-antigravity:gemini-3.1-pro-high": now_ms + 60000,
                    },
                },
                {
                    "email": "bob@example.com",
                    "refreshToken": "refresh-bob",
                    "projectId": "proj-b",
                },
            ],
            "activeIndex": 0,
            "cursor": 0,
            "activeIndexByFamily": {"claude": 0, "gemini": 0},
        }
        manager = self._make_manager(data)
        result = manager.get_current_or_next_for_family(
            "gemini",
            model="gemini-3.1-pro-high",
            strategy="round-robin",
            header_style="antigravity",
        )
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.email, "bob@example.com")

    def test_hybrid_skips_gemini_antigravity_model_limited_candidate(self) -> None:
        """Hybrid Gemini candidate filtering uses the concrete header-style pool."""
        now_ms = time.time() * 1000
        data = {
            "version": 4,
            "accounts": [
                {
                    "email": "alice@example.com",
                    "refreshToken": "refresh-alice",
                    "projectId": "proj-a",
                    "lastUsed": 0,
                    "rateLimitResetTimes": {
                        "gemini-antigravity:gemini-3.1-pro-high": now_ms + 60000,
                    },
                },
                {
                    "email": "bob@example.com",
                    "refreshToken": "refresh-bob",
                    "projectId": "proj-b",
                    "lastUsed": 100,
                },
            ],
            "activeIndex": 0,
            "cursor": 0,
            "activeIndexByFamily": {"claude": 0, "gemini": 0},
        }
        manager = self._make_manager(data)
        result = manager.get_current_or_next_for_family(
            "gemini",
            model="gemini-3.1-pro-high",
            strategy="hybrid",
            header_style="antigravity",
        )
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.email, "bob@example.com")

    def test_multiple_accounts(self) -> None:
        """Two accounts report count==2."""
        data = {
            "version": 4,
            "accounts": [
                {
                    "email": "alice@example.com",
                    "refreshToken": "refresh-alice",
                    "projectId": "proj-a",
                },
                {
                    "email": "bob@example.com",
                    "refreshToken": "refresh-bob",
                    "projectId": "proj-b",
                },
            ],
            "activeIndex": 0,
            "cursor": 0,
            "activeIndexByFamily": {"claude": 0, "gemini": 0},
        }
        manager = self._make_manager(data)
        self.assertEqual(manager.get_account_count(), 2)

    def test_remove_reindexes(self) -> None:
        """Removing account 0 reindexes remaining account to index 0."""
        data = {
            "version": 4,
            "accounts": [
                {
                    "email": "alice@example.com",
                    "refreshToken": "refresh-alice",
                    "projectId": "proj-a",
                },
                {
                    "email": "bob@example.com",
                    "refreshToken": "refresh-bob",
                    "projectId": "proj-b",
                },
            ],
            "activeIndex": 0,
            "cursor": 0,
            "activeIndexByFamily": {"claude": 0, "gemini": 0},
        }
        manager = self._make_manager(data)
        manager.remove_account(0)
        remaining = manager.get_accounts()
        self.assertEqual(len(remaining), 1)
        self.assertEqual(remaining[0].index, 0)
        self.assertEqual(remaining[0].email, "bob@example.com")

    def test_remove_last_account_persists_empty_accounts(self) -> None:
        data = {
            "version": 4,
            "accounts": [{
                "email": "alice@example.com",
                "refreshToken": "refresh-alice",
                "projectId": "proj-a",
            }],
            "activeIndex": 0,
            "cursor": 0,
            "activeIndexByFamily": {"claude": 0, "gemini": 0},
        }
        manager = self._make_manager(data)

        with mock.patch(
            "antigravity_auth.storage.get_accounts_json_path",
            return_value=self.accounts_path,
        ):
            self.assertTrue(manager.remove_account(0))

        with open(self.accounts_path, "r", encoding="utf-8") as f:
            stored = json.load(f)
        self.assertEqual(stored["accounts"], [])
        self.assertEqual(stored["activeIndexByFamily"], {"claude": 0, "gemini": 0})

    def test_remove_active_last_index_selects_previous_remaining_account(self) -> None:
        data = {
            "version": 4,
            "accounts": [
                {
                    "email": "alice@example.com",
                    "refreshToken": "refresh-alice",
                    "projectId": "proj-a",
                },
                {
                    "email": "middle@example.com",
                    "refreshToken": "refresh-middle",
                    "projectId": "proj-middle",
                },
                {
                    "email": "last-active@example.com",
                    "refreshToken": "refresh-last",
                    "projectId": "proj-last",
                },
            ],
            "activeIndex": 2,
            "cursor": 2,
            "activeIndexByFamily": {"claude": 2, "gemini": 2},
        }
        manager = self._make_manager(data)

        with mock.patch.object(manager, "_request_save_to_disk"):
            self.assertTrue(manager.remove_account(2))

        for family in ("claude", "gemini"):
            current = manager.get_current_account_for_family(family)
            self.assertIsNotNone(current)
            assert current is not None
            self.assertEqual(current.index, 1)
            self.assertEqual(current.email, "middle@example.com")

        with mock.patch(
            "antigravity_auth.storage.get_accounts_json_path",
            return_value=self.accounts_path,
        ):
            self.assertTrue(manager.save_to_disk())

        with open(self.accounts_path, "r", encoding="utf-8") as f:
            stored = json.load(f)
        self.assertEqual(stored["activeIndex"], 1)
        self.assertEqual(stored["activeIndexByFamily"], {"claude": 1, "gemini": 1})

    def test_set_enabled(self) -> None:
        """Toggling an account off reduces enabled count to zero."""
        data = {
            "version": 4,
            "accounts": [
                {
                    "email": "alice@example.com",
                    "refreshToken": "refresh-alice",
                    "projectId": "proj-a",
                }
            ],
            "activeIndex": 0,
            "cursor": 0,
            "activeIndexByFamily": {"claude": 0, "gemini": 0},
        }
        manager = self._make_manager(data)
        manager.set_account_enabled(0, False)
        self.assertEqual(manager.get_account_count(), 0)

    def test_accounts_snapshot(self) -> None:
        """get_accounts_snapshot returns list with email field."""
        data = {
            "version": 4,
            "accounts": [
                {
                    "email": "alice@example.com",
                    "refreshToken": "refresh-alice",
                    "projectId": "proj-a",
                }
            ],
            "activeIndex": 0,
            "cursor": 0,
            "activeIndexByFamily": {"claude": 0, "gemini": 0},
        }
        manager = self._make_manager(data)
        snapshot = manager.get_accounts_snapshot()
        self.assertEqual(len(snapshot), 1)
        self.assertEqual(snapshot[0]["email"], "alice@example.com")

    def test_skips_cooling_down(self) -> None:
        """Sticky selection skips a cooling-down account and returns the next."""
        now_ms = time.time() * 1000
        data = {
            "version": 4,
            "accounts": [
                {
                    "email": "alice@example.com",
                    "refreshToken": "refresh-alice",
                    "projectId": "proj-a",
                    "coolingDownUntil": now_ms + 86400000,
                    "cooldownReason": "auth-failure",
                },
                {
                    "email": "bob@example.com",
                    "refreshToken": "refresh-bob",
                    "projectId": "proj-b",
                },
            ],
            "activeIndex": 0,
            "cursor": 0,
            "activeIndexByFamily": {"claude": 0, "gemini": 0},
        }
        manager = self._make_manager(data)
        result = manager.get_current_or_next_for_family("gemini", strategy="sticky")
        self.assertIsNotNone(result, "Expected a non-cooling-down account to be selected")
        assert result is not None
        self.assertEqual(result.email, "bob@example.com")
