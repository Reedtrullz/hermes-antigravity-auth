from __future__ import annotations

import copy
import importlib
import json
import os
import sys
import types
import unittest
from typing import Any

from antigravity_auth.transform.envelope import (
    MODEL_NAME_MAP,
    build_antigravity_envelope,
    build_antigravity_headers,
    build_antigravity_url,
    extract_model_from_url,
    generate_synthetic_project_id,
    is_antigravity_request,
    resolve_model_for_header_style,
)


_OAUTH_ENV_VARS = (
    "HERMES_GEMINI_CLIENT_ID",
    "HERMES_GEMINI_CLIENT_SECRET",
    "ANTIGRAVITY_CLIENT_ID",
    "ANTIGRAVITY_CLIENT_SECRET",
)


def _load_antigravity_models_with_provider_stubs():
    import antigravity_auth

    class ProviderProfile:
        def __init__(self, **kwargs):
            for key, value in kwargs.items():
                setattr(self, key, value)

    providers_mod = types.ModuleType("providers")
    providers_mod.__path__ = []
    setattr(providers_mod, "register_provider", lambda provider: None)

    base_mod = types.ModuleType("providers.base")
    setattr(base_mod, "ProviderProfile", ProviderProfile)
    setattr(providers_mod, "base", base_mod)

    hermes_cli_mod = types.ModuleType("hermes_cli")

    module_names = (
        "providers",
        "providers.base",
        "hermes_cli",
        "hermes_cli.models",
        "hermes_cli.auth",
        "antigravity_auth.hermes_provider_plugin",
    )
    sentinel = object()
    previous_modules: dict[str, Any] = {
        name: sys.modules.get(name, sentinel)
        for name in module_names
    }
    previous_plugin_attr = getattr(
        antigravity_auth,
        "hermes_provider_plugin",
        sentinel,
    )
    previous_env: dict[str, str | None] = {
        name: os.environ.get(name)
        for name in _OAUTH_ENV_VARS
    }

    sys.modules["providers"] = providers_mod
    sys.modules["providers.base"] = base_mod
    sys.modules["hermes_cli"] = hermes_cli_mod
    sys.modules.pop("hermes_cli.models", None)
    sys.modules.pop("hermes_cli.auth", None)
    sys.modules.pop("antigravity_auth.hermes_provider_plugin", None)

    try:
        plugin = importlib.import_module("antigravity_auth.hermes_provider_plugin")
        return tuple(plugin.ANTIGRAVITY_MODELS)
    finally:
        for name, value in previous_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        for name, module in previous_modules.items():
            if module is sentinel:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module
        if previous_plugin_attr is sentinel:
            if hasattr(antigravity_auth, "hermes_provider_plugin"):
                delattr(antigravity_auth, "hermes_provider_plugin")
        else:
            setattr(
                antigravity_auth,
                "hermes_provider_plugin",
                previous_plugin_attr,
            )


class TestLoadAntigravityModelsWithProviderStubs(unittest.TestCase):
    def test_restores_stub_modules_and_parent_package_attribute(self):
        import antigravity_auth

        sentinel = object()
        module_names = (
            "providers",
            "providers.base",
            "hermes_cli",
            "hermes_cli.models",
            "hermes_cli.auth",
            "antigravity_auth.hermes_provider_plugin",
        )
        original_modules: dict[str, Any] = {
            name: sys.modules.get(name, sentinel)
            for name in module_names
        }
        original_attr = getattr(
            antigravity_auth,
            "hermes_provider_plugin",
            sentinel,
        )

        models = _load_antigravity_models_with_provider_stubs()

        self.assertTrue(models)
        if original_attr is sentinel:
            self.assertFalse(hasattr(antigravity_auth, "hermes_provider_plugin"))
        else:
            self.assertIs(
                getattr(antigravity_auth, "hermes_provider_plugin"),
                original_attr,
            )
        for name, module in original_modules.items():
            with self.subTest(module=name):
                if module is sentinel:
                    self.assertNotIn(name, sys.modules)
                else:
                    self.assertIn(name, sys.modules)
                    self.assertIs(sys.modules[name], module)

    def test_restores_oauth_environment_variables(self):
        sentinel = object()
        original_env = {
            name: os.environ.get(name, sentinel)
            for name in _OAUTH_ENV_VARS
        }

        os.environ.pop("HERMES_GEMINI_CLIENT_ID", None)
        os.environ.pop("HERMES_GEMINI_CLIENT_SECRET", None)
        os.environ["ANTIGRAVITY_CLIENT_ID"] = "stub-id"
        os.environ["ANTIGRAVITY_CLIENT_SECRET"] = "stub-secret"

        try:
            models = _load_antigravity_models_with_provider_stubs()

            self.assertTrue(models)
            self.assertNotIn("HERMES_GEMINI_CLIENT_ID", os.environ)
            self.assertNotIn("HERMES_GEMINI_CLIENT_SECRET", os.environ)
            self.assertEqual(os.environ.get("ANTIGRAVITY_CLIENT_ID"), "stub-id")
            self.assertEqual(
                os.environ.get("ANTIGRAVITY_CLIENT_SECRET"),
                "stub-secret",
            )
        finally:
            for name, value in original_env.items():
                if isinstance(value, str):
                    os.environ[name] = value
                else:
                    os.environ.pop(name, None)


class TestBuildAntigravityHeaders(unittest.TestCase):
    def test_gemini_cli_style_has_nodejs_ua(self):
        headers = build_antigravity_headers(header_style="gemini-cli")
        self.assertIn("User-Agent", headers)
        self.assertIn("google-api-nodejs-client", headers["User-Agent"])
        self.assertEqual("gl-node/22.17.0", headers.get("X-Goog-Api-Client"))

    def test_antigravity_style_has_client_metadata_json(self):
        headers = build_antigravity_headers(header_style="antigravity")
        self.assertIn("Client-Metadata", headers)
        metadata = json.loads(headers["Client-Metadata"])
        self.assertEqual("ANTIGRAVITY", metadata["ideType"])
        self.assertIn("platform", metadata)
        self.assertEqual("GEMINI", metadata["pluginType"])

    def test_antigravity_style_has_user_agent(self):
        headers = build_antigravity_headers(header_style="antigravity")
        self.assertIn("User-Agent", headers)
        self.assertTrue(headers["User-Agent"].startswith("antigravity/"))

    def test_gemini_cli_style_has_client_metadata_string(self):
        headers = build_antigravity_headers(header_style="gemini-cli")
        self.assertIn("Client-Metadata", headers)
        self.assertIn("ideType=", headers["Client-Metadata"])
        self.assertIn("pluginType=GEMINI", headers["Client-Metadata"])

    def test_fingerprint_user_agent_overrides_ua(self):
        custom_ua = "Mozilla/5.0 CustomAgent/2.0"
        headers = build_antigravity_headers(
            header_style="antigravity",
            fingerprint_user_agent=custom_ua,
        )
        self.assertEqual(custom_ua, headers["User-Agent"])


class TestResolveModelForHeaderStyle(unittest.TestCase):
    def test_gemini_cli_maps_antigravity_prefix(self):
        result = resolve_model_for_header_style(
            "antigravity-gemini-3-pro", "gemini-cli"
        )
        self.assertEqual("gemini-3-pro-preview", result)

    def test_gemini_cli_strips_antigravity_prefix_claude(self):
        result = resolve_model_for_header_style(
            "antigravity-claude-sonnet-4-6", "gemini-cli"
        )
        self.assertEqual("claude-sonnet-4-6", result)

    def test_antigravity_style_maps_aliases(self):
        result = resolve_model_for_header_style(
            "antigravity-gemini-3-pro", "antigravity"
        )
        self.assertEqual("gemini-3-pro-preview", result)

    def test_antigravity_style_maps_claude_aliases(self):
        result = resolve_model_for_header_style(
            "antigravity-claude-sonnet-4-6", "antigravity"
        )
        self.assertEqual("claude-sonnet-4-6", result)

    def test_no_prefix_no_change_for_gemini_cli(self):
        result = resolve_model_for_header_style(
            "gemini-3-flash", "gemini-cli"
        )
        self.assertEqual("gemini-3-flash", result)

    def test_no_prefix_no_change_for_antigravity(self):
        result = resolve_model_for_header_style(
            "gemini-3-flash", "antigravity"
        )
        self.assertEqual("gemini-3-flash", result)

    # New Antigravity 2.0 models
    def test_gemini_cli_strips_prefix_3_5_flash(self):
        result = resolve_model_for_header_style(
            "antigravity-gemini-3.5-flash", "gemini-cli"
        )
        self.assertEqual("gemini-3.5-flash-low", result)

    def test_gemini_cli_maps_3_5_flash_quality_aliases(self):
        expected = {
            "gemini-3.5-flash-high": "gemini-3-flash-agent",
            "gemini-3.5-flash-medium": "gemini-3.5-flash-low",
            "gemini-3.5-flash-low": "gemini-3.5-flash-low",
            "gemini-3.5-flash-minimal": "gemini-3.5-flash-low",
        }
        for model, backend_model in expected.items():
            with self.subTest(model=model):
                self.assertEqual(
                    backend_model,
                    resolve_model_for_header_style(model, "gemini-cli"),
                )

    def test_gemini_cli_strips_prefix_3_1_pro_high(self):
        result = resolve_model_for_header_style(
            "antigravity-gemini-3.1-pro", "gemini-cli"
        )
        self.assertEqual("gemini-3.1-pro-low", result)

    def test_gemini_cli_maps_3_1_pro_quality_aliases(self):
        for model in (
            "gemini-3.1-pro-high",
            "gemini-3.1-pro-low",
        ):
            with self.subTest(model=model):
                self.assertEqual(
                    model,
                    resolve_model_for_header_style(model, "gemini-cli"),
                )
        self.assertEqual(
            "gemini-3.1-pro",
            resolve_model_for_header_style("gemini-3.1-pro-preview", "gemini-cli"),
        )

    def test_gemini_cli_strips_prefix_sonnet_thinking(self):
        result = resolve_model_for_header_style(
            "antigravity-claude-sonnet-4-6-thinking", "gemini-cli"
        )
        self.assertEqual("claude-sonnet-4-6-thinking", result)

    def test_gemini_cli_strips_prefix_gpt_oss(self):
        result = resolve_model_for_header_style(
            "antigravity-gpt-oss-120b", "gemini-cli"
        )
        self.assertEqual("gpt-oss-120b-medium", result)

    def test_resolve_model_for_header_style_uses_model_name_map_for_antigravity_aliases(self):
        self.assertEqual(
            "gemini-3.1-pro-low",
            resolve_model_for_header_style("antigravity-gemini-3.1-pro", "antigravity"),
        )
        self.assertEqual(
            "claude-sonnet-4-6-thinking",
            resolve_model_for_header_style(
                "antigravity-claude-sonnet-4-6-thinking", "antigravity"
            ),
        )

    def test_resolve_model_for_header_style_matches_antigravity_model_table(self):
        for model in _load_antigravity_models_with_provider_stubs():
            with self.subTest(model=model):
                self.assertEqual(
                    MODEL_NAME_MAP.get(model, model),
                    resolve_model_for_header_style(model, "antigravity"),
                )


class TestBuildAntigravityUrl(unittest.TestCase):
    def test_streaming_has_alt_sse(self):
        url = build_antigravity_url(
            base_endpoint="https://example.com",
            model="gemini-3-pro",
            streaming=True,
        )
        self.assertIn("?alt=sse", url)

    def test_non_streaming_lacks_alt_sse(self):
        url = build_antigravity_url(
            base_endpoint="https://example.com",
            model="gemini-3-pro",
            streaming=False,
        )
        self.assertNotIn("?alt=sse", url)
        self.assertNotIn("alt=sse", url)

    def test_url_format_streaming(self):
        url = build_antigravity_url(
            base_endpoint="https://example.com",
            model="gemini-3-pro",
            action="streamGenerateContent",
            streaming=True,
        )
        self.assertEqual(
            "https://example.com/v1internal:streamGenerateContent?alt=sse",
            url,
        )

    def test_url_format_non_streaming(self):
        url = build_antigravity_url(
            base_endpoint="https://example.com",
            model="gemini-3-pro",
            action="generateContent",
            streaming=False,
        )
        self.assertEqual(
            "https://example.com/v1internal:generateContent",
            url,
        )


class TestBuildAntigravityEnvelope(unittest.TestCase):
    def test_build_antigravity_envelope_does_not_mutate_input_or_leave_snake_case_system_instruction(self):
        payload = {
            "contents": [],
            "system_instruction": {"parts": [{"text": "sys"}]},
        }
        original = copy.deepcopy(payload)

        envelope = build_antigravity_envelope(
            payload,
            model="claude-sonnet-4-6",
            project_id="proj",
        )

        self.assertEqual(payload, original)
        self.assertIn("systemInstruction", envelope["request"])
        self.assertNotIn("system_instruction", envelope["request"])

    def test_build_antigravity_envelope_does_not_mutate_nested_system_instruction_parts(self):
        payload = {
            "contents": [],
            "systemInstruction": {"parts": [{"text": "sys"}]},
        }
        original = copy.deepcopy(payload)

        envelope = build_antigravity_envelope(
            payload,
            model="claude-sonnet-4-6",
            project_id="proj",
        )

        self.assertEqual(payload, original)
        self.assertIn(
            "You are Antigravity",
            envelope["request"]["systemInstruction"]["parts"][0]["text"],
        )

    def test_antigravity_style_includes_system_instruction(self):
        envelope = build_antigravity_envelope(
            request_payload={"contents": []},
            model="antigravity-gemini-3-pro",
            project_id="test-project",
            header_style="antigravity",
        )
        self.assertIn("systemInstruction", envelope["request"])
        si = envelope["request"]["systemInstruction"]
        self.assertIn("parts", si)
        self.assertTrue(len(si["parts"]) > 0)
        self.assertIn("text", si["parts"][0])

    def test_preserves_existing_system_instruction_content(self):
        existing_text = "You are a helpful assistant."
        envelope = build_antigravity_envelope(
            request_payload={
                "contents": [],
                "systemInstruction": {
                    "role": "system",
                    "parts": [{"text": existing_text}],
                },
            },
            model="antigravity-gemini-3-pro",
            project_id="test-project",
            header_style="antigravity",
        )
        si = envelope["request"]["systemInstruction"]
        self.assertIn(existing_text, si["parts"][0]["text"])
        # Should still contain the antigravity prefix
        self.assertIn("You are Antigravity", si["parts"][0]["text"])

    def test_has_request_type_agent(self):
        envelope = build_antigravity_envelope(
            request_payload={"contents": []},
            model="antigravity-gemini-3-pro",
            project_id="test-project",
            header_style="antigravity",
        )
        self.assertEqual("agent", envelope["requestType"])

    def test_request_id_starts_with_agent_dash(self):
        envelope = build_antigravity_envelope(
            request_payload={"contents": []},
            model="antigravity-gemini-3-pro",
            project_id="test-project",
            header_style="antigravity",
        )
        self.assertIn("requestId", envelope)
        self.assertTrue(envelope["requestId"].startswith("agent-"))

    def test_envelope_includes_project_and_model(self):
        envelope = build_antigravity_envelope(
            request_payload={"contents": []},
            model="antigravity-gemini-3-pro",
            project_id="test-project",
            header_style="antigravity",
        )
        self.assertEqual("test-project", envelope["project"])
        self.assertEqual("antigravity-gemini-3-pro", envelope["model"])

    def test_gemini_cli_style_no_request_type(self):
        envelope = build_antigravity_envelope(
            request_payload={"contents": []},
            model="gemini-3-pro",
            project_id="test-project",
            header_style="gemini-cli",
        )
        self.assertNotIn("requestType", envelope)
        self.assertNotIn("requestId", envelope)

    def test_gemini_cli_style_no_system_instruction_injected(self):
        payload = {"contents": []}
        envelope = build_antigravity_envelope(
            request_payload=payload,
            model="gemini-3-pro",
            project_id="test-project",
            header_style="gemini-cli",
        )
        self.assertNotIn("systemInstruction", envelope["request"])

    def test_existing_system_instruction_string_format(self):
        existing_text = "You are a test bot."
        envelope = build_antigravity_envelope(
            request_payload={
                "contents": [],
                "systemInstruction": existing_text,
            },
            model="antigravity-gemini-3-pro",
            project_id="test-project",
            header_style="antigravity",
        )
        si = envelope["request"]["systemInstruction"]
        self.assertEqual("user", si["role"])
        self.assertIn(existing_text, si["parts"][0]["text"])

    def test_antigravity_style_user_agent_field(self):
        envelope = build_antigravity_envelope(
            request_payload={"contents": []},
            model="antigravity-gemini-3-pro",
            project_id="test-project",
            header_style="antigravity",
        )
        self.assertEqual("antigravity", envelope["userAgent"])


class TestIsAntigravityRequest(unittest.TestCase):
    def test_true_for_generativelanguage(self):
        url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-3-pro:generateContent"
        self.assertTrue(is_antigravity_request(url))

    def test_true_for_generativelanguage_alternative(self):
        url = "https://us-central1-aiplatform.googleapis.com/v1/projects/test/locations/us-central1/publishers/google/models/gemini-3-pro:generateContent"
        self.assertFalse(is_antigravity_request(url))

    def test_false_for_anthropic(self):
        url = "https://api.anthropic.com/v1/messages"
        self.assertFalse(is_antigravity_request(url))

    def test_false_for_openai(self):
        url = "https://api.openai.com/v1/chat/completions"
        self.assertFalse(is_antigravity_request(url))

    def test_true_when_generativelanguage_in_subdomain(self):
        url = "https://generativelanguage.googleapis.com/some/other/path"
        self.assertTrue(is_antigravity_request(url))


class TestGenerateSyntheticProjectId(unittest.TestCase):
    def test_three_parts_dash_separated(self):
        pid = generate_synthetic_project_id()
        parts = pid.split("-")
        self.assertEqual(3, len(parts))

    def test_last_part_hex(self):
        pid = generate_synthetic_project_id()
        parts = pid.split("-")
        hex_part = parts[2]
        self.assertEqual(5, len(hex_part))
        # Should be hex characters
        for c in hex_part:
            self.assertIn(c, "0123456789abcdef")

    def test_generates_different_ids(self):
        ids = {generate_synthetic_project_id() for _ in range(20)}
        # With 8*8*16^5 combinations, 20 should almost certainly be unique
        self.assertGreater(len(ids), 1)


class TestExtractModelFromUrl(unittest.TestCase):
    def test_extracts_from_generativelanguage_url(self):
        url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-3-pro:generateContent"
        result = extract_model_from_url(url)
        self.assertEqual("gemini-3-pro", result)

    def test_extracts_from_daily_url(self):
        url = "https://daily-cloudcode-pa.sandbox.googleapis.com/v1internal:streamGenerateContent?alt=sse"
        # This URL doesn't match the /models/ pattern
        result = extract_model_from_url(url)
        self.assertIsNone(result)

    def test_extracts_claude_model(self):
        url = "https://generativelanguage.googleapis.com/v1beta/models/claude-sonnet-4-6:streamGenerateContent"
        result = extract_model_from_url(url)
        self.assertEqual("claude-sonnet-4-6", result)

    def test_returns_none_for_non_matching_url(self):
        url = "https://api.anthropic.com/v1/messages"
        result = extract_model_from_url(url)
        self.assertIsNone(result)

    def test_extracts_from_url_with_query_params(self):
        url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-3-flash:generateContent?alt=sse"
        result = extract_model_from_url(url)
        self.assertEqual("gemini-3-flash", result)


if __name__ == "__main__":
    unittest.main()
