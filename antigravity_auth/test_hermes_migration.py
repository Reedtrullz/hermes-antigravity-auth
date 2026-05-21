import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch


class TestHermesMigrationIntegration(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
