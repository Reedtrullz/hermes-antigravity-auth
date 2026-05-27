from contextlib import contextmanager
import os
from types import SimpleNamespace
import tempfile
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
def isolated_register_environment(tmpdir, interceptor_side_effect=None):
  config = SimpleNamespace(debug=False, debug_tui=False, log_dir=os.path.join(tmpdir, "logs"))
  with patch.dict(os.environ, {"HERMES_HOME": tmpdir}), \
      patch("antigravity_auth.hermes_plugin.get_config", return_value=config), \
      patch(
        "antigravity_auth.interceptor.install",
        return_value=True,
        side_effect=interceptor_side_effect,
      ), \
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
      with isolated_register_environment(tmpdir, RuntimeError("boom")), \
          patch("antigravity_auth.hermes_plugin.initialize_debug"):
        with self.assertLogs("antigravity_auth.hermes_plugin", level="WARNING") as logs:
          hermes_plugin.register(FakeCtx())

    self.assertTrue(any("interceptor" in line.lower() and "boom" in line for line in logs.output))
