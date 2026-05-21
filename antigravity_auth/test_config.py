import os
import unittest

from antigravity_auth.config import (
    Config,
    SignatureCacheConfig,
    HealthScoreConfig,
    TokenBucketConfig,
    DEFAULT_CONFIG,
    load_config_from_dict,
    load_config_from_yaml,
    apply_env_overrides,
    get_config,
)


class TestConfigDefaults(unittest.TestCase):
    def setUp(self):
        import antigravity_auth.config as cfg
        cfg._config_cache = None

    def tearDown(self):
        import antigravity_auth.config as cfg
        cfg._config_cache = None

    def test_default_config_has_expected_defaults(self):
        config = DEFAULT_CONFIG
        self.assertFalse(config.quiet_mode)
        self.assertEqual(config.toast_scope, "root_only")
        self.assertFalse(config.debug)
        self.assertFalse(config.debug_tui)
        self.assertIsNone(config.log_dir)
        self.assertFalse(config.keep_thinking)
        self.assertTrue(config.session_recovery)
        self.assertTrue(config.auto_resume)
        self.assertEqual(config.resume_text, "continue")
        self.assertEqual(config.empty_response_max_attempts, 4)
        self.assertTrue(config.tool_id_recovery)
        self.assertTrue(config.claude_tool_hardening)
        self.assertFalse(config.claude_prompt_auto_caching)
        self.assertTrue(config.proactive_token_refresh)
        self.assertEqual(config.proactive_refresh_buffer_seconds, 1800)
        self.assertEqual(config.max_rate_limit_wait_seconds, 300)
        self.assertFalse(config.quota_fallback)
        self.assertFalse(config.cli_first)
        self.assertEqual(config.account_selection_strategy, "hybrid")
        self.assertFalse(config.pid_offset_enabled)
        self.assertEqual(config.scheduling_mode, "cache_first")
        self.assertEqual(config.max_cache_first_wait_seconds, 60)
        self.assertEqual(config.failure_ttl_seconds, 3600)
        self.assertEqual(config.default_retry_after_seconds, 60)
        self.assertEqual(config.max_backoff_seconds, 60)
        self.assertEqual(config.request_jitter_max_ms, 0)
        self.assertEqual(config.soft_quota_threshold_percent, 90)
        self.assertEqual(config.quota_refresh_interval_minutes, 15)
        self.assertEqual(config.soft_quota_cache_ttl_minutes, "auto")


class TestSignatureCacheConfig(unittest.TestCase):
    def test_defaults(self):
        sc = SignatureCacheConfig()
        self.assertTrue(sc.enabled)
        self.assertEqual(sc.memory_ttl_seconds, 3600)
        self.assertEqual(sc.disk_ttl_seconds, 172800)
        self.assertEqual(sc.write_interval_seconds, 60)

    def test_custom_values(self):
        sc = SignatureCacheConfig(enabled=False, memory_ttl_seconds=1800)
        self.assertFalse(sc.enabled)
        self.assertEqual(sc.memory_ttl_seconds, 1800)
        self.assertEqual(sc.disk_ttl_seconds, 172800)


class TestHealthScoreConfig(unittest.TestCase):
    def test_defaults(self):
        hs = HealthScoreConfig()
        self.assertEqual(hs.initial, 70)
        self.assertEqual(hs.success_reward, 1)
        self.assertEqual(hs.rate_limit_penalty, -10)
        self.assertEqual(hs.failure_penalty, -20)
        self.assertEqual(hs.recovery_rate_per_hour, 2.0)
        self.assertEqual(hs.min_usable, 50)
        self.assertEqual(hs.max_score, 100)

    def test_custom_values(self):
        hs = HealthScoreConfig(initial=50, max_score=80)
        self.assertEqual(hs.initial, 50)
        self.assertEqual(hs.max_score, 80)


class TestTokenBucketConfig(unittest.TestCase):
    def test_defaults(self):
        tb = TokenBucketConfig()
        self.assertEqual(tb.max_tokens, 50)
        self.assertEqual(tb.regeneration_rate_per_minute, 6.0)
        self.assertEqual(tb.initial_tokens, 50)


class TestLoadConfigFromDict(unittest.TestCase):
    def test_empty_dict_returns_defaults(self):
        config = load_config_from_dict({})
        self.assertIsInstance(config, Config)
        self.assertFalse(config.debug)
        self.assertEqual(config.scheduling_mode, "cache_first")

    def test_overrides_scalar_fields(self):
        config = load_config_from_dict({
            "quiet_mode": True,
            "debug": True,
            "debug_tui": True,
            "keep_thinking": True,
            "session_recovery": False,
            "cli_first": True,
            "scheduling_mode": "balance",
            "account_selection_strategy": "round-robin",
            "soft_quota_threshold_percent": 80,
            "quota_refresh_interval_minutes": 10,
        })
        self.assertTrue(config.quiet_mode)
        self.assertTrue(config.debug)
        self.assertTrue(config.debug_tui)
        self.assertTrue(config.keep_thinking)
        self.assertFalse(config.session_recovery)
        self.assertTrue(config.cli_first)
        self.assertEqual(config.scheduling_mode, "balance")
        self.assertEqual(config.account_selection_strategy, "round-robin")
        self.assertEqual(config.soft_quota_threshold_percent, 80)
        self.assertEqual(config.quota_refresh_interval_minutes, 10)

    def test_nested_signature_cache(self):
        config = load_config_from_dict({
            "signature_cache": {"enabled": False, "memory_ttl_seconds": 500},
        })
        self.assertFalse(config.signature_cache.enabled)
        self.assertEqual(config.signature_cache.memory_ttl_seconds, 500)
        self.assertEqual(config.signature_cache.disk_ttl_seconds, 172800)

    def test_nested_health_score(self):
        config = load_config_from_dict({
            "health_score": {"initial": 60, "failure_penalty": -30},
        })
        self.assertEqual(config.health_score.initial, 60)
        self.assertEqual(config.health_score.failure_penalty, -30)
        self.assertEqual(config.health_score.success_reward, 1)

    def test_nested_token_bucket(self):
        config = load_config_from_dict({
            "token_bucket": {"max_tokens": 100, "regeneration_rate_per_minute": 10.0},
        })
        self.assertEqual(config.token_bucket.max_tokens, 100)
        self.assertEqual(config.token_bucket.regeneration_rate_per_minute, 10.0)

    def test_soft_quota_cache_ttl_as_int(self):
        config = load_config_from_dict({"soft_quota_cache_ttl_minutes": 30})
        self.assertEqual(config.soft_quota_cache_ttl_minutes, 30)

    def test_soft_quota_cache_ttl_as_string(self):
        config = load_config_from_dict({"soft_quota_cache_ttl_minutes": "auto"})
        self.assertEqual(config.soft_quota_cache_ttl_minutes, "auto")

    def test_loads_hermes_plugin_entry_config(self):
        import tempfile
        from pathlib import Path

        try:
            import yaml  # noqa: F401
        except ImportError:
            self.skipTest("pyyaml not installed")

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "config.yaml"
            path.write_text(
                """
plugins:
  entries:
    antigravity:
      debug: true
      cli_first: true
      quiet_mode: true
      health_score:
        initial: 62
""",
                encoding="utf-8",
            )
            config = load_config_from_yaml(path)

        self.assertIsNotNone(config)
        assert config is not None
        self.assertTrue(config.debug)
        self.assertTrue(config.cli_first)
        self.assertTrue(config.quiet_mode)
        self.assertEqual(config.health_score.initial, 62)

    def test_nested_hermes_config_overrides_root_compat_config(self):
        import tempfile
        from pathlib import Path

        try:
            import yaml  # noqa: F401
        except ImportError:
            self.skipTest("pyyaml not installed")

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "config.yaml"
            path.write_text(
                """
debug: false
plugins:
  entries:
    antigravity:
      debug: true
""",
                encoding="utf-8",
            )
            config = load_config_from_yaml(path)

        self.assertIsNotNone(config)
        assert config is not None
        self.assertTrue(config.debug)


class TestApplyEnvOverrides(unittest.TestCase):
    def setUp(self):
        self.env_backup = {}
        for key in (
            "HERMES_ANTIGRAVITY_DEBUG",
            "HERMES_ANTIGRAVITY_QUIET",
            "HERMES_ANTIGRAVITY_KEEP_THINKING",
            "HERMES_ANTIGRAVITY_CLI_FIRST",
            "HERMES_ANTIGRAVITY_ACCOUNT_SELECTION_STRATEGY",
            "HERMES_ANTIGRAVITY_SCHEDULING_MODE",
            "HERMES_ANTIGRAVITY_DEBUG_TUI",
            "HERMES_ANTIGRAVITY_LOG_DIR",
        ):
            self.env_backup[key] = os.environ.get(key)

    def tearDown(self):
        for key, value in self.env_backup.items():
            if value is not None:
                os.environ[key] = value
            else:
                os.environ.pop(key, None)

    def test_env_debug_enabled(self):
        os.environ["HERMES_ANTIGRAVITY_DEBUG"] = "1"
        config = apply_env_overrides(Config())
        self.assertTrue(config.debug)

    def test_env_debug_disabled(self):
        os.environ["HERMES_ANTIGRAVITY_DEBUG"] = "0"
        config = apply_env_overrides(Config())
        self.assertFalse(config.debug)

    def test_env_quiet_mode(self):
        os.environ["HERMES_ANTIGRAVITY_QUIET"] = "true"
        config = apply_env_overrides(Config())
        self.assertTrue(config.quiet_mode)

    def test_env_keep_thinking(self):
        os.environ["HERMES_ANTIGRAVITY_KEEP_THINKING"] = "yes"
        config = apply_env_overrides(Config())
        self.assertTrue(config.keep_thinking)

    def test_env_cli_first(self):
        os.environ["HERMES_ANTIGRAVITY_CLI_FIRST"] = "on"
        config = apply_env_overrides(Config())
        self.assertTrue(config.cli_first)

    def test_env_account_selection_strategy(self):
        os.environ["HERMES_ANTIGRAVITY_ACCOUNT_SELECTION_STRATEGY"] = "sticky"
        config = apply_env_overrides(Config())
        self.assertEqual(config.account_selection_strategy, "sticky")

    def test_env_scheduling_mode(self):
        os.environ["HERMES_ANTIGRAVITY_SCHEDULING_MODE"] = "performance_first"
        config = apply_env_overrides(Config())
        self.assertEqual(config.scheduling_mode, "performance_first")

    def test_env_debug_tui(self):
        os.environ["HERMES_ANTIGRAVITY_DEBUG_TUI"] = "1"
        config = apply_env_overrides(Config())
        self.assertTrue(config.debug_tui)

    def test_env_log_dir(self):
        os.environ["HERMES_ANTIGRAVITY_LOG_DIR"] = "/tmp/custom-logs"
        config = apply_env_overrides(Config())
        self.assertEqual(config.log_dir, "/tmp/custom-logs")

    def test_multiple_env_overrides(self):
        os.environ["HERMES_ANTIGRAVITY_DEBUG"] = "1"
        os.environ["HERMES_ANTIGRAVITY_QUIET"] = "1"
        os.environ["HERMES_ANTIGRAVITY_SCHEDULING_MODE"] = "balance"
        config = apply_env_overrides(Config())
        self.assertTrue(config.debug)
        self.assertTrue(config.quiet_mode)
        self.assertEqual(config.scheduling_mode, "balance")


if __name__ == "__main__":
    unittest.main()
