"""Regression test: storage.py must not eagerly import hermes_cli.auth on module load.

When Hermes 0.21 scans plugins via providers._discover_providers(), importing
antigravity-cli entrypoint must not trigger premature hermes_cli.auth loading,
which would freeze PROVIDER_REGISTRY before bundled/user plugins (like commandcode)
are discovered.
"""

import sys
import unittest


class TestProviderDiscoveryTiming(unittest.TestCase):
    def test_storage_import_does_not_eagerly_import_hermes_auth(self):
        """Importing antigravity_auth.storage must not import hermes_cli.auth."""
        # Unload storage and hermes_cli.auth to simulate a cold import
        sys.modules.pop("hermes_cli.auth", None)
        sys.modules.pop("antigravity_auth.storage", None)

        import antigravity_auth.storage as storage_mod

        self.assertNotIn(
            "hermes_cli.auth",
            sys.modules,
            "antigravity_auth.storage must NOT import hermes_cli.auth at module load time",
        )

        # Context manager interface should be functional
        self.assertTrue(
            hasattr(storage_mod._auth_store_lock, "__enter__"),
            "_auth_store_lock must provide a context manager interface",
        )
        with storage_mod._auth_store_lock:
            pass


if __name__ == "__main__":
    unittest.main()
