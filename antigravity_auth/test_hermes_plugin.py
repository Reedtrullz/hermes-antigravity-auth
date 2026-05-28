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

    class FakeProviderProfile:
      def __init__(self, **kwargs):
        self.__dict__.update(kwargs)

    providers = types.ModuleType("providers")
    providers.register_provider = Mock()
    providers_base = types.ModuleType("providers.base")
    providers_base.ProviderProfile = FakeProviderProfile

    with patch.dict(sys.modules, {
      "providers": providers,
      "providers.base": providers_base,
    }), patch("antigravity_auth.interceptor.install", return_value=True) as install:
      sys.modules.pop("antigravity_auth.hermes_provider_plugin", None)
      importlib.import_module("antigravity_auth.hermes_provider_plugin")

    install.assert_called_once()

  def test_provider_picker_registers_all_advertised_aliases(self):
    import importlib
    import antigravity_auth

    captured = []

    class FakeProviderProfile:
      def __init__(self, **kwargs):
        self.__dict__.update(kwargs)

    class FakeProviderEntry:
      def __init__(self, slug, label, desc):
        self.slug = slug
        self.label = label
        self.desc = desc

    fake_providers = types.ModuleType("providers")
    fake_providers.register_provider = lambda profile: captured.append(profile)
    fake_base = types.ModuleType("providers.base")
    fake_base.ProviderProfile = FakeProviderProfile
    fake_hermes_cli = types.ModuleType("hermes_cli")
    fake_hermes_cli.__path__ = []
    fake_models = types.ModuleType("hermes_cli.models")
    fake_models._PROVIDER_MODELS = {}
    fake_models._PROVIDER_LABELS = {}
    fake_models._PROVIDER_ALIASES = {}
    fake_models.ProviderEntry = FakeProviderEntry
    fake_models.CANONICAL_PROVIDERS = [FakeProviderEntry("google-gemini-cli", "Old", "Old")]
    fake_cli_providers = types.ModuleType("hermes_cli.providers")
    fake_cli_providers._LABEL_OVERRIDES = {}
    fake_cli_providers.ALIASES = {}
    fake_hermes_cli.models = fake_models
    fake_hermes_cli.providers = fake_cli_providers

    had_attr = hasattr(antigravity_auth, "hermes_provider_plugin")
    old_attr = getattr(antigravity_auth, "hermes_provider_plugin", None)
    old_module = sys.modules.pop("antigravity_auth.hermes_provider_plugin", None)
    try:
      with patch.dict(sys.modules, {
        "providers": fake_providers,
        "providers.base": fake_base,
        "hermes_cli": fake_hermes_cli,
        "hermes_cli.models": fake_models,
        "hermes_cli.providers": fake_cli_providers,
      }):
        importlib.import_module("antigravity_auth.hermes_provider_plugin")

      self.assertEqual(captured[0].name, "google-gemini-cli")
      for alias in ("antigravity", "antigravity-google", "ag", "gemini-cli", "gemini-oauth"):
        self.assertEqual(fake_models._PROVIDER_ALIASES.get(alias), "google-gemini-cli")
        self.assertEqual(fake_cli_providers.ALIASES.get(alias), "google-gemini-cli")
    finally:
      sys.modules.pop("antigravity_auth.hermes_provider_plugin", None)
      if old_module is not None:
        sys.modules["antigravity_auth.hermes_provider_plugin"] = old_module
      if had_attr:
        setattr(antigravity_auth, "hermes_provider_plugin", old_attr)
      elif hasattr(antigravity_auth, "hermes_provider_plugin"):
        delattr(antigravity_auth, "hermes_provider_plugin")

  def test_interceptor_resolves_antigravity_gemini_model_alias_before_wrapping(self):
    from antigravity_auth import interceptor

    calls = []

    class FakeGeminiCloudCodeClient:
      def __init__(self):
        self._http = SimpleNamespace(event_hooks={})

    def fake_wrap_code_assist_request(**kwargs):
      calls.append(kwargs)
      return {"model": kwargs["model"]}

    fake_agent = types.ModuleType("agent")
    fake_gca = types.ModuleType("agent.gemini_cloudcode_adapter")
    fake_gca.GeminiCloudCodeClient = FakeGeminiCloudCodeClient
    fake_gca.wrap_code_assist_request = fake_wrap_code_assist_request
    fake_agent.gemini_cloudcode_adapter = fake_gca
    old_state = (interceptor._PATCHED, interceptor._ORIGINAL_INIT, interceptor._ORIGINAL_WRAP_CODE_ASSIST)
    interceptor._PATCHED = False
    interceptor._ORIGINAL_INIT = None
    interceptor._ORIGINAL_WRAP_CODE_ASSIST = None
    try:
      with patch.dict(sys.modules, {
        "agent": fake_agent,
        "agent.gemini_cloudcode_adapter": fake_gca,
      }), patch("antigravity_auth.interceptor.get_config", return_value=SimpleNamespace(cli_first=False)):
        self.assertTrue(interceptor.install())
        result = fake_gca.wrap_code_assist_request(
          project_id="project-1",
          model="antigravity-gemini-3.1-pro",
          inner_request={"contents": []},
          user_prompt_id="prompt-1",
        )
        if interceptor._PATCHED:
          interceptor.uninstall()

      self.assertEqual(calls[0]["model"], "gemini-3.1-pro-high")
      self.assertEqual(result["model"], "gemini-3.1-pro-high")
    finally:
      interceptor._PATCHED, interceptor._ORIGINAL_INIT, interceptor._ORIGINAL_WRAP_CODE_ASSIST = old_state

  def test_interceptor_resolves_claude_alias_and_keeps_tool_call_injection(self):
    from antigravity_auth import interceptor

    calls = []

    class FakeGeminiCloudCodeClient:
      def __init__(self):
        self._http = SimpleNamespace(event_hooks={})

    def fake_wrap_code_assist_request(**kwargs):
      calls.append(kwargs)
      return {"model": kwargs["model"]}

    fake_agent = types.ModuleType("agent")
    fake_gca = types.ModuleType("agent.gemini_cloudcode_adapter")
    fake_gca.GeminiCloudCodeClient = FakeGeminiCloudCodeClient
    fake_gca.wrap_code_assist_request = fake_wrap_code_assist_request
    fake_agent.gemini_cloudcode_adapter = fake_gca
    inner_request = {
      "contents": [{"parts": [{"functionCall": {"name": "read_file", "args": {}}}]}],
      "tools": [{"functionDeclarations": [{
        "name": "read_file",
        "parameters": {"type": "object", "properties": {}},
      }]}],
    }
    old_state = (interceptor._PATCHED, interceptor._ORIGINAL_INIT, interceptor._ORIGINAL_WRAP_CODE_ASSIST)
    interceptor._PATCHED = False
    interceptor._ORIGINAL_INIT = None
    interceptor._ORIGINAL_WRAP_CODE_ASSIST = None
    try:
      with patch.dict(sys.modules, {
        "agent": fake_agent,
        "agent.gemini_cloudcode_adapter": fake_gca,
      }), patch("antigravity_auth.interceptor.get_config", return_value=SimpleNamespace(cli_first=False, keep_thinking=False)):
        self.assertTrue(interceptor.install())
        fake_gca.wrap_code_assist_request(
          project_id="project-1",
          model="antigravity-claude-sonnet-4-6-thinking",
          inner_request=inner_request,
        )
        if interceptor._PATCHED:
          interceptor.uninstall()

      self.assertEqual(calls[0]["model"], "claude-sonnet-4-6-thinking")
      self.assertEqual(
        inner_request["contents"][0]["parts"][0]["functionCall"]["id"],
        "tool-call-1",
      )
      self.assertEqual(inner_request["toolConfig"]["functionCallingConfig"]["mode"], "VALIDATED")
    finally:
      interceptor._PATCHED, interceptor._ORIGINAL_INIT, interceptor._ORIGINAL_WRAP_CODE_ASSIST = old_state
