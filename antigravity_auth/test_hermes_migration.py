import os
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch


class TestHermesMigrationIntegration(unittest.TestCase):
    def test_auth_sync_exports_google_oauth_sync(self):
        from antigravity_auth.auth_sync import sync_token_to_google_oauth
        self.assertTrue(callable(sync_token_to_google_oauth))

    def test_auth_sync_top_level_fallback_import_exports_google_oauth_sync(self):
        package_dir = Path(__file__).resolve().parent
        script = (
            "import sys\n"
            f"sys.path.insert(0, {str(package_dir)!r})\n"
            "import auth_sync\n"
            "print(callable(auth_sync.sync_token_to_google_oauth))\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "True")

    def test_cli_top_level_import_avoids_local_token_shadowing(self):
        package_dir = Path(__file__).resolve().parent
        script = (
            "import sys\n"
            f"sys.path.insert(0, {str(package_dir)!r})\n"
            "import cli\n"
            "assert hasattr(cli, 'sync_token_to_google_oauth')\n"
            "print('CLI_TOP_LEVEL_OK')\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(result.stdout.strip(), "CLI_TOP_LEVEL_OK")

    def test_accounts_manager_imports_and_uses_hermes_home(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {"HERMES_HOME": tmpdir}):
                from antigravity_auth.accounts.manager import AccountManager

                manager = AccountManager.load_from_disk()
                self.assertTrue(manager.save_to_disk())
                self.assertTrue((Path(tmpdir) / "antigravity-accounts.json").exists())

    def test_sync_token_to_google_oauth_uses_hermes_credentials_store(self):
        from antigravity_auth.cli import sync_token_to_google_oauth

        saved = {}

        class FakeGoogleCredentials:
            def __init__(
                self,
                access_token,
                refresh_token,
                expires_ms,
                email,
                project_id,
                managed_project_id,
            ):
                self.access_token = access_token
                self.refresh_token = refresh_token
                self.expires_ms = expires_ms
                self.email = email
                self.project_id = project_id
                self.managed_project_id = managed_project_id

        fake_agent = types.ModuleType("agent")
        fake_google_oauth = types.ModuleType("agent.google_oauth")
        fake_google_oauth.GoogleCredentials = FakeGoogleCredentials

        def save_credentials(credentials):
            saved["credentials"] = credentials

        fake_google_oauth.save_credentials = save_credentials

        with patch.dict(sys.modules, {
            "agent": fake_agent,
            "agent.google_oauth": fake_google_oauth,
        }):
            ok = sync_token_to_google_oauth(
                access_token="access",
                refresh_token="refresh-token|project-1|managed-1",
                project_id="",
                email="user@example.com",
                expires_ms=123456,
            )

        self.assertTrue(ok)
        credentials = saved["credentials"]
        self.assertEqual(credentials.access_token, "access")
        self.assertEqual(credentials.refresh_token, "refresh-token")
        self.assertEqual(credentials.project_id, "project-1")
        self.assertEqual(credentials.managed_project_id, "managed-1")
        self.assertEqual(credentials.email, "user@example.com")
        self.assertEqual(credentials.expires_ms, 123456)

    def test_sync_token_to_google_oauth_degrades_when_hermes_module_missing(self):
        from antigravity_auth.cli import sync_token_to_google_oauth

        with patch.dict(sys.modules, {"agent.google_oauth": None}):
            self.assertFalse(sync_token_to_google_oauth("access", "refresh"))

    def test_all_auth_store_sync_skips_google_oauth_when_auth_json_fails(self):
        from antigravity_auth.auth_sync import sync_token_to_all_auth_stores

        saved = []

        class FakeGoogleCredentials:
            def __init__(self, **kwargs):
                self.__dict__.update(kwargs)

        fake_agent = types.ModuleType("agent")
        fake_google_oauth = types.ModuleType("agent.google_oauth")
        fake_google_oauth.GoogleCredentials = FakeGoogleCredentials
        fake_google_oauth.save_credentials = lambda credentials: saved.append(credentials)

        with patch("antigravity_auth.auth_sync.sync_token_to_auth_json", side_effect=RuntimeError("no write")), \
             patch.dict(sys.modules, {
                 "agent": fake_agent,
                 "agent.google_oauth": fake_google_oauth,
             }):
            result = sync_token_to_all_auth_stores(
                "access",
                "refresh|project",
                project_id="project",
                email="user@example.com",
            )

        self.assertFalse(result.auth_json)
        self.assertFalse(result.google_oauth)
        self.assertEqual(saved, [])

    def test_provider_plugin_bridges_antigravity_env_credentials(self):
        import importlib
        import antigravity_auth

        captured = []

        class FakeProviderProfile:
            def __init__(self, **kwargs):
                self.__dict__.update(kwargs)

        fake_providers = types.ModuleType("providers")
        fake_providers.register_provider = lambda profile: captured.append(profile)
        fake_base = types.ModuleType("providers.base")
        fake_base.ProviderProfile = FakeProviderProfile

        had_attr = hasattr(antigravity_auth, "hermes_provider_plugin")
        old_attr = getattr(antigravity_auth, "hermes_provider_plugin", None)
        old_module = sys.modules.pop("antigravity_auth.hermes_provider_plugin", None)
        try:
            with patch.dict(sys.modules, {
                "providers": fake_providers,
                "providers.base": fake_base,
            }), patch.dict(os.environ, {
                "ANTIGRAVITY_CLIENT_ID": "ag-client-id",
                "ANTIGRAVITY_CLIENT_SECRET": "ag-client-secret",
            }, clear=False):
                os.environ.pop("HERMES_GEMINI_CLIENT_ID", None)
                os.environ.pop("HERMES_GEMINI_CLIENT_SECRET", None)
                importlib.import_module("antigravity_auth.hermes_provider_plugin")
                self.assertEqual(os.environ.get("HERMES_GEMINI_CLIENT_ID"), "ag-client-id")
                self.assertEqual(os.environ.get("HERMES_GEMINI_CLIENT_SECRET"), "ag-client-secret")

            self.assertEqual(captured[0].name, "google-gemini-cli")
        finally:
            sys.modules.pop("antigravity_auth.hermes_provider_plugin", None)
            if old_module is not None:
                sys.modules["antigravity_auth.hermes_provider_plugin"] = old_module
            if had_attr:
                setattr(antigravity_auth, "hermes_provider_plugin", old_attr)
            elif hasattr(antigravity_auth, "hermes_provider_plugin"):
                delattr(antigravity_auth, "hermes_provider_plugin")

    def test_install_plugins_writes_hermes_plugin_layout(self):
        from antigravity_auth.install_plugins import install_plugins

        with tempfile.TemporaryDirectory() as tmpdir:
            paths = install_plugins(Path(tmpdir))

            self.assertEqual(len(paths), 2)
            self.assertTrue((Path(tmpdir) / "plugins" / "antigravity-cli" / "__init__.py").exists())
            self.assertTrue((Path(tmpdir) / "plugins" / "antigravity-cli" / "plugin.yaml").exists())
            self.assertTrue((
                Path(tmpdir)
                / "plugins"
                / "model-providers"
                / "antigravity"
                / "__init__.py"
            ).exists())
            self.assertTrue((
                Path(tmpdir)
                / "plugins"
                / "model-providers"
                / "antigravity"
                / "plugin.yaml"
            ).exists())

    def test_resolve_hermes_python_from_bash_launcher(self):
        from antigravity_auth.install_plugins import resolve_hermes_python

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            python = root / "hermes-agent" / "venv" / "bin" / "python3"
            hermes_venv_bin = root / "hermes-agent" / "venv" / "bin" / "hermes"
            launcher = root / "bin" / "hermes"
            python.parent.mkdir(parents=True)
            launcher.parent.mkdir(parents=True)
            python.write_text("", encoding="utf-8")
            hermes_venv_bin.write_text(f"#!{python}\n", encoding="utf-8")
            launcher.write_text(f'#!/usr/bin/env bash\nexec "{hermes_venv_bin}" "$@"\n', encoding="utf-8")

            self.assertEqual(resolve_hermes_python(str(launcher)), python)

    def test_resolve_hermes_python_from_version_output(self):
        from antigravity_auth.install_plugins import resolve_hermes_python

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            project = root / "hermes-agent"
            python = project / "venv" / "bin" / "python3"
            launcher = root / "bin" / "hermes"
            python.parent.mkdir(parents=True)
            launcher.parent.mkdir(parents=True)
            python.write_text("", encoding="utf-8")
            launcher.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            result = subprocess.CompletedProcess(
                [str(launcher), "--version"],
                0,
                stdout=f"Hermes Agent v0.16.0\nProject: {project}\nPython: 3.11.15\n",
                stderr="",
            )

            with patch("antigravity_auth.install_plugins.subprocess.run", return_value=result):
                self.assertEqual(resolve_hermes_python(str(launcher)), python)

    def test_install_package_targets_hermes_python(self):
        from antigravity_auth.install_plugins import install_package_in_hermes_python

        with tempfile.TemporaryDirectory() as tmpdir:
            python = Path(tmpdir) / "hermes-agent" / "venv" / "bin" / "python3"
            python.parent.mkdir(parents=True)
            python.write_text("", encoding="utf-8")

            with patch("antigravity_auth.install_plugins.subprocess.run") as run:
                installed = install_package_in_hermes_python(python, "example-package")

            self.assertTrue(installed)
            run.assert_called_once_with([
                str(python),
                "-m",
                "pip",
                "install",
                "--upgrade",
                "example-package",
            ], check=True)

    def test_install_package_preserves_venv_python_symlink_path(self):
        from antigravity_auth.install_plugins import install_package_in_hermes_python

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            base_python = root / "uv-python" / "bin" / "python3.11"
            venv_python = root / "hermes-agent" / "venv" / "bin" / "python3"
            base_python.parent.mkdir(parents=True)
            venv_python.parent.mkdir(parents=True)
            base_python.write_text("", encoding="utf-8")
            venv_python.symlink_to(base_python)

            with patch("antigravity_auth.install_plugins.subprocess.run") as run:
                installed = install_package_in_hermes_python(venv_python, "example-package")

            self.assertTrue(installed)
            self.assertEqual(run.call_args.args[0][0], str(venv_python))


if __name__ == "__main__":
    unittest.main()
