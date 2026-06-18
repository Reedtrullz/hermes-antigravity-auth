import unittest

from antigravity_auth.accounts.quota_display import (
    QuotaDisplayInfo,
    get_quota_status_line,
    format_quota_progress_bar,
    format_single_account_quota,
    format_account_list,
    format_quota_display,
)


class TestGetQuotaStatusLine(unittest.TestCase):
    def test_none_returns_na(self):
        self.assertEqual(get_quota_status_line(None), "N/A")

    def test_exhausted(self):
        line = get_quota_status_line({"remainingFraction": 0, "used": 100, "limit": 100})
        self.assertIn("EXHAUSTED", line)

    def test_low(self):
        line = get_quota_status_line({"remainingFraction": 0.1, "used": 90, "limit": 100})
        self.assertIn("LOW", line)
        self.assertIn("10%", line)

    def test_ok(self):
        line = get_quota_status_line({"remainingFraction": 0.5, "used": 50, "limit": 100})
        self.assertIn("OK", line)
        self.assertIn("50%", line)

    def test_used_limit_format(self):
        line = get_quota_status_line({"used": 30, "limit": 100})
        self.assertEqual(line, "30/100")

    def test_empty_dict_returns_na(self):
        self.assertEqual(get_quota_status_line({}), "N/A")

    def test_malformed_remaining_fraction_is_clamped(self):
        line = get_quota_status_line({"remainingFraction": "unknown"})
        self.assertIn("EXHAUSTED", line)
        self.assertNotIn("unknown", line)


class TestFormatQuotaProgressBar(unittest.TestCase):
    def test_full(self):
        bar = format_quota_progress_bar(1.0)
        self.assertIn("100%", bar)

    def test_half(self):
        bar = format_quota_progress_bar(0.5)
        self.assertIn("50%", bar)

    def test_empty(self):
        bar = format_quota_progress_bar(0.0)
        self.assertIn("0%", bar)

    def test_low_uses_red(self):
        bar = format_quota_progress_bar(0.1)
        self.assertIn("10%", bar)

    def test_mid_uses_yellow(self):
        bar = format_quota_progress_bar(0.3)
        self.assertIn("30%", bar)

    def test_clamps_negative(self):
        bar = format_quota_progress_bar(-0.5)
        self.assertIn("0%", bar)
        self.assertNotIn("-50%", bar)

    def test_clamps_overflow(self):
        bar = format_quota_progress_bar(1.5)
        self.assertIn("100%", bar)
        self.assertNotIn("150%", bar)

    def test_custom_width(self):
        bar = format_quota_progress_bar(0.5, width=10)
        self.assertIn("50%", bar)


class TestFormatSingleAccountQuota(unittest.TestCase):
    def test_with_email(self):
        info = QuotaDisplayInfo(
            email="user@example.com",
            index=0,
            quota_groups={
                "antigravity": {"remainingFraction": 0.8, "used": 20, "limit": 100},
            },
            is_enabled=True,
        )
        output = format_single_account_quota(info)
        self.assertIn("user@example.com", output)
        self.assertIn("Antigravity", output)

    def test_without_email(self):
        info = QuotaDisplayInfo(
            email=None,
            index=1,
            quota_groups={},
            is_enabled=True,
        )
        output = format_single_account_quota(info)
        self.assertIn("Account 2", output)

    def test_disabled_account(self):
        info = QuotaDisplayInfo(
            email="disabled@example.com",
            index=0,
            quota_groups={},
            is_enabled=False,
        )
        output = format_single_account_quota(info)
        self.assertIn("disabled@example.com", output)

    def test_with_status_text(self):
        info = QuotaDisplayInfo(
            email="user@example.com",
            index=0,
            quota_groups={},
            is_enabled=True,
            status_text="Rate limited",
        )
        output = format_single_account_quota(info)
        self.assertIn("Rate limited", output)


class TestFormatAccountList(unittest.TestCase):
    def test_empty_list(self):
        result = format_account_list([])
        self.assertEqual(result, "")

    def test_single_account(self):
        class MockAccount:
            email = "test@example.com"
            is_enabled = True

        result = format_account_list([MockAccount()])
        self.assertIn("test@example.com", result)
        self.assertIn("1.", result)

    def test_multiple_accounts(self):
        class MockAccount1:
            email = "first@example.com"

        class MockAccount2:
            email = "second@example.com"

        result = format_account_list([MockAccount1(), MockAccount2()])
        self.assertIn("first@example.com", result)
        self.assertIn("second@example.com", result)
        self.assertIn("1.", result)
        self.assertIn("2.", result)

    def test_disabled_account_uses_enabled_attr(self):
        class MockAccount:
            email = "disabled@example.com"
            enabled = False

        result = format_account_list([MockAccount()])
        self.assertIn("disabled@example.com", result)


class TestFormatQuotaDisplay(unittest.TestCase):
    def test_empty_accounts(self):
        result = format_quota_display([])
        self.assertIn("Quota Status", result)
        self.assertIn("0 account", result)

    def test_single_account(self):
        info = QuotaDisplayInfo(
            email="user@example.com",
            index=0,
            quota_groups={
                "antigravity": {"remainingFraction": 0.9, "used": 10, "limit": 100},
            },
            is_enabled=True,
        )
        result = format_quota_display([info])
        self.assertIn("user@example.com", result)
        self.assertIn("1 account", result)
        self.assertIn("1 enabled", result)
        self.assertIn("0 rate-limited", result)

    def test_rate_limited_account(self):
        info = QuotaDisplayInfo(
            email="limited@example.com",
            index=0,
            quota_groups={},
            is_enabled=False,
        )
        result = format_quota_display([info])
        self.assertIn("0 enabled", result)
        self.assertIn("1 rate-limited", result)


if __name__ == "__main__":
    unittest.main()
