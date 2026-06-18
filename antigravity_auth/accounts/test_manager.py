import json
import tempfile
import time
import os
import unittest
from unittest import mock
from pathlib import Path
from typing import Any

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
        self._managers: list[AccountManager] = []

    def tearDown(self) -> None:
        for manager in getattr(self, "_managers", []):
            timer = getattr(manager, "_save_timer", None)
            if timer is not None:
                timer.cancel()
            manager._save_timer = None
            manager._save_pending = False
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
            manager = AccountManager.load_from_disk()
        self._managers.append(manager)
        return manager

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

    def test_load_and_save_preserves_rate_limit_failure_state(self) -> None:
        data = {
            "version": 4,
            "accounts": [{
                "email": "quota@example.com",
                "refreshToken": "refresh-quota",
                "projectId": "proj-quota",
                "consecutiveFailures": 3,
                "lastFailureTime": 123456,
                "rateLimitResetTimes": {"gemini-antigravity": 999999},
            }],
            "activeIndex": 0,
            "cursor": 0,
            "activeIndexByFamily": {"claude": 0, "gemini": 0},
        }
        manager = self._make_manager(data)
        account = manager.get_account_by_index(0)
        self.assertIsNotNone(account)
        assert account is not None
        self.assertEqual(account.consecutive_failures, 3)
        self.assertEqual(account.last_failure_time, 123456)

        account.consecutive_failures = 4
        account.last_failure_time = 234567
        with mock.patch(
            "antigravity_auth.storage.get_accounts_json_path",
            return_value=self.accounts_path,
        ):
            self.assertTrue(manager.save_to_disk())

        with open(self.accounts_path, "r", encoding="utf-8") as f:
            stored = json.load(f)["accounts"][0]
        self.assertEqual(stored["consecutiveFailures"], 4)
        self.assertEqual(stored["lastFailureTime"], 234567)
        self.assertEqual(stored["rateLimitResetTimes"], {"gemini-antigravity": 999999})

    def test_request_success_clears_failure_counter_and_timestamp(self) -> None:
        data = {
            "version": 4,
            "accounts": [{
                "email": "recover@example.com",
                "refreshToken": "refresh-recover",
                "projectId": "proj-recover",
                "consecutiveFailures": 2,
                "lastFailureTime": 123456,
            }],
            "activeIndex": 0,
            "cursor": 0,
            "activeIndexByFamily": {"claude": 0, "gemini": 0},
        }
        manager = self._make_manager(data)
        account = manager.get_account_by_index(0)
        self.assertIsNotNone(account)
        assert account is not None

        manager.mark_request_success(account)

        self.assertEqual(account.consecutive_failures, 0)
        self.assertIsNone(account.last_failure_time)

    def test_snapshot_redacts_refresh_and_access_tokens(self) -> None:
        data = {
            "version": 4,
            "accounts": [{
                "email": "secret@example.com",
                "refreshToken": "raw-refresh-token",
                "projectId": "proj-secret",
                "managedProjectId": "managed-secret",
                "accessToken": "raw-access-token",
                "accessTokenExpiresAt": 123456,
                "lastRefreshAt": 111,
            }],
            "activeIndex": 0,
            "cursor": 0,
            "activeIndexByFamily": {"claude": 0, "gemini": 0},
        }
        manager = self._make_manager(data)
        snapshot = manager.get_accounts_snapshot()
        rendered = repr(snapshot)
        self.assertNotIn("raw-refresh-token", rendered)
        self.assertNotIn("raw-access-token", rendered)
        self.assertEqual(snapshot[0]["refresh_parts"]["refresh_token"], "[REDACTED]")
        self.assertTrue(snapshot[0]["access_token_cached"])
        self.assertEqual(snapshot[0]["access_token_expires_at"], 123456)

    def test_save_to_disk_preserves_newer_token_cache_from_disk(self) -> None:
        data = {
            "version": 4,
            "accounts": [{
                "email": "race@example.com",
                "refreshToken": "old-refresh",
                "projectId": "proj-race",
                "accessToken": "old-access",
                "accessTokenExpiresAt": 1000,
                "lastRefreshAt": 100,
                "fingerprint": {"deviceId": "old-device", "createdAt": 1},
            }],
            "activeIndex": 0,
            "cursor": 0,
            "activeIndexByFamily": {"claude": 0, "gemini": 0},
        }
        manager = self._make_manager(data)
        account = manager.get_account_by_index(0)
        self.assertIsNotNone(account)
        assert account is not None
        account.access = "stale-access"
        account.expires = 200
        account.last_refresh_at = 100

        self._write_accounts({
            "version": 4,
            "accounts": [{
                "email": "race@example.com",
                "refreshToken": "new-refresh",
                "projectId": "proj-race",
                "accessToken": "new-access",
                "accessTokenExpiresAt": 9999,
                "lastRefreshAt": 999,
                "fingerprint": {"deviceId": "new-device", "createdAt": 999},
                "rateLimitResetTimes": {"claude": 8888},
            }],
            "activeIndex": 0,
            "cursor": 0,
            "activeIndexByFamily": {"claude": 0, "gemini": 0},
        })
        with mock.patch(
            "antigravity_auth.storage.get_accounts_json_path",
            return_value=self.accounts_path,
        ):
            self.assertTrue(manager.save_to_disk())
        with open(self.accounts_path, "r", encoding="utf-8") as f:
            stored = json.load(f)["accounts"][0]
        self.assertEqual(stored["refreshToken"], "new-refresh")
        self.assertEqual(stored["accessToken"], "new-access")
        self.assertEqual(stored["lastRefreshAt"], 999)
        self.assertEqual(stored["fingerprint"], {"deviceId": "new-device", "createdAt": 999})
        self.assertEqual(stored["rateLimitResetTimes"], {"claude": 8888})

    def test_reload_from_disk_mutates_existing_manager_and_cancels_pending_save(self) -> None:
        data = {
            "version": 4,
            "accounts": [
                {"email": "bad@example.com", "refreshToken": "refresh-bad", "projectId": "proj-bad"},
                {"email": "good@example.com", "refreshToken": "refresh-good", "projectId": "proj-good"},
            ],
            "activeIndex": 0,
            "cursor": 1,
            "activeIndexByFamily": {"claude": 0, "gemini": 0},
        }
        manager = self._make_manager(data)
        manager._request_save_to_disk()
        self.assertTrue(manager._save_pending)
        self.assertIsNotNone(manager._save_timer)

        self._write_accounts({
            "version": 4,
            "accounts": [
                {"email": "good@example.com", "refreshToken": "refresh-good", "projectId": "proj-good"},
            ],
            "activeIndex": 0,
            "cursor": 0,
            "activeIndexByFamily": {"claude": 0, "gemini": 0},
        })
        with mock.patch(
            "antigravity_auth.storage.get_accounts_json_path",
            return_value=self.accounts_path,
        ):
            manager.reload_from_disk()

        self.assertFalse(manager._save_pending)
        self.assertIsNone(manager._save_timer)
        self.assertEqual(manager.get_total_account_count(), 1)
        self.assertEqual(manager.get_accounts()[0].email, "good@example.com")
        current = manager.get_current_account_for_family("claude")
        self.assertIsNotNone(current)
        assert current is not None
        self.assertEqual(current.email, "good@example.com")

        with mock.patch(
            "antigravity_auth.storage.get_accounts_json_path",
            return_value=self.accounts_path,
        ):
            self.assertTrue(manager.save_to_disk())
        with open(self.accounts_path, "r", encoding="utf-8") as f:
            stored = json.load(f)
        self.assertEqual([a["email"] for a in stored["accounts"]], ["good@example.com"])

    def test_reload_invalidates_captured_debounce_callback(self) -> None:
        """A debounce callback captured before reload cannot save stale state later."""
        data = {
            "version": 4,
            "accounts": [
                {"email": "bad@example.com", "refreshToken": "refresh-bad", "projectId": "proj-bad"},
                {"email": "good@example.com", "refreshToken": "refresh-good", "projectId": "proj-good"},
            ],
            "activeIndex": 0,
            "cursor": 1,
            "activeIndexByFamily": {"claude": 0, "gemini": 0},
        }
        manager = self._make_manager(data)
        manager._request_save_to_disk()
        timer = manager._save_timer
        self.assertIsNotNone(timer)

        self._write_accounts({
            "version": 4,
            "accounts": [
                {"email": "good@example.com", "refreshToken": "refresh-good", "projectId": "proj-good"},
            ],
            "activeIndex": 0,
            "cursor": 0,
            "activeIndexByFamily": {"claude": 0, "gemini": 0},
        })
        with mock.patch(
            "antigravity_auth.storage.get_accounts_json_path",
            return_value=self.accounts_path,
        ):
            manager.reload_from_disk()

        assert timer is not None
        with mock.patch("antigravity_auth.storage.save_accounts") as save_accounts:
            timer.function()
        save_accounts.assert_not_called()

        with open(self.accounts_path, "r", encoding="utf-8") as f:
            stored = json.load(f)
        self.assertEqual([a["email"] for a in stored["accounts"]], ["good@example.com"])

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

    def test_get_account_by_index_returns_enabled_account(self) -> None:
        data = {
            "version": 4,
            "accounts": [
                {"email": "alice@example.com", "refreshToken": "refresh-alice", "projectId": "proj-a"},
                {"email": "bob@example.com", "refreshToken": "refresh-bob", "projectId": "proj-b"},
            ],
            "activeIndex": 0,
            "cursor": 0,
            "activeIndexByFamily": {"claude": 0, "gemini": 0},
        }
        manager = self._make_manager(data)
        first_account = manager.get_account_by_index(0)
        self.assertIsNotNone(first_account)
        assert first_account is not None
        self.assertEqual(first_account.email, "alice@example.com")

        second_account = manager.get_account_by_index(1)
        self.assertIsNotNone(second_account)
        assert second_account is not None
        self.assertEqual(second_account.email, "bob@example.com")

    def test_get_account_by_index_rejects_out_of_range_and_disabled(self) -> None:
        data = {
            "version": 4,
            "accounts": [
                {
                    "email": "alice@example.com",
                    "refreshToken": "refresh-alice",
                    "projectId": "proj-a",
                    "enabled": False,
                },
            ],
            "activeIndex": 0,
            "cursor": 0,
            "activeIndexByFamily": {"claude": 0, "gemini": 0},
        }
        manager = self._make_manager(data)
        self.assertIsNone(manager.get_account_by_index(-1))
        self.assertIsNone(manager.get_account_by_index(999))
        self.assertIsNone(manager.get_account_by_index(0))

    def test_get_account_by_index_rejects_non_integer_inputs(self) -> None:
        data = {
            "version": 4,
            "accounts": [
                {"email": "alice@example.com", "refreshToken": "refresh-alice", "projectId": "proj-a"},
            ],
            "activeIndex": 0,
            "cursor": 0,
            "activeIndexByFamily": {"claude": 0, "gemini": 0},
        }
        manager = self._make_manager(data)

        invalid_indices: tuple[Any, ...] = (True, False, "0", 0.0, None)
        for index in invalid_indices:
            with self.subTest(index=index):
                self.assertIsNone(manager.get_account_by_index(index))

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

    def test_malformed_cached_quota_does_not_block_selection(self) -> None:
        """Bad quota cache shapes should not make an otherwise usable account unavailable."""
        data = {
            "version": 4,
            "accounts": [
                {
                    "email": "alice@example.com",
                    "refreshToken": "refresh-alice",
                    "projectId": "proj-a",
                    "cachedQuota": {"gemini-pro": {"remainingFraction": "unknown"}},
                    "cachedQuotaUpdatedAt": time.time() * 1000,
                }
            ],
            "activeIndex": 0,
            "cursor": 0,
            "activeIndexByFamily": {"claude": 0, "gemini": 0},
        }
        manager = self._make_manager(data)
        result = manager.get_current_or_next_for_family(
            "gemini",
            strategy="sticky",
            soft_quota_threshold_percent=90,
            soft_quota_cache_ttl_ms=60000,
        )
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
        with mock.patch(
            "antigravity_auth.storage.get_accounts_json_path",
            return_value=self.accounts_path,
        ):
            manager.remove_account(0)
            if manager._save_timer is not None:
                manager._save_timer.cancel()
                manager._save_timer = None
                manager._save_pending = False
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
        with mock.patch.object(manager, "_request_save_to_disk") as request_save:
            manager.set_account_enabled(0, False)
            request_save.assert_called_once()
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
