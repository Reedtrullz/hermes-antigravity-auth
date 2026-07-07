import sys
import types
import unittest
from collections import namedtuple
from unittest.mock import patch


class TestHermesCompat(unittest.TestCase):
  def test_detect_reports_standalone_fallback_when_hermes_modules_missing(self):
    from antigravity_auth.hermes_compat import detect_hermes_features

    with patch.dict(sys.modules, {
      "hermes_cli": None,
      "hermes_cli.models": None,
      "hermes_cli.providers": None,
      "hermes_cli.auth": None,
      "agent": None,
      "agent.gemini_cloudcode_adapter": None,
    }):
      rows = detect_hermes_features()

    details = "\n".join(row.detail + " " + row.fix for row in rows)
    self.assertIn("hermes_cli.models unavailable", details)
    self.assertIn("Standalone provider fallback remains available", details)

  def test_detect_passes_when_private_symbols_are_present(self):
    from antigravity_auth.hermes_compat import detect_hermes_features

    ProviderEntry = namedtuple("ProviderEntry", "slug label tui_desc")

    models = types.ModuleType("hermes_cli.models")
    models._PROVIDER_MODELS = {}
    models._PROVIDER_LABELS = {}
    models._PROVIDER_ALIASES = {}
    models.ProviderEntry = ProviderEntry
    models.CANONICAL_PROVIDERS = []
    models.PROVIDER_GROUPS = {}
    models._SLUG_TO_GROUP = {}

    providers = types.ModuleType("hermes_cli.providers")
    providers._LABEL_OVERRIDES = {}
    providers.ALIASES = {}

    auth = types.ModuleType("hermes_cli.auth")
    auth.PROVIDER_REGISTRY = {}
    auth.ProviderConfig = lambda **kwargs: kwargs

    class FakeGeminiCloudCodeClient:
      def _ensure_project_context(self, access_token, model):
        return None

    adapter = types.ModuleType("agent.gemini_cloudcode_adapter")
    adapter.GeminiCloudCodeClient = FakeGeminiCloudCodeClient
    adapter.wrap_code_assist_request = lambda **kwargs: kwargs

    hermes_cli = types.ModuleType("hermes_cli")
    agent = types.ModuleType("agent")

    with patch.dict(sys.modules, {
      "hermes_cli": hermes_cli,
      "hermes_cli.models": models,
      "hermes_cli.providers": providers,
      "hermes_cli.auth": auth,
      "agent": agent,
      "agent.gemini_cloudcode_adapter": adapter,
    }):
      rows = detect_hermes_features()

    checks = {(row.check, row.status) for row in rows}
    self.assertIn(("Hermes model picker internals", "PASS"), checks)
    self.assertIn(("Hermes provider grouping internals", "PASS"), checks)
    self.assertIn(("Hermes provider alias internals", "PASS"), checks)
    self.assertIn(("Hermes auth registry internals", "PASS"), checks)
    self.assertIn(("Hermes Cloud Code adapter internals", "PASS"), checks)

  def test_detect_passes_with_modern_factory_runtime_without_cloudcode_adapter(self):
    from antigravity_auth.hermes_compat import detect_hermes_features

    ProviderEntry = namedtuple("ProviderEntry", "slug label tui_desc")

    models = types.ModuleType("hermes_cli.models")
    models._PROVIDER_MODELS = {}
    models._PROVIDER_LABELS = {}
    models._PROVIDER_ALIASES = {}
    models.ProviderEntry = ProviderEntry
    models.CANONICAL_PROVIDERS = []
    models.PROVIDER_GROUPS = {}
    models._SLUG_TO_GROUP = {}

    providers = types.ModuleType("hermes_cli.providers")
    providers._LABEL_OVERRIDES = {}
    providers.ALIASES = {}

    auth = types.ModuleType("hermes_cli.auth")
    auth.PROVIDER_REGISTRY = {}
    auth.ProviderConfig = lambda **kwargs: kwargs

    runtime_provider = types.ModuleType("hermes_cli.runtime_provider")
    runtime_provider.resolve_runtime_provider = lambda **kwargs: {}

    agent_runtime_helpers = types.ModuleType("agent.agent_runtime_helpers")
    agent_runtime_helpers.create_openai_client = lambda *args, **kwargs: object()

    auxiliary_client = types.ModuleType("agent.auxiliary_client")
    auxiliary_client.resolve_provider_client = lambda *args, **kwargs: (None, None)

    native = types.ModuleType("agent.gemini_native_adapter")
    native.build_gemini_request = lambda **kwargs: {}
    native.translate_gemini_response = lambda payload, model: payload
    native.translate_stream_event = lambda event, model, tool_call_indices: []
    native._iter_sse_events = lambda response: iter(())
    native.gemini_http_error = lambda response, body_text="": RuntimeError(body_text)

    hermes_cli = types.ModuleType("hermes_cli")
    agent = types.ModuleType("agent")

    with patch.dict(sys.modules, {
      "hermes_cli": hermes_cli,
      "hermes_cli.models": models,
      "hermes_cli.providers": providers,
      "hermes_cli.auth": auth,
      "hermes_cli.runtime_provider": runtime_provider,
      "agent": agent,
      "agent.agent_runtime_helpers": agent_runtime_helpers,
      "agent.auxiliary_client": auxiliary_client,
      "agent.gemini_native_adapter": native,
      "agent.gemini_cloudcode_adapter": None,
    }):
      rows = detect_hermes_features()

    checks = {(row.check, row.status) for row in rows}
    self.assertIn(("Hermes runtime factory internals", "PASS"), checks)
    self.assertNotIn(("Hermes Cloud Code adapter internals", "FAIL"), checks)

  def test_modern_runtime_features_require_native_adapter_symbols(self):
    from antigravity_auth.hermes_compat import has_modern_runtime_features

    runtime_provider = types.ModuleType("hermes_cli.runtime_provider")
    runtime_provider.resolve_runtime_provider = lambda **kwargs: {}

    agent_runtime_helpers = types.ModuleType("agent.agent_runtime_helpers")
    agent_runtime_helpers.create_openai_client = lambda *args, **kwargs: object()

    auxiliary_client = types.ModuleType("agent.auxiliary_client")
    auxiliary_client.resolve_provider_client = lambda *args, **kwargs: (None, None)

    native = types.ModuleType("agent.gemini_native_adapter")
    native.build_gemini_request = lambda **kwargs: {}
    native.translate_gemini_response = lambda payload, model: payload
    native.translate_stream_event = lambda event, model, tool_call_indices: []
    native._iter_sse_events = lambda response: iter(())

    with patch.dict(sys.modules, {
      "hermes_cli": types.ModuleType("hermes_cli"),
      "hermes_cli.runtime_provider": runtime_provider,
      "agent": types.ModuleType("agent"),
      "agent.agent_runtime_helpers": agent_runtime_helpers,
      "agent.auxiliary_client": auxiliary_client,
      "agent.gemini_native_adapter": native,
    }):
      self.assertFalse(has_modern_runtime_features())

  def test_model_picker_feature_helper_reports_missing_private_symbols(self):
    from antigravity_auth.hermes_compat import has_required_model_picker_features

    models = types.SimpleNamespace(_PROVIDER_MODELS={}, _PROVIDER_LABELS={})

    ok, missing = has_required_model_picker_features(models)

    self.assertFalse(ok)
    self.assertIn("_PROVIDER_ALIASES", missing)
    self.assertIn("ProviderEntry", missing)


if __name__ == "__main__":
  unittest.main()
