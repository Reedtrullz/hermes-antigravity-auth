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
      "agent.gemini_native_adapter": None,
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

  def test_detect_explains_native_adapter_is_not_cloudcode_adapter(self):
    from antigravity_auth.hermes_compat import detect_hermes_features

    native = types.ModuleType("agent.gemini_native_adapter")
    native.GeminiNativeClient = object
    native.build_gemini_request = lambda **kwargs: kwargs

    with patch.dict(sys.modules, {
      "agent.gemini_cloudcode_adapter": None,
      "agent.gemini_native_adapter": native,
    }):
      rows = detect_hermes_features()

    matching = [row for row in rows if row.check == "Hermes native Gemini adapter internals"]
    self.assertTrue(matching)
    self.assertEqual(matching[0].status, "WARN")
    self.assertIn("not the Cloud Code transport", matching[0].detail)

  def test_model_picker_feature_helper_reports_missing_private_symbols(self):
    from antigravity_auth.hermes_compat import has_required_model_picker_features

    models = types.SimpleNamespace(_PROVIDER_MODELS={}, _PROVIDER_LABELS={})

    ok, missing = has_required_model_picker_features(models)

    self.assertFalse(ok)
    self.assertIn("_PROVIDER_ALIASES", missing)
    self.assertIn("ProviderEntry", missing)


if __name__ == "__main__":
  unittest.main()
