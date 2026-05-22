import time
import unittest

from antigravity_auth.accounts.quota import (
    classify_quota_group,
    compute_soft_quota_cache_ttl_ms,
    is_over_soft_quota_threshold,
    normalize_remaining_fraction,
    parse_reset_time,
    resolve_quota_group,
)


class TestNormalizeRemainingFraction(unittest.TestCase):
    def test_half_returns_half(self):
        self.assertEqual(normalize_remaining_fraction(0.5), 0.5)

    def test_above_one_clamps_to_one(self):
        self.assertEqual(normalize_remaining_fraction(1.5), 1.0)

    def test_negative_clamps_to_zero(self):
        self.assertEqual(normalize_remaining_fraction(-0.5), 0.0)

    def test_string_returns_zero(self):
        self.assertEqual(normalize_remaining_fraction("abc"), 0.0)

    def test_none_returns_zero(self):
        self.assertEqual(normalize_remaining_fraction(None), 0.0)

    def test_zero_returns_zero(self):
        self.assertEqual(normalize_remaining_fraction(0.0), 0.0)

    def test_one_returns_one(self):
        self.assertEqual(normalize_remaining_fraction(1.0), 1.0)

    def test_int_value(self):
        self.assertEqual(normalize_remaining_fraction(0), 0.0)


class TestClassifyQuotaGroup(unittest.TestCase):
    def test_claude_opus_returns_claude(self):
        self.assertEqual(classify_quota_group("claude-opus"), "claude")

    def test_gemini_3_flash_preview_returns_gemini_flash(self):
        self.assertEqual(classify_quota_group("gemini-3-flash-preview"), "gemini-flash")

    def test_gemini_3_pro_preview_returns_gemini_pro(self):
        self.assertEqual(classify_quota_group("gemini-3-pro-preview"), "gemini-pro")

    def test_unknown_model_returns_none(self):
        self.assertIsNone(classify_quota_group("unknown"))

    def test_claude_in_display_name(self):
        self.assertEqual(
            classify_quota_group("some-model", display_name="Claude Next"),
            "claude",
        )

    def test_gemini_3_with_space(self):
        self.assertEqual(classify_quota_group("gemini 3 flash"), "gemini-flash")

    def test_non_gemini3_returns_none(self):
        self.assertIsNone(classify_quota_group("gemini-2-pro"))

    def test_gemini_3_no_flash_returns_pro(self):
        self.assertEqual(classify_quota_group("gemini-3-ultra"), "gemini-pro")

    def test_claude_sonnet_returns_claude(self):
        self.assertEqual(classify_quota_group("claude-sonnet-4"), "claude")


class TestResolveQuotaGroup(unittest.TestCase):
    def test_claude_family_with_model_returns_claude(self):
        self.assertEqual(resolve_quota_group("claude", model="claude-sonnet-4"), "claude")

    def test_claude_family_no_model_returns_claude(self):
        self.assertEqual(resolve_quota_group("claude"), "claude")

    def test_gemini_family_pro_model_returns_gemini_pro(self):
        self.assertEqual(
            resolve_quota_group("gemini", model="gemini-3-pro-preview"),
            "gemini-pro",
        )

    def test_gemini_family_flash_model_overrides_to_gemini_flash(self):
        self.assertEqual(
            resolve_quota_group("gemini", model="gemini-3-flash-preview"),
            "gemini-flash",
        )

    def test_unknown_model_falls_back_to_gemini_pro(self):
        self.assertEqual(
            resolve_quota_group("gemini", model="unknown-model"),
            "gemini-pro",
        )

    def test_gemini_family_no_model_returns_gemini_pro(self):
        self.assertEqual(resolve_quota_group("gemini"), "gemini-pro")

    def test_unknown_family_no_model_returns_gemini_pro(self):
        self.assertEqual(resolve_quota_group("other"), "gemini-pro")


class TestComputeCacheTTL(unittest.TestCase):
    def test_auto_with_15_minutes_returns_1800000(self):
        self.assertEqual(
            compute_soft_quota_cache_ttl_ms("auto", 15),
            1800000,
        )

    def test_auto_with_3_minutes_uses_minimum_10(self):
        self.assertEqual(
            compute_soft_quota_cache_ttl_ms("auto", 3),
            600000,
        )

    def test_explicit_30_returns_1800000(self):
        self.assertEqual(
            compute_soft_quota_cache_ttl_ms(30, 100),
            1800000,
        )

    def test_explicit_5_returns_300000(self):
        self.assertEqual(
            compute_soft_quota_cache_ttl_ms(5, 100),
            300000,
        )

    def test_auto_with_zero_refresh_interval_uses_minimum(self):
        self.assertEqual(
            compute_soft_quota_cache_ttl_ms("auto", 0),
            600000,
        )

    def test_auto_with_large_refresh_interval(self):
        self.assertEqual(
            compute_soft_quota_cache_ttl_ms("auto", 60),
            7200000,
        )


class TestSoftQuotaThreshold(unittest.TestCase):
    def test_threshold_100_returns_false(self):
        self.assertFalse(
            is_over_soft_quota_threshold(
                cached_quota={},
                cached_quota_updated_at=0,
                family="gemini",
                threshold_percent=100,
                cache_ttl_ms=60000,
            )
        )

    def test_no_cache_returns_false(self):
        self.assertFalse(
            is_over_soft_quota_threshold(
                cached_quota=None,
                cached_quota_updated_at=0,
                family="gemini",
                threshold_percent=90,
                cache_ttl_ms=60000,
            )
        )

    def test_no_updated_at_returns_false(self):
        self.assertFalse(
            is_over_soft_quota_threshold(
                cached_quota={"gemini-pro": {}},
                cached_quota_updated_at=None,
                family="gemini",
                threshold_percent=90,
                cache_ttl_ms=60000,
            )
        )

    def test_stale_cache_returns_false(self):
        self.assertFalse(
            is_over_soft_quota_threshold(
                cached_quota={"gemini-pro": {"remainingFraction": 0.05}},
                cached_quota_updated_at=0,
                family="gemini",
                threshold_percent=90,
                cache_ttl_ms=1000,
            )
        )

    def test_95_percent_used_with_90_percent_threshold_returns_true(self):
        now_ms = time.time() * 1000
        self.assertTrue(
            is_over_soft_quota_threshold(
                cached_quota={"gemini-pro": {"remainingFraction": 0.05}},
                cached_quota_updated_at=now_ms,
                family="gemini",
                threshold_percent=90,
                cache_ttl_ms=60000,
            )
        )

    def test_50_percent_used_with_90_percent_threshold_returns_false(self):
        now_ms = time.time() * 1000
        self.assertFalse(
            is_over_soft_quota_threshold(
                cached_quota={"gemini-pro": {"remainingFraction": 0.5}},
                cached_quota_updated_at=now_ms,
                family="gemini",
                threshold_percent=90,
                cache_ttl_ms=60000,
            )
        )

    def test_missing_quota_group_in_cache_returns_false(self):
        now_ms = time.time() * 1000
        self.assertFalse(
            is_over_soft_quota_threshold(
                cached_quota={"claude": {"remainingFraction": 0.05}},
                cached_quota_updated_at=now_ms,
                family="gemini",
                threshold_percent=90,
                cache_ttl_ms=60000,
            )
        )

    def test_missing_remaining_fraction_returns_false(self):
        now_ms = time.time() * 1000
        self.assertFalse(
            is_over_soft_quota_threshold(
                cached_quota={"gemini-pro": {}},
                cached_quota_updated_at=now_ms,
                family="gemini",
                threshold_percent=90,
                cache_ttl_ms=60000,
            )
        )

    def test_exactly_at_threshold_returns_true(self):
        now_ms = time.time() * 1000
        # 10% remaining = 90% used = exactly at 90% threshold
        self.assertTrue(
            is_over_soft_quota_threshold(
                cached_quota={"gemini-pro": {"remainingFraction": 0.10}},
                cached_quota_updated_at=now_ms,
                family="gemini",
                threshold_percent=90,
                cache_ttl_ms=60000,
            )
        )

    def test_with_model_override_uses_flash_group(self):
        now_ms = time.time() * 1000
        self.assertTrue(
            is_over_soft_quota_threshold(
                cached_quota={"gemini-flash": {"remainingFraction": 0.05}},
                cached_quota_updated_at=now_ms,
                family="gemini",
                threshold_percent=90,
                cache_ttl_ms=60000,
                model="gemini-3-flash-preview",
            )
        )


class TestParseResetTime(unittest.TestCase):
    def test_valid_iso_returns_not_none(self):
        result = parse_reset_time("2026-05-20T12:00:00Z")
        self.assertIsNotNone(result)
        self.assertIsInstance(result, float)

    def test_valid_iso_with_milliseconds(self):
        result = parse_reset_time("2026-05-20T12:00:00.123456Z")
        self.assertIsNotNone(result)

    def test_none_returns_none(self):
        self.assertIsNone(parse_reset_time(None))

    def test_empty_string_returns_none(self):
        self.assertIsNone(parse_reset_time(""))

    def test_invalid_string_returns_zero(self):
        self.assertEqual(parse_reset_time("not-a-date"), 0.0)


if __name__ == "__main__":
    unittest.main()
