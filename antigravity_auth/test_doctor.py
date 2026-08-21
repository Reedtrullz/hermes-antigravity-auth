import os
import tempfile
import unittest
from unittest.mock import patch


class TestDoctor(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_hermes_home = os.environ.get("HERMES_HOME")
        os.environ["HERMES_HOME"] = self.temp_dir.name

    def tearDown(self):
        if self.original_hermes_home is not None:
            os.environ["HERMES_HOME"] = self.original_hermes_home
        else:
            os.environ.pop("HERMES_HOME", None)
        self.temp_dir.cleanup()

    def test_doctor_output_redacts_refresh_access_and_bearer_tokens(self):
        from antigravity_auth.doctor import format_doctor_rows, run_doctor
        from antigravity_auth.storage import save_accounts, sync_token_to_auth_json

        save_accounts({
            "version": 4,
            "accounts": [{
                "email": "redact@example.com",
                "refreshToken": "raw-refresh-secret",
                "projectId": "project-secret",
                "accessToken": "raw-access-secret",
                "accessTokenExpiresAt": 9999999999999,
            }],
            "activeIndex": 0,
            "cursor": 0,
            "activeIndexByFamily": {"claude": 0, "gemini": 0},
        })
        sync_token_to_auth_json(
            "raw-access-secret",
            "raw-refresh-secret|project-secret",
            "project-secret",
            "redact@example.com",
        )

        with patch("antigravity_auth.doctor.refresh_access_token", side_effect=RuntimeError(
            "Authorization: Bearer raw-access-secret refresh_token=raw-refresh-secret code=oauth-code-secret"
        )):
            output = format_doctor_rows(run_doctor())

        self.assertNotIn("raw-access-secret", output)
        self.assertNotIn("raw-refresh-secret", output)
        self.assertNotIn("oauth-code-secret", output)
        self.assertIn("[REDACTED]", output)

    def test_doctor_reports_missing_hermes_adapter_as_fail(self):
        from antigravity_auth.doctor import run_doctor

        def fake_import(name):
            if name == "agent.gemini_native_adapter":
                raise ImportError("missing native adapter")
            if name == "agent.gemini_cloudcode_adapter":
                raise ImportError("missing adapter")
            return __import__(name)

        with patch("antigravity_auth.doctor.importlib.import_module", side_effect=fake_import):
            rows = run_doctor()

        adapter_rows = [row for row in rows if row.check == "Hermes adapter import"]
        self.assertTrue(adapter_rows)
        self.assertEqual(adapter_rows[0].status, "FAIL")

    def test_doctor_warns_when_modern_runtime_lacks_native_adapter_symbol(self):
        import types
        from antigravity_auth.doctor import _check_hermes_adapter

        runtime_helpers = types.SimpleNamespace(create_openai_client=lambda *args, **kwargs: object())
        runtime_provider = types.SimpleNamespace(resolve_runtime_provider=lambda *args, **kwargs: {})
        auxiliary_client = types.SimpleNamespace(resolve_provider_client=lambda *args, **kwargs: (None, None))
        native_adapter = types.SimpleNamespace(
            build_gemini_request=lambda **kwargs: {},
            translate_gemini_response=lambda payload, model: payload,
            translate_stream_event=lambda event, model, tool_call_indices: [],
            _iter_sse_events=lambda response: iter(()),
        )

        def fake_import(name):
            modules = {
                "agent.agent_runtime_helpers": runtime_helpers,
                "hermes_cli.runtime_provider": runtime_provider,
                "agent.auxiliary_client": auxiliary_client,
                "agent.gemini_native_adapter": native_adapter,
            }
            if name == "agent.gemini_cloudcode_adapter":
                raise ImportError("missing legacy adapter")
            if name in modules:
                return modules[name]
            return __import__(name)

        with patch("antigravity_auth.hermes_compat.importlib.import_module", side_effect=fake_import):
            rows = _check_hermes_adapter()

        runtime_rows = [row for row in rows if row.check == "Hermes runtime factory"]
        self.assertEqual(len(runtime_rows), 1)
        self.assertEqual(runtime_rows[0].status, "WARN")
        self.assertIn("agent.gemini_native_adapter.gemini_http_error", runtime_rows[0].detail)
        adapter_rows = [row for row in rows if row.check == "Hermes adapter import"]
        self.assertEqual(adapter_rows[0].status, "FAIL")

    def test_doctor_reports_retry_streaming_limitation_as_pass(self):
        from antigravity_auth.doctor import format_doctor_rows, run_doctor

        rows = run_doctor()
        retry_rows = [row for row in rows if row.check == "automatic retry"]
        self.assertEqual(len(retry_rows), 1)
        self.assertEqual(retry_rows[0].status, "PASS")
        self.assertIn("streaming responses cannot be replayed", retry_rows[0].detail)

        output = format_doctor_rows(rows)
        self.assertIn("PASS automatic retry", output)
        self.assertIn("streaming responses cannot be replayed", output)

    def test_doctor_reports_claude_routing_health(self):
        from antigravity_auth.doctor import _check_routing_health

        with patch("antigravity_auth.interceptor.get_routing_health", return_value={
            "status": "degraded",
            "detail": "missing interceptor patch",
            "fix": "restart Hermes",
            "claude_routing_ready": False,
        }):
            rows = _check_routing_health()

        checks = {row.check: row for row in rows}
        self.assertEqual(checks["routing health"].status, "WARN")
        self.assertIn("missing interceptor patch", checks["routing health"].detail)
        self.assertEqual(checks["Claude routing"].status, "WARN")
        self.assertIn("restart Hermes", checks["Claude routing"].fix)

    def test_doctor_account_store_locking_uses_actual_probe_success(self):
        from antigravity_auth.doctor import run_doctor

        with patch("antigravity_auth.doctor._probe_process_file_lock", return_value=("fcntl", "probe acquired and released")):
            rows = run_doctor()

        lock_rows = [row for row in rows if row.check == "account store locking"]
        self.assertEqual(len(lock_rows), 1)
        self.assertEqual(lock_rows[0].status, "PASS")
        self.assertIn("probe acquired and released", lock_rows[0].detail)

    def test_doctor_account_store_locking_reports_probe_failure(self):
        from antigravity_auth.doctor import run_doctor

        with patch("antigravity_auth.doctor._probe_process_file_lock", return_value=(None, "lock acquisition failed: denied")):
            rows = run_doctor()

        lock_rows = [row for row in rows if row.check == "account store locking"]
        self.assertEqual(len(lock_rows), 1)
        self.assertEqual(lock_rows[0].status, "WARN")
        self.assertIn("lock acquisition failed", lock_rows[0].detail)

    def test_doctor_account_store_reports_malformed_private_json(self):
        from antigravity_auth.doctor import _check_account_store
        from antigravity_auth.storage import get_accounts_json_path

        path = get_accounts_json_path()
        path.write_text("{not-json", encoding="utf-8")
        os.chmod(path, 0o600)

        rows = _check_account_store()
        store_rows = [row for row in rows if row.check == "account store"]
        permission_rows = [row for row in rows if row.check == "account store permissions"]

        self.assertEqual(len(store_rows), 1)
        self.assertEqual(store_rows[0].status, "FAIL")
        self.assertIn("could not parse", store_rows[0].detail)
        self.assertEqual(len(permission_rows), 1)
        self.assertEqual(permission_rows[0].status, "PASS")

    def test_doctor_account_store_reports_permissions_separately(self):
        from antigravity_auth.doctor import _check_account_store
        from antigravity_auth.storage import save_accounts, get_accounts_json_path

        save_accounts({
            "version": 4,
            "accounts": [],
            "activeIndex": 0,
            "cursor": 0,
            "activeIndexByFamily": {"claude": 0, "gemini": 0},
        })
        path = get_accounts_json_path()
        os.chmod(path, 0o644)

        rows = _check_account_store()
        store_rows = [row for row in rows if row.check == "account store"]
        permission_rows = [row for row in rows if row.check == "account store permissions"]

        self.assertEqual(store_rows[0].status, "PASS")
        self.assertEqual(permission_rows[0].status, "WARN")
        self.assertIn("permissions", permission_rows[0].detail)

    def test_doctor_surfaces_provider_diagnostics(self):
        from antigravity_auth import hermes_provider_plugin
        from antigravity_auth.doctor import _check_provider_registration

        diagnostics = [{
            "status": "WARN",
            "check": "provider aliases",
            "detail": "could not patch aliases",
            "fix": "use google-gemini-cli",
        }]

        with patch.object(hermes_provider_plugin, "get_provider_diagnostics", return_value=diagnostics):
            rows = _check_provider_registration()

        provider_rows = [row for row in rows if row.check == "provider aliases"]
        self.assertEqual(len(provider_rows), 1)
        self.assertEqual(provider_rows[0].status, "WARN")
        self.assertIn("could not patch aliases", provider_rows[0].detail)
        self.assertIn("google-gemini-cli", provider_rows[0].fix)

    def test_doctor_reports_installed_file_wrappers(self):
        from pathlib import Path
        from antigravity_auth.doctor import _check_installed_wrappers
        from antigravity_auth.install_plugins import install_plugins

        install_plugins(Path(self.temp_dir.name))
        rows = _check_installed_wrappers()
        checks = {row.check: row for row in rows}

        self.assertEqual(checks["CLI file plugin"].status, "PASS")
        self.assertEqual(checks["provider file plugin"].status, "PASS")

    def test_doctor_fails_wrapper_content_drift(self):
        from pathlib import Path
        from antigravity_auth.doctor import _check_installed_wrappers
        from antigravity_auth.install_plugins import install_plugins

        install_plugins(Path(self.temp_dir.name))
        cli_init = Path(self.temp_dir.name) / "plugins" / "antigravity-cli" / "__init__.py"
        cli_init.write_text(cli_init.read_text(encoding="utf-8") + "\n# stale local edit\n", encoding="utf-8")

        rows = _check_installed_wrappers()
        cli_rows = [row for row in rows if row.check == "CLI file plugin"]

        self.assertEqual(cli_rows[0].status, "FAIL")
        self.assertIn("content differs", cli_rows[0].detail)

    def test_doctor_fails_malformed_file_wrapper_manifest(self):
        from pathlib import Path
        from antigravity_auth.doctor import _check_installed_wrappers

        cli_dir = Path(self.temp_dir.name) / "plugins" / "antigravity-cli"
        cli_dir.mkdir(parents=True)
        (cli_dir / "__init__.py").write_text("load_cli_register\n", encoding="utf-8")
        (cli_dir / "plugin.yaml").write_text("name: wrong\nkind: standalone\n", encoding="utf-8")

        rows = _check_installed_wrappers()
        cli_rows = [row for row in rows if row.check == "CLI file plugin"]

        self.assertEqual(cli_rows[0].status, "FAIL")
        self.assertIn("name=wrong", cli_rows[0].detail)

    def test_doctor_reports_hermes_home_permissions(self):
        from antigravity_auth.doctor import _check_hermes_home_permissions

        row = _check_hermes_home_permissions()

        self.assertEqual(row.status, "PASS")
        self.assertEqual(row.check, "Hermes home permissions")
        self.assertIn("0o700", row.detail)

    def test_doctor_reports_missing_oauth_client_credentials(self):
        from antigravity_auth.doctor import _check_oauth_client_credentials

        with patch.dict("os.environ", {"HERMES_HOME": self.temp_dir.name}, clear=True):
            row = _check_oauth_client_credentials()

        self.assertEqual(row.status, "WARN")
        self.assertEqual(row.check, "OAuth client credentials")
        self.assertIn("not configured", row.detail)
        self.assertIn("set-credentials", row.fix)

    def test_doctor_warns_when_active_cli_toolsets_are_unknown(self):
        try:
            import yaml  # noqa: F401
        except ImportError:
            self.skipTest("pyyaml not installed")
        from pathlib import Path
        from antigravity_auth.doctor import _check_config

        config_path = Path(self.temp_dir.name) / "config.yaml"
        config_path.write_text(
            "\n".join([
                "platform_toolsets:",
                "  cli:",
                "    - web",
                "    - messaging",
                "    - moa",
                "    - custom-stale",
                "",
            ]),
            encoding="utf-8",
        )

        with patch("antigravity_auth.doctor._available_hermes_toolsets", return_value={"terminal", "web"}):
            rows = _check_config()

        toolset_rows = [row for row in rows if row.check == "Hermes CLI toolsets"]
        self.assertEqual(len(toolset_rows), 1)
        self.assertEqual(toolset_rows[0].status, "WARN")
        self.assertIn("messaging", toolset_rows[0].detail)
        self.assertIn("moa", toolset_rows[0].detail)
        self.assertIn("custom-stale", toolset_rows[0].detail)
        self.assertIn("platform_toolsets.cli", toolset_rows[0].fix)

    def test_doctor_warns_when_oauth_credential_file_is_world_readable(self):
        from pathlib import Path
        from antigravity_auth.doctor import _check_oauth_client_credentials

        path = Path(self.temp_dir.name) / "antigravity-credentials.json"
        path.write_text('{"client_id":"id","client_secret":"secret"}', encoding="utf-8")
        os.chmod(path, 0o644)

        with patch.dict("os.environ", {"HERMES_HOME": self.temp_dir.name}, clear=True):
            row = _check_oauth_client_credentials()

        self.assertEqual(row.status, "WARN")
        self.assertEqual(row.check, "OAuth client credentials")
        self.assertIn("permissions are 0o644", row.detail)
        self.assertIn("chmod 600", row.fix)
