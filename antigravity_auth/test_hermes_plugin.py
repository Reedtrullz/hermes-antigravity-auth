from contextlib import contextmanager
import os
import sys
import tempfile
import types
from types import SimpleNamespace
import unittest
from unittest.mock import patch


class FakeCtx:
  def __init__(self):
    self.commands = []
    self.hooks = []

  def register_cli_command(self, **kwargs):
    self.commands.append(kwargs)

  def register_hook(self, name, callback):
    self.hooks.append((name, callback))


@contextmanager
def isolated_register_environment(tmpdir, interceptor_return=True, interceptor_side_effect=None):
  config = SimpleNamespace(debug=False, debug_tui=False, log_dir=os.path.join(tmpdir, "logs"))
  with patch.dict(os.environ, {"HERMES_HOME": tmpdir}), \
      patch("antigravity_auth.hermes_plugin.get_config", return_value=config), \
      patch(
        "antigravity_auth.interceptor.install",
        return_value=interceptor_return,
        side_effect=interceptor_side_effect,
      ), \
      patch("antigravity_auth.interceptor.is_installed", return_value=False), \
      patch("antigravity_auth.accounts.shared.get_or_create_global_manager"), \
      patch("antigravity_auth.tools.register_tools"), \
      patch("antigravity_auth.token_watchdog.start_watchdog"), \
      patch("antigravity_auth.version.start_version_check"):
    yield config


class TestHermesPluginRegister(unittest.TestCase):
  def test_register_initializes_debug_logging(self):
    from antigravity_auth import hermes_plugin

    with tempfile.TemporaryDirectory() as tmpdir:
      with isolated_register_environment(tmpdir), \
          patch("antigravity_auth.hermes_plugin.initialize_debug") as init_debug:
        hermes_plugin.register(FakeCtx())

    init_debug.assert_called_once()

  def test_register_logs_interceptor_install_failure(self):
    from antigravity_auth import hermes_plugin

    with tempfile.TemporaryDirectory() as tmpdir:
      with isolated_register_environment(tmpdir, interceptor_side_effect=RuntimeError("boom")), \
          patch("antigravity_auth.hermes_plugin.initialize_debug"):
        with self.assertLogs("antigravity_auth.hermes_plugin", level="WARNING") as logs:
          hermes_plugin.register(FakeCtx())

    self.assertTrue(any("interceptor" in line.lower() and "boom" in line for line in logs.output))

  def test_register_warns_when_interceptor_install_returns_false(self):
    from antigravity_auth import hermes_plugin

    with tempfile.TemporaryDirectory() as tmpdir:
      with isolated_register_environment(tmpdir, interceptor_return=False), \
          patch("antigravity_auth.hermes_plugin.initialize_debug"):
        with self.assertLogs("antigravity_auth.hermes_plugin", level="WARNING") as logs:
          hermes_plugin.register(FakeCtx())

    self.assertTrue(any(
      "interceptor" in line.lower() and "without http interception" in line.lower()
      for line in logs.output
    ))

  def test_provider_plugin_installs_interceptor_best_effort(self):
    import importlib
    import sys
    import types
    from unittest.mock import Mock, patch

    # Save original so we can restore after
    import antigravity_auth.interceptor as interceptor_mod
    import antigravity_auth.hermes_provider_plugin as provider_mod
    orig_install = interceptor_mod.install
    orig_is_installed = interceptor_mod.is_installed
    orig_patched = interceptor_mod._PATCHED
    orig_init = interceptor_mod._ORIGINAL_INIT
    orig_wrap = interceptor_mod._ORIGINAL_WRAP_CODE_ASSIST

    try:
      # Reset interceptor state
      interceptor_mod._PATCHED = False
      interceptor_mod._ORIGINAL_INIT = None
      interceptor_mod._ORIGINAL_WRAP_CODE_ASSIST = None

      # Test 1: install succeeds
      with patch.object(interceptor_mod, "install", return_value=True):
        # Force re-import by clearing from sys.modules
        provider_key = "antigravity_auth.hermes_provider_plugin"
        if provider_key in sys.modules:
          del sys.modules[provider_key]

        import antigravity_auth.hermes_provider_plugin as reloaded
        self.assertTrue(reloaded._interceptor_installed)

    finally:
      interceptor_mod.install = orig_install
      interceptor_mod.is_installed = orig_is_installed
      interceptor_mod._PATCHED = orig_patched
      interceptor_mod._ORIGINAL_INIT = orig_init
      interceptor_mod._ORIGINAL_WRAP_CODE_ASSIST = orig_wrap

  def test_provider_plugin_handles_interceptor_import_failure(self):
    import sys
    from unittest.mock import patch

    with patch.dict(sys.modules, {"antigravity_auth.interceptor": None}):
      # Force re-import
      provider_key = "antigravity_auth.hermes_provider_plugin"
      if provider_key in sys.modules:
        del sys.modules[provider_key]

      # Should not raise
      import antigravity_auth.hermes_provider_plugin as reloaded
      self.assertFalse(reloaded._interceptor_installed)

  def test_provider_plugin_sets_provider_registry_labels(self):
    from antigravity_auth.hermes_provider_plugin import antigravity
    self.assertEqual(antigravity.display_name, "Google Antigravity")
    self.assertEqual(antigravity.name, "google-gemini-cli")
    self.assertIn("ag", antigravity.aliases)

  def test_antigravity_models_include_claude(self):
    from antigravity_auth.hermes_provider_plugin import ANTIGRAVITY_MODELS
    claude_models = [m for m in ANTIGRAVITY_MODELS if "claude" in m.lower()]
    self.assertGreater(len(claude_models), 0)
    self.assertIn("claude-opus-4-6-thinking", claude_models)

  def test_register_registers_cli_command(self):
    from antigravity_auth import hermes_plugin

    ctx = FakeCtx()
    with tempfile.TemporaryDirectory() as tmpdir:
      with isolated_register_environment(tmpdir), \
          patch("antigravity_auth.hermes_plugin.initialize_debug"):
        hermes_plugin.register(ctx)

    commands = [c for c in ctx.commands if c.get("name") == "antigravity"]
    self.assertEqual(len(commands), 1, f"Expected 1 'antigravity' CLI command, got {ctx.commands}")
    cmd = commands[0]
    self.assertEqual(cmd["help"], "Google Antigravity utilities")
    self.assertTrue(callable(cmd["setup_fn"]))
    self.assertTrue(callable(cmd["handler_fn"]))

  def test_register_registers_recovery_hook(self):
    from antigravity_auth import hermes_plugin

    ctx = FakeCtx()
    with tempfile.TemporaryDirectory() as tmpdir:
      with isolated_register_environment(tmpdir), \
          patch("antigravity_auth.hermes_plugin.initialize_debug"):
        hermes_plugin.register(ctx)

    self.assertEqual(len(ctx.hooks), 1)
    self.assertEqual(ctx.hooks[0][0], "pre_api_request")
    self.assertTrue(callable(ctx.hooks[0][1]))

  def test_interceptor_status_shows_installed_when_patched(self):
    import io
    import sys
    from unittest.mock import patch

    from antigravity_auth.cli import print_interceptor_status

    with patch("antigravity_auth.interceptor.is_installed", return_value=True), \
         patch("builtins.print") as mock_print:
      print_interceptor_status()

    output = " ".join(str(call.args[0]) for call in mock_print.call_args_list if call.args)
    self.assertIn("INSTALLED", output)
    self.assertNotIn("NOT INSTALLED", output)

  def test_interceptor_status_shows_not_installed_when_not_patched(self):
    import io
    import sys
    from unittest.mock import patch

    from antigravity_auth.cli import print_interceptor_status

    with patch("antigravity_auth.interceptor.is_installed", return_value=False), \
         patch("builtins.print") as mock_print:
      print_interceptor_status()

    output = " ".join(str(call.args[0]) for call in mock_print.call_args_list if call.args)
    self.assertIn("NOT INSTALLED", output)

  def test_interceptor_status_shows_accounts(self):
    import json
    import tempfile
    from unittest.mock import patch

    from antigravity_auth.cli import print_interceptor_status

    with tempfile.TemporaryDirectory() as tmpdir:
      accounts_path = os.path.join(tmpdir, "antigravity-accounts.json")
      with open(accounts_path, "w") as f:
        json.dump({
          "version": 4,
          "accounts": [{"email": "test@example.com", "refreshToken": "rt", "accessToken": "at"}],
          "activeIndex": 0,
          "cursor": 0,
        }, f)

      with patch.dict(os.environ, {"HERMES_HOME": tmpdir}), \
           patch("antigravity_auth.interceptor.is_installed", return_value=True), \
           patch("builtins.print") as mock_print:
        print_interceptor_status()

      output = " ".join(str(call.args[0]) for call in mock_print.call_args_list if call.args)
      self.assertIn("test@example.com", output)
      self.assertIn("ACTIVE", output)

  def test_interceptor_status_shows_claude_models(self):
    from unittest.mock import patch

    from antigravity_auth.cli import print_interceptor_status

    with patch("antigravity_auth.interceptor.is_installed", return_value=True), \
         patch("builtins.print") as mock_print:
      print_interceptor_status()

    output = " ".join(str(call.args[0]) for call in mock_print.call_args_list if call.args)
    self.assertIn("claude-opus-4-6-thinking", output)

  def test_interceptor_status_warns_when_not_installed_and_claude_models_present(self):
    from unittest.mock import patch

    from antigravity_auth.cli import print_interceptor_status

    with patch("antigravity_auth.interceptor.is_installed", return_value=False), \
         patch("builtins.print") as mock_print:
      print_interceptor_status()

    output = " ".join(str(call.args[0]) for call in mock_print.call_args_list if call.args)
    self.assertIn("Claude models require", output)
