import builtins
import contextlib
import importlib.util
import json
import os
import sys
import tempfile
import types
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from antigravity_auth.storage import (
    get_hermes_home,
    get_auth_json_path,
    get_accounts_json_path,
    load_accounts,
    save_accounts,
    update_accounts,
    sync_token_to_auth_json,
    get_active_token_from_auth_json,
)


class TestStorage(unittest.TestCase):
    def _storage_with_lock(self, lock):
        from antigravity_auth import storage

        auth = types.ModuleType("hermes_cli.auth")
        setattr(auth, "_auth_store_lock", lock)
        spec = importlib.util.spec_from_file_location("antigravity_auth._lock_test_storage", storage.__file__)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {"hermes_cli.auth": auth}):
            spec.loader.exec_module(module)
        return module

    def test_available_process_lock_failure_preserves_auth_bytes(self):
        import fcntl

        storage = self._storage_with_lock(threading.RLock())
        path = get_auth_json_path()
        for original in (None, b'{"providers":{"other":{"keep":true}}}'):
            with self.subTest(existing=original is not None):
                if original is not None:
                    path.write_bytes(original)
                with patch.object(fcntl, "flock", side_effect=OSError("available lock denied")):
                    with self.assertRaisesRegex(OSError, "available lock denied"):
                        storage.sync_token_to_auth_json("synthetic-access", "synthetic-refresh")
                self.assertEqual(path.read_bytes() if path.exists() else None, original)

    def test_hermes_factory_owns_same_path_lock_during_recursive_concurrent_writes(self):
        import fcntl
        from concurrent.futures import ThreadPoolExecutor

        path = get_auth_json_path()
        guard = threading.RLock()
        local = threading.local()
        entries, exits, writes = [], [], []

        @contextlib.contextmanager
        def factory(*, target_path):
            self.assertEqual(target_path, path)
            with guard:
                depth = getattr(local, "depth", 0)
                with open(target_path.with_suffix(".lock"), "a+b") as handle:
                    if depth == 0:
                        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    local.depth = depth + 1
                    entry = (threading.get_ident(), local.depth)
                    entries.append(entry)
                    try:
                        yield
                    finally:
                        exits.append(entry)
                        local.depth = depth
                        if depth == 0:
                            fcntl.flock(handle, fcntl.LOCK_UN)

        storage = self._storage_with_lock(factory)
        original_replace = os.replace

        def replace(src, dst):
            self.assertGreater(getattr(local, "depth", 0), 0)
            with open(path.with_suffix(".lock"), "a+b") as contender:
                with self.assertRaises(BlockingIOError):
                    fcntl.flock(contender, fcntl.LOCK_EX | fcntl.LOCK_NB)
            storage.get_active_token_from_auth_json()
            writes.append(os.stat(src).st_mode & 0o777)
            original_replace(src, dst)

        with patch.object(storage.os, "replace", side_effect=replace):
            with ThreadPoolExecutor(max_workers=4) as pool:
                list(pool.map(lambda i: storage.sync_token_to_auth_json(f"fixture-{i}", "synthetic-refresh"), range(4)))
        self.assertEqual(writes, [0o600] * 4)
        self.assertEqual(sorted(entries), sorted(exits))
        self.assertEqual(len(entries), 7)  # First write has no previous auth file to read.
        self.assertEqual(storage.get_active_token_from_auth_json()["refresh_token"], "synthetic-refresh")

    def test_available_hermes_factory_failure_prevents_write(self):
        def factory(*, target_path):
            raise RuntimeError("available factory denied")

        storage = self._storage_with_lock(factory)
        with self.assertRaisesRegex(RuntimeError, "available factory denied"):
            storage.sync_token_to_auth_json("synthetic-access", "synthetic-refresh")
        self.assertFalse(get_auth_json_path().exists())

    def test_direct_hermes_manager_exits_after_write_error(self):
        events = []

        class Manager:
            def __enter__(self):
                events.append("enter")

            def __exit__(self, kind, value, traceback):
                events.append(value)

        storage = self._storage_with_lock(Manager())
        failure = OSError("replace denied")
        with patch.object(storage.os, "replace", side_effect=failure):
            with self.assertRaises(OSError) as caught:
                storage.sync_token_to_auth_json("synthetic-access", "synthetic-refresh")
        self.assertIs(caught.exception, failure)
        self.assertEqual(events, ["enter", failure])
        self.assertFalse(get_auth_json_path().exists())

    def test_available_windows_process_lock_failure_prevents_write(self):
        storage = self._storage_with_lock(threading.RLock())
        original_import = builtins.__import__

        def without_posix(name, *args, **kwargs):
            if name == "fcntl":
                raise ImportError("POSIX unavailable")
            return original_import(name, *args, **kwargs)

        def fail_lock(*args):
            raise OSError("Windows lock denied")

        windows = types.SimpleNamespace(LK_LOCK=1, LK_UNLCK=2, locking=fail_lock)
        with patch.dict(sys.modules, {"msvcrt": windows}), patch("builtins.__import__", side_effect=without_posix):
            with self.assertRaisesRegex(RuntimeError, "Windows lock denied"):
                storage.sync_token_to_auth_json("synthetic-access", "synthetic-refresh")
        self.assertFalse(get_auth_json_path().exists())

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

    def test_get_hermes_home_creates_dir(self):
        home_path = get_hermes_home()
        self.assertTrue(home_path.exists())
        self.assertTrue(home_path.is_dir())
        self.assertEqual(home_path, Path(self.temp_dir.name).resolve())

    def test_get_hermes_home_repairs_private_permissions(self):
        import stat

        old_umask = os.umask(0)
        try:
            home_path = get_hermes_home()
        finally:
            os.umask(old_umask)

        self.assertEqual(stat.S_IMODE(os.stat(home_path).st_mode), 0o700)

    def test_paths(self):
        auth_path = get_auth_json_path()
        accounts_path = get_accounts_json_path()
        self.assertEqual(auth_path.name, "auth.json")
        self.assertEqual(accounts_path.name, "antigravity-accounts.json")
        self.assertEqual(auth_path.parent, get_hermes_home())

    def test_load_accounts_default(self):
        data = load_accounts()
        self.assertEqual(data["version"], 4)
        self.assertEqual(data["accounts"], [])
        self.assertEqual(data["activeIndex"], 0)
        self.assertEqual(data["activeIndexByFamily"], {"claude": 0, "gemini": 0})

    def test_save_and_load_accounts(self):
        test_data = {
            "version": 4,
            "accounts": [
                {
                    "email": "test@example.com",
                    "refreshToken": "refresh_123",
                    "projectId": "project_abc",
                }
            ],
            "activeIndex": 0,
            "activeIndexByFamily": {"claude": 0, "gemini": 0},
        }
        save_accounts(test_data)
        loaded = load_accounts()
        self.assertEqual(loaded["version"], 4)
        self.assertEqual(len(loaded["accounts"]), 1)
        self.assertEqual(loaded["accounts"][0]["email"], "test@example.com")
        self.assertEqual(loaded["accounts"][0]["refreshToken"], "refresh_123")

    def test_process_lock_creates_private_lock_file_and_can_reacquire(self):
        import os
        import stat
        import tempfile
        from pathlib import Path
        from antigravity_auth.storage import _process_file_lock

        with tempfile.TemporaryDirectory() as tmp:
            lock_path = Path(tmp) / "store.lock"
            with _process_file_lock(lock_path):
                self.assertTrue(lock_path.exists())
                self.assertEqual(stat.S_IMODE(os.stat(lock_path).st_mode), 0o600)
            with _process_file_lock(lock_path):
                self.assertTrue(lock_path.exists())
                self.assertEqual(stat.S_IMODE(os.stat(lock_path).st_mode), 0o600)

    def test_process_lock_uses_msvcrt_when_fcntl_is_unavailable(self):
        from antigravity_auth import storage

        calls = []
        fake_msvcrt = types.SimpleNamespace(
            LK_LOCK=7,
            LK_UNLCK=8,
            locking=lambda fd, mode, nbytes: calls.append((mode, nbytes)),
        )
        original_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "fcntl":
                raise ImportError("no fcntl in this test")
            return original_import(name, *args, **kwargs)

        with tempfile.TemporaryDirectory() as tmp:
            lock_path = Path(tmp) / "store.lock"
            with patch.dict(sys.modules, {"msvcrt": fake_msvcrt}):
                with patch("builtins.__import__", side_effect=fake_import):
                    with storage._process_file_lock(lock_path):
                        self.assertTrue(lock_path.exists())

        self.assertEqual(calls, [(fake_msvcrt.LK_LOCK, 1), (fake_msvcrt.LK_UNLCK, 1)])

    def test_process_lock_opens_lock_file_in_binary_mode_for_msvcrt(self):
        from antigravity_auth import storage

        modes = []
        fake_msvcrt = types.SimpleNamespace(
            LK_LOCK=7,
            LK_UNLCK=8,
            locking=lambda fd, mode, nbytes: None,
        )
        original_import = builtins.__import__
        original_open = builtins.open

        def fake_import(name, *args, **kwargs):
            if name == "fcntl":
                raise ImportError("no fcntl in this test")
            return original_import(name, *args, **kwargs)

        with tempfile.TemporaryDirectory() as tmp:
            lock_path = Path(tmp) / "store.lock"

            def tracking_open(file, mode="r", *args, **kwargs):
                if Path(file) == lock_path:
                    modes.append(mode)
                return original_open(file, mode, *args, **kwargs)

            with patch.dict(sys.modules, {"msvcrt": fake_msvcrt}):
                with patch("builtins.__import__", side_effect=fake_import):
                    with patch("builtins.open", side_effect=tracking_open):
                        with storage._process_file_lock(lock_path):
                            pass

        self.assertTrue(modes)
        self.assertIn("b", modes[0])

    def test_probe_process_file_lock_acquires_and_releases_available_backend(self):
        from antigravity_auth import storage

        calls = []
        fake_msvcrt = types.SimpleNamespace(
            LK_LOCK=7,
            LK_UNLCK=8,
            locking=lambda fd, mode, nbytes: calls.append((mode, nbytes)),
        )
        original_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "fcntl":
                raise ImportError("no fcntl in this test")
            return original_import(name, *args, **kwargs)

        with tempfile.TemporaryDirectory() as tmp:
            lock_path = Path(tmp) / "probe.lock"
            with patch.dict(sys.modules, {"msvcrt": fake_msvcrt}):
                with patch("builtins.__import__", side_effect=fake_import):
                    backend, detail = storage._probe_process_file_lock(lock_path)

        self.assertEqual(backend, "msvcrt")
        self.assertIn("acquired and released", detail)
        self.assertEqual(calls, [(fake_msvcrt.LK_LOCK, 1), (fake_msvcrt.LK_UNLCK, 1)])

    def test_process_lock_warns_once_when_no_process_backend_is_available(self):
        from antigravity_auth import storage

        storage._process_lock_warning_emitted = False
        original_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name in ("fcntl", "msvcrt"):
                raise ImportError(f"no {name} in this test")
            return original_import(name, *args, **kwargs)

        with tempfile.TemporaryDirectory() as tmp:
            lock_path = Path(tmp) / "store.lock"
            with patch("builtins.__import__", side_effect=fake_import):
                with self.assertLogs("antigravity_auth.storage", level="WARNING") as captured:
                    with storage._process_file_lock(lock_path):
                        pass
                    with storage._process_file_lock(lock_path):
                        pass

        warnings = [line for line in captured.output if "Inter-process file locking is unavailable" in line]
        self.assertEqual(len(warnings), 1)
        self.assertIn("account-store transactions are only thread-safe inside this process", warnings[0])

    def test_save_accounts_creates_private_process_lock_file(self):
        import stat

        save_accounts({"version": 4, "accounts": []})
        lock_path = get_accounts_json_path().with_suffix(".lock")
        self.assertTrue(lock_path.exists())
        self.assertEqual(stat.S_IMODE(os.stat(lock_path).st_mode), 0o600)

        save_accounts({"version": 4, "accounts": [], "activeIndex": 0})
        self.assertTrue(lock_path.exists())
        self.assertEqual(stat.S_IMODE(os.stat(lock_path).st_mode), 0o600)

    def test_save_accounts_temp_file_is_private_before_replace(self):
        observed_modes = []
        original_replace = os.replace

        def inspect_tmp_before_replace(src, dst):
            observed_modes.append(os.stat(src).st_mode & 0o777)
            return original_replace(src, dst)

        old_umask = os.umask(0)
        try:
            with patch("antigravity_auth.storage.os.replace", side_effect=inspect_tmp_before_replace):
                save_accounts({"version": 4, "accounts": []})
        finally:
            os.umask(old_umask)

        self.assertEqual(observed_modes, [0o600])

    def test_update_accounts_prevents_lost_updates_across_threads(self):
        import threading

        save_accounts({"version": 4, "accounts": [], "activeIndex": 0, "cursor": 0})
        start = threading.Barrier(12)

        def worker(idx):
            start.wait()

            def mutator(data):
                accounts = data.setdefault("accounts", [])
                accounts.append({
                    "email": f"user-{idx}@example.com",
                    "refreshToken": f"refresh-{idx}",
                    "projectId": f"project-{idx}",
                })

            update_accounts(mutator)

        threads = [threading.Thread(target=worker, args=(idx,)) for idx in range(12)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)

        loaded = load_accounts()
        self.assertEqual(len(loaded["accounts"]), 12)
        self.assertEqual(
            {account["email"] for account in loaded["accounts"]},
            {f"user-{idx}@example.com" for idx in range(12)},
        )
        self.assertEqual(loaded["activeIndex"], 0)
        self.assertEqual(loaded["cursor"], 0)

    def test_sync_token_to_auth_json_new_and_existing(self):
        sync_token_to_auth_json(
            access_token="acc_111",
            refresh_token="ref_222",
            project_id="proj_333",
            email="user@example.com",
            set_active=True,
        )

        active = get_active_token_from_auth_json()
        self.assertEqual(active["access_token"], "acc_111")
        self.assertEqual(active["refresh_token"], "ref_222")
        self.assertEqual(active["project_id"], "proj_333")

        sync_token_to_auth_json(
            access_token="acc_updated",
            refresh_token="ref_updated",
            project_id="proj_updated",
            email="user@example.com",
            set_active=False,
        )

        active_updated = get_active_token_from_auth_json()
        self.assertEqual(active_updated["access_token"], "acc_updated")
        self.assertEqual(active_updated["refresh_token"], "ref_updated")
        self.assertEqual(active_updated["project_id"], "proj_updated")

    def test_sync_token_to_auth_json_creates_private_lock_and_preserves_unrelated_providers(self):
        import stat

        auth_path = get_auth_json_path()
        auth_path.parent.mkdir(parents=True, exist_ok=True)
        with open(auth_path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "providers": {
                        "other": {
                            "tokens": {"access_token": "keep_access"},
                            "project_id": "keep_project",
                        }
                    },
                    "active_provider": "other",
                },
                f,
            )

        sync_token_to_auth_json(
            access_token="acc_111",
            refresh_token="ref_222",
            project_id="proj_333",
            email="user@example.com",
            set_active=False,
        )

        lock_path = auth_path.with_suffix(".lock")
        self.assertTrue(lock_path.exists())
        self.assertEqual(stat.S_IMODE(os.stat(lock_path).st_mode), 0o600)
        with open(auth_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["active_provider"], "other")
        self.assertEqual(data["providers"]["other"]["project_id"], "keep_project")
        self.assertEqual(data["providers"]["antigravity"]["project_id"], "proj_333")
        self.assertEqual(
            data["providers"]["google-gemini-cli"],
            data["providers"]["antigravity"],
        )

    def test_sync_token_to_auth_json_sets_canonical_runtime_active_provider(self):
        sync_token_to_auth_json(
            access_token="acc_111",
            refresh_token="ref_222|proj_333",
            project_id="proj_333",
            email="user@example.com",
            set_active=True,
        )

        with open(get_auth_json_path(), "r", encoding="utf-8") as f:
            data = json.load(f)

        self.assertEqual(data["active_provider"], "google-gemini-cli")
        self.assertIn("antigravity", data["providers"])
        self.assertIn("google-gemini-cli", data["providers"])
        self.assertEqual(
            data["providers"]["google-gemini-cli"],
            data["providers"]["antigravity"],
        )

    def test_sync_token_to_auth_json_temp_file_is_private_before_replace(self):
        observed_modes = []
        original_replace = os.replace

        def inspect_tmp_before_replace(src, dst):
            observed_modes.append(os.stat(src).st_mode & 0o777)
            return original_replace(src, dst)

        old_umask = os.umask(0)
        try:
            with patch("antigravity_auth.storage.os.replace", side_effect=inspect_tmp_before_replace):
                sync_token_to_auth_json(
                    access_token="secret_access",
                    refresh_token="secret_refresh|secret_project",
                    project_id="secret_project",
                    email="user@example.com",
                )
        finally:
            os.umask(old_umask)

        self.assertEqual(observed_modes, [0o600])

    def test_sync_token_to_auth_json_clears_antigravity_active_provider_when_tokens_empty(self):
        sync_token_to_auth_json(
            access_token="acc_111",
            refresh_token="ref_222|proj_333",
            project_id="proj_333",
            email="user@example.com",
            set_active=True,
        )

        sync_token_to_auth_json(
            access_token="",
            refresh_token="",
            project_id="",
            email=None,
            set_active=False,
        )
        with open(get_auth_json_path(), "r", encoding="utf-8") as f:
            data = json.load(f)

        self.assertEqual(data["active_provider"], "")
        self.assertEqual(data["providers"]["antigravity"]["tokens"]["access_token"], "")
        self.assertEqual(data["providers"]["antigravity"]["tokens"]["refresh_token"], "")
        self.assertEqual(
            data["providers"]["google-gemini-cli"],
            data["providers"]["antigravity"],
        )

    def test_sync_token_to_all_auth_stores_reports_google_oauth_failure_independently(self):
        from antigravity_auth.auth_sync import sync_token_to_all_auth_stores

        with patch("antigravity_auth.auth_sync.sync_token_to_auth_json") as auth_json_sync, \
             patch("antigravity_auth.auth_sync.sync_token_to_google_oauth", return_value=False) as google_sync:
            result = sync_token_to_all_auth_stores(
                access_token="acc_111",
                refresh_token="ref_222|proj_333",
                project_id="proj_333",
                email="user@example.com",
                expires_ms=123,
                set_active=True,
            )

        self.assertEqual(result.auth_json, True)
        self.assertEqual(result.google_oauth, False)
        self.assertEqual(result.ok, False)
        self.assertEqual(bool(result), False)
        auth_json_sync.assert_called_once()
        google_sync.assert_called_once()

    def test_sync_token_to_all_auth_stores_skips_google_oauth_when_auth_json_fails(self):
        from antigravity_auth.auth_sync import sync_token_to_all_auth_stores, sync_token_to_all_auth_stores_bool

        with patch("antigravity_auth.auth_sync.sync_token_to_auth_json", side_effect=RuntimeError("boom")), \
             patch("antigravity_auth.auth_sync.sync_token_to_google_oauth", return_value=True) as google_sync:
            result = sync_token_to_all_auth_stores(
                access_token="acc_111",
                refresh_token="ref_222|proj_333",
                project_id="proj_333",
                email="user@example.com",
            )

        self.assertEqual(result.auth_json, False)
        self.assertEqual(result.google_oauth, False)
        self.assertEqual(result.ok, False)
        self.assertEqual(bool(result), False)
        google_sync.assert_not_called()

        with patch("antigravity_auth.auth_sync.sync_token_to_auth_json", side_effect=RuntimeError("boom")), \
             patch("antigravity_auth.auth_sync.sync_token_to_google_oauth", return_value=True) as google_sync:
            self.assertEqual(
                sync_token_to_all_auth_stores_bool(
                    access_token="acc_111",
                    refresh_token="ref_222|proj_333",
                    project_id="proj_333",
                    email="user@example.com",
                ),
                False,
            )
        google_sync.assert_not_called()


if __name__ == "__main__":
    unittest.main()
