# Antigravity Plugin Remediation Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Fix the audited Hermes Antigravity plugin gaps so provider routing, credential state, interceptor transforms, multi-account rotation, and documentation all match the actual runtime behavior.

**Architecture:** Preserve the runtime heartbeat: `hermes_plugin.register()` installs the interceptor, the interceptor patches Hermes `GeminiCloudCodeClient.__init__` and `wrap_code_assist_request()`, and the httpx request hook remains headers-only except for Authorization/header selection. Body-level Claude transforms must happen only in the `wrap_code_assist_request()` patch, before serialization. Credential switching must update all three state surfaces together: `antigravity-accounts.json`, `auth.json`, and Hermes `auth/google_oauth.json`.

**Tech Stack:** Python 3.10+, stdlib-first package, pytest/unittest tests colocated under `antigravity_auth/`, Hermes Agent v0.14 Cloud Code runtime, httpx event hooks, Google OAuth credential stores.

---

## Pre-read and architecture audit

Already read before writing this plan:
- `AGENTS.md`
- `docs/ARCHITECTURE.md`
- `docs/ANTIGRAVITY_API_SPEC.md`
- `README.md`
- `antigravity_auth/interceptor.py`
- `antigravity_auth/hermes_plugin.py`
- `antigravity_auth/hermes_provider_plugin.py`
- `antigravity_auth/storage.py`
- `antigravity_auth/token.py`
- `antigravity_auth/cli.py`
- `antigravity_auth/accounts/manager.py`
- Hermes skill refs:
  - `writing-plans/references/interceptor-dependency-chain-analysis.md`
  - `hermes-plugin-development/references/claude-antigravity-transforms.md`
  - `hermes-plugin-development/references/claude-tool-call-id-injection.md`
  - `hermes-plugin-development/references/antigravity-model-updates.md`

Runtime heartbeat dependency chains:

```text
Plugin load chain:
plugins/antigravity_tools/__init__.py
  -> antigravity_auth.hermes_plugin.register(ctx)
     -> antigravity_auth.interceptor.install()
        -> agent.gemini_cloudcode_adapter.GeminiCloudCodeClient.__init__ monkey patch
        -> agent.gemini_cloudcode_adapter.wrap_code_assist_request monkey patch
     -> antigravity_auth.accounts.shared.get_or_create_global_manager()
     -> antigravity_auth.tools.register_tools()
     -> antigravity_auth.token_watchdog.start_watchdog()

Provider side-effect chain:
plugins/model-providers/antigravity/__init__.py
  -> from antigravity_auth.hermes_provider_plugin import *
     -> providers.register_provider(antigravity)
     -> hermes_cli.models picker aliases/labels
     -> hermes_cli.auth.PROVIDER_REGISTRY aliases

Rate-limit rotation chain, silently swallowed if broken:
antigravity_auth.interceptor._antigravity_response_hook()
  -> antigravity_auth.accounts.manager.get_or_create_global_manager()
     -> antigravity_auth.accounts.ratelimit
     -> antigravity_auth.accounts.rotation
     -> antigravity_auth.accounts.state
  -> antigravity_auth.accounts.ratelimit.mark_rate_limited()
```

Architecture invariants from docs and skills:
- Do not mutate serialized httpx request bodies in request hooks; h11/content-length makes this unsafe.
- Request-hook responsibilities: select account, set `Authorization`, preserve critical headers, inject Antigravity headers/fingerprint.
- Claude body transforms are allowed only in the `wrap_code_assist_request()` patch, before serialization.
- Preserve critical headers: `Authorization`, `Content-Type`, `Host`, `Accept`, `Accept-Encoding`, `Content-Length`.
- Wildcard import in `plugins/model-providers/antigravity/__init__.py` is intentional and must not be refactored away.
- Any task touching `interceptor.py`, `accounts/manager.py`, `accounts/ratelimit.py`, or `accounts/rotation.py` needs manual import-chain verification because failures can be swallowed and disable rotation silently.

Global verification commands after every code task touching runtime chains:

```bash
python3 -m pytest antigravity_auth/ -q
python3 -m compileall -q antigravity_auth plugins
python3 -c "from antigravity_auth.interceptor import install, _antigravity_request_hook; print('INTERCEPTOR_OK')"
python3 -c "from antigravity_auth.accounts.manager import AccountManager; print('MANAGER_OK')"
python3 -c "from antigravity_auth.accounts.ratelimit import mark_rate_limited; print('RATELIMIT_OK')"
python3 -c "from antigravity_auth.hermes_plugin import register; print('PLUGIN_OK')"
```

Hermes runtime smoke command for provider routing tasks:

```bash
PYTHONPATH=/Users/reidar/Projectos/hermes-antigravity-auth:/Users/reidar/.hermes/hermes-agent \
/Users/reidar/.hermes/hermes-agent/venv/bin/python - <<'PY'
from hermes_cli.runtime_provider import resolve_runtime_provider
for requested in ("google-gemini-cli", "antigravity", "ag"):
  rt = resolve_runtime_provider(requested=requested, target_model="claude-sonnet-4-6-thinking")
  assert rt["provider"] == "google-gemini-cli", (requested, rt)
  assert rt["base_url"] == "cloudcode-pa://google", (requested, rt)
print("RUNTIME_PROVIDER_OK")
PY
```

Expected output: `RUNTIME_PROVIDER_OK`.

---

## Phase 0: Baseline safety

### Task 0: Capture clean baseline

**Objective:** Confirm the repository starts from a clean, passing state before remediation.

**Files:** none.

**Step 1: Run baseline tests**

Run:
```bash
git status --short
python3 -m pytest antigravity_auth/ -q
python3 -m compileall -q antigravity_auth plugins
```

Expected:
- `git status --short` prints nothing except this plan file if it has not been committed yet.
- pytest passes.
- compileall prints nothing and exits 0.

**Step 2: Run runtime smoke**

Run the Hermes runtime smoke command from the global verification section.

Expected: `RUNTIME_PROVIDER_OK`.

**Step 3: Commit the plan first**

```bash
git add docs/plans/2026-05-27-antigravity-plugin-remediation.md
git commit -m "docs: add antigravity plugin remediation plan"
```

---

## Phase 1: Provider and credential state correctness

### Task 1: Make `auth.json.active_provider` canonical

**Objective:** Store `google-gemini-cli` as the active runtime provider while retaining the `antigravity` auth key for branding/backward compatibility.

**Risk:** LOW. Touches credential storage only. Run provider smoke after change.

**Files:**
- Modify: `antigravity_auth/storage.py:126-185`
- Test: `antigravity_auth/test_storage.py`

**Step 1: Write failing test**

Append to `TestStorage` in `antigravity_auth/test_storage.py`:

```python
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
```

Also add `import json` at the top if missing.

**Step 2: Run test to verify failure**

Run:
```bash
python3 -m pytest antigravity_auth/test_storage.py::TestStorage::test_sync_token_to_auth_json_sets_canonical_runtime_active_provider -q
```

Expected: FAIL — active provider is currently `antigravity`.

**Step 3: Implement minimal fix**

In `antigravity_auth/storage.py`, change:

```python
        if set_active:
            data["active_provider"] = "antigravity"
```

to:

```python
        if set_active:
            data["active_provider"] = "google-gemini-cli"
```

**Step 4: Verify**

Run:
```bash
python3 -m pytest antigravity_auth/test_storage.py -q
python3 -m pytest antigravity_auth/ -q
```

Expected: PASS.

**Step 5: Runtime smoke**

Run the Hermes runtime smoke command.

Expected: `RUNTIME_PROVIDER_OK`.

**Step 6: Commit**

```bash
git add antigravity_auth/storage.py antigravity_auth/test_storage.py
git commit -m "fix: use canonical gemini cli active provider"
```

---

### Task 2: Bridge documented Antigravity env vars into Hermes OAuth env vars

**Objective:** Make `ANTIGRAVITY_CLIENT_ID` / `ANTIGRAVITY_CLIENT_SECRET` work for both package OAuth and Hermes `agent.google_oauth` refresh.

**Risk:** MODERATE. Provider plugin is side-effect-driven and import failures are easy to hide.

**Files:**
- Modify: `antigravity_auth/hermes_provider_plugin.py:15-27`
- Test: `antigravity_auth/test_hermes_migration.py`

**Step 1: Write failing test**

Append to `TestHermesMigrationIntegration`:

```python
    def test_provider_plugin_bridges_antigravity_env_credentials(self):
        import importlib

        captured = []

        class FakeProviderProfile:
            def __init__(self, **kwargs):
                self.__dict__.update(kwargs)

        fake_providers = types.ModuleType("providers")
        fake_providers.register_provider = lambda profile: captured.append(profile)
        fake_base = types.ModuleType("providers.base")
        fake_base.ProviderProfile = FakeProviderProfile

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
```

**Step 2: Run test to verify failure**

```bash
python3 -m pytest antigravity_auth/test_hermes_migration.py::TestHermesMigrationIntegration::test_provider_plugin_bridges_antigravity_env_credentials -q
```

Expected: FAIL — `HERMES_GEMINI_*` remain unset.

**Step 3: Implement minimal fix**

In `_set_oauth_env_from_credentials()`, prefer explicit Hermes env vars, then documented Antigravity env vars, then `_credentials.py`:

```python
def _set_oauth_env_from_credentials() -> None:
  if os.getenv("HERMES_GEMINI_CLIENT_ID") and os.getenv("HERMES_GEMINI_CLIENT_SECRET"):
    return

  client_id = os.getenv("ANTIGRAVITY_CLIENT_ID", "").strip()
  client_secret = os.getenv("ANTIGRAVITY_CLIENT_SECRET", "").strip()

  if not client_id or not client_secret:
    try:
      from ._credentials import ANTIGRAVITY_CLIENT_ID, ANTIGRAVITY_CLIENT_SECRET
      client_id = client_id or ANTIGRAVITY_CLIENT_ID
      client_secret = client_secret or ANTIGRAVITY_CLIENT_SECRET
    except ImportError:
      pass

  if client_id:
    os.environ.setdefault("HERMES_GEMINI_CLIENT_ID", client_id)
  if client_secret:
    os.environ.setdefault("HERMES_GEMINI_CLIENT_SECRET", client_secret)
```

**Step 4: Verify import side effects still work**

Run:
```bash
python3 -m pytest antigravity_auth/test_hermes_migration.py -q
python3 -m compileall -q antigravity_auth plugins
```

Expected: PASS.

**Step 5: Runtime smoke**

Run the Hermes runtime smoke command.

Expected: `RUNTIME_PROVIDER_OK`.

**Step 6: Commit**

```bash
git add antigravity_auth/hermes_provider_plugin.py antigravity_auth/test_hermes_migration.py
git commit -m "fix: bridge antigravity oauth env vars"
```

---

### Task 3: Make plugin load failures visible and initialize debug logging

**Objective:** Ensure interceptor/tool/watchdog load failures log visibly instead of silently disabling plugin behavior; initialize debug logs from config.

**Risk:** HIGH. Touches plugin heartbeat entrypoint.

**Files:**
- Modify: `antigravity_auth/hermes_plugin.py:8-76`
- Test: `antigravity_auth/test_hermes_migration.py` or new `antigravity_auth/test_hermes_plugin.py`

**Step 1: Write failing tests**

Create `antigravity_auth/test_hermes_plugin.py`:

```python
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


class TestHermesPluginRegister(unittest.TestCase):
    def test_register_initializes_debug_logging(self):
        from antigravity_auth import hermes_plugin

        with patch("antigravity_auth.hermes_plugin.initialize_debug") as init_debug:
            hermes_plugin.register(FakeCtx())

        init_debug.assert_called_once()

    def test_register_logs_interceptor_install_failure(self):
        from antigravity_auth import hermes_plugin

        with patch("antigravity_auth.interceptor.install", side_effect=RuntimeError("boom")):
            with self.assertLogs("antigravity_auth.hermes_plugin", level="WARNING") as logs:
                hermes_plugin.register(FakeCtx())

        self.assertTrue(any("interceptor" in line.lower() and "boom" in line for line in logs.output))
```

**Step 2: Run tests to verify failure**

```bash
python3 -m pytest antigravity_auth/test_hermes_plugin.py -q
```

Expected: FAIL — `initialize_debug` is not imported/called and failures are swallowed.

**Step 3: Implement minimal fix**

At top of `antigravity_auth/hermes_plugin.py` add:

```python
import logging

from .config import get_config
from .debug import initialize_debug
```

Inside `register(ctx)`, after CLI registration:

```python
  logger = logging.getLogger(__name__)
  config = get_config()
  initialize_debug(config.debug, config.debug_tui, config.log_dir)
```

Replace each silent `except Exception: pass` with a warning that names the subsystem. Example for the interceptor:

```python
  try:
    from .interceptor import install as install_interceptor
    installed = install_interceptor()
    logger.info("Antigravity interceptor install result: %s", installed)
  except Exception as e:
    logger.warning("Antigravity interceptor install failed: %s", e)
```

For optional tools/watchdog/version check, keep behavior non-fatal but log at warning/debug.

**Step 4: Verify heartbeat import chain**

Run:
```bash
python3 -m pytest antigravity_auth/test_hermes_plugin.py -q
python3 -m pytest antigravity_auth/ -q
python3 -c "from antigravity_auth.hermes_plugin import register; print('PLUGIN_OK')"
python3 -c "from antigravity_auth.interceptor import install; print('INTERCEPTOR_OK')"
```

Expected: PASS and printed `PLUGIN_OK`, `INTERCEPTOR_OK`.

**Step 5: Commit**

```bash
git add antigravity_auth/hermes_plugin.py antigravity_auth/test_hermes_plugin.py
git commit -m "fix: surface plugin load failures"
```

---

### Task 4: Create a dedicated auth-sync module for Google OAuth store writes

**Objective:** Remove runtime credential write logic from `cli.py` so interceptor, watchdog, CLI, and verification can share one sync path without import cycles.

**Risk:** MODERATE. Touches auth-store plumbing but not account selection yet.

**Files:**
- Create: `antigravity_auth/auth_sync.py`
- Modify: `antigravity_auth/cli.py:138-169`
- Test: `antigravity_auth/test_hermes_migration.py`

**Step 1: Write failing import test**

Append to `TestHermesMigrationIntegration`:

```python
    def test_auth_sync_exports_google_oauth_sync(self):
        from antigravity_auth.auth_sync import sync_token_to_google_oauth
        self.assertTrue(callable(sync_token_to_google_oauth))
```

**Step 2: Run test to verify failure**

```bash
python3 -m pytest antigravity_auth/test_hermes_migration.py::TestHermesMigrationIntegration::test_auth_sync_exports_google_oauth_sync -q
```

Expected: FAIL — module does not exist.

**Step 3: Create `antigravity_auth/auth_sync.py`**

Copy the current `sync_token_to_google_oauth()` implementation from `cli.py`, with these imports:

```python
"""Helpers for syncing Antigravity credentials into Hermes runtime stores."""

from __future__ import annotations

import time

from .storage import sync_token_to_auth_json
from .token import parse_refresh_parts


def sync_token_to_google_oauth(
    access_token: str,
    refresh_token: str,
    project_id: str = "",
    email: str | None = None,
    expires_ms: int | None = None,
) -> bool:
    """Write credentials to Hermes' native auth/google_oauth.json store."""
    try:
        from agent.google_oauth import GoogleCredentials, save_credentials
    except Exception:
        return False

    parts = parse_refresh_parts(refresh_token)
    resolved_project_id = project_id or parts.get("projectId") or ""
    resolved_expires_ms = expires_ms or int(time.time() * 1000) + 3600 * 1000

    credentials = GoogleCredentials(
        access_token=access_token,
        refresh_token=parts.get("refreshToken", ""),
        expires_ms=resolved_expires_ms,
        email=email or "",
        project_id=resolved_project_id,
        managed_project_id=parts.get("managedProjectId") or "",
    )
    save_credentials(credentials)
    return True


def sync_token_to_all_auth_stores(
    access_token: str,
    refresh_token: str,
    project_id: str = "",
    email: str | None = None,
    expires_ms: int | None = None,
    set_active: bool = True,
) -> bool:
    """Sync active credentials to auth.json and google_oauth.json together."""
    sync_token_to_auth_json(
        access_token=access_token,
        refresh_token=refresh_token,
        project_id=project_id,
        email=email,
        set_active=set_active,
    )
    return sync_token_to_google_oauth(
        access_token=access_token,
        refresh_token=refresh_token,
        project_id=project_id,
        email=email,
        expires_ms=expires_ms,
    )
```

**Step 4: Preserve backward import from CLI**

In `antigravity_auth/cli.py`, remove the existing local `def sync_token_to_google_oauth(...)` function and place this module-level import near the other imports. Do **not** put an import inside the old function body; the function definition should be gone so all callers use the same shared function object:

```python
from .auth_sync import sync_token_to_google_oauth, sync_token_to_all_auth_stores
```

Do not break existing callers importing `sync_token_to_google_oauth` from `cli.py`; keeping the imported name in module scope preserves that compatibility.

**Step 5: Verify**

```bash
python3 -m pytest antigravity_auth/test_hermes_migration.py -q
python3 -m pytest antigravity_auth/test_cli.py -q
python3 -m pytest antigravity_auth/ -q
python3 -c "from antigravity_auth.auth_sync import sync_token_to_all_auth_stores; print('AUTH_SYNC_OK')"
```

Expected: PASS and `AUTH_SYNC_OK`.

**Step 6: Commit**

```bash
git add antigravity_auth/auth_sync.py antigravity_auth/cli.py antigravity_auth/test_hermes_migration.py
git commit -m "refactor: centralize runtime auth sync"
```

---

### Task 5: Make token refresh pure by default

**Objective:** `refresh_access_token()` should return refreshed credentials without mutating global auth stores unless explicitly requested.

**Risk:** HIGH. Many callers rely on current side effects. Implement this before updating callers, then fix all call sites immediately in Tasks 6-8.

**Files:**
- Modify: `antigravity_auth/token.py:103-250`
- Test: `antigravity_auth/test_token.py`

**Step 1: Write failing test**

Add to `TestToken`:

```python
    @patch("urllib.request.urlopen")
    def test_refresh_access_token_does_not_set_active_provider_by_default(self, mock_urlopen):
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.read.return_value = json.dumps({
            "access_token": "new_access",
            "expires_in": 3600,
            "refresh_token": "new_refresh",
        }).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_response

        sync_token_to_auth_json("old_access", "old_refresh|proj", "proj", "old@example.com")

        updated = refresh_access_token({
            "refresh": "old_refresh|proj",
            "access": "old_access",
            "expires": 0,
            "email": "user@example.com",
        })

        self.assertEqual(updated["access"], "new_access")
        self.assertEqual(updated["refresh"], "new_refresh|proj")
        active = get_active_token_from_auth_json()
        self.assertEqual(active["access_token"], "old_access")
        self.assertEqual(active["refresh_token"], "old_refresh|proj")
```

**Step 2: Run test to verify failure**

```bash
python3 -m pytest antigravity_auth/test_token.py::TestToken::test_refresh_access_token_does_not_set_active_provider_by_default -q
```

Expected: FAIL — current function writes `auth.json`.

**Step 3: Implement explicit persistence flag**

Change signature:

```python
def refresh_access_token(auth: dict, *, persist: bool = False, set_active: bool = False) -> dict:
```

Wrap the current `sync_token_to_auth_json(...)` block:

```python
    if persist:
        try:
            sync_token_to_auth_json(
                access_token=access_token,
                refresh_token=new_refresh_packed,
                project_id=project_id,
                email=email,
                set_active=set_active,
            )
        except Exception:
            pass
```

Keep updating `antigravity-accounts.json` with a rotated raw refresh token because that preserves the account record, not the active runtime provider.

**Step 4: Update existing token tests**

Tests that expect auth.json mutation should pass `persist=True, set_active=True` explicitly. For example:

```python
updated_auth = refresh_access_token(auth, persist=True, set_active=True)
```

**Step 5: Verify**

```bash
python3 -m pytest antigravity_auth/test_token.py -q
python3 -m pytest antigravity_auth/ -q
```

Expected: PASS.

**Step 6: Commit**

```bash
git add antigravity_auth/token.py antigravity_auth/test_token.py
git commit -m "fix: make token refresh side effects explicit"
```

---

### Task 6: Pack refresh tokens with project IDs in CLI quota checks and account switching

**Objective:** Prevent CLI refresh calls from losing `projectId` and stale refresh-token metadata.

**Risk:** MODERATE. Touches CLI account operations.

**Files:**
- Modify: `antigravity_auth/cli.py:330-463`
- Test: `antigravity_auth/test_cli.py`

**Step 1: Write failing tests**

Add tests that mock `refresh_access_token` and assert the packed refresh string includes project ID:

```python
    def test_check_quotas_refreshes_with_packed_project_id(self):
        # Use HERMES_HOME temp fixture pattern already used in this file.
        save_accounts({
            "version": 4,
            "accounts": [{
                "email": "user@example.com",
                "refreshToken": "raw-refresh",
                "projectId": "proj-1",
            }],
            "activeIndex": 0,
            "activeIndexByFamily": {"claude": 0, "gemini": 0},
        })
        calls = []
        def fake_refresh(auth, **kwargs):
            calls.append(auth["refresh"])
            return {"access": "access", "refresh": "rotated|proj-1", "expires": 123}

        with patch("antigravity_auth.token.refresh_access_token", side_effect=fake_refresh), \
             patch("antigravity_auth.accounts.quota.fetch_quota_from_api", return_value=[]), \
             patch("antigravity_auth.verification.verify_account_access"):
            check_quotas_and_verify()

        self.assertEqual(calls, ["raw-refresh|proj-1"])
```

Use the existing imports/patterns in `test_cli.py` rather than duplicating temp setup.

**Step 2: Run targeted test to verify failure**

```bash
python3 -m pytest antigravity_auth/test_cli.py -q
```

Expected: new test FAILS because CLI passes raw refresh.

**Step 3: Implement minimal fix**

In `check_quotas_and_verify()`, build packed refresh before refreshing:

```python
        packed_refresh = format_refresh_parts({
            "refreshToken": refresh_token,
            "projectId": project_id,
            "managedProjectId": acc.get("managedProjectId") or "",
        })
        refreshed = refresh_access_token({"refresh": packed_refresh, "email": email})
```

In account switching, after refresh returns a possibly rotated packed value, use that value for both stores:

```python
                                refreshed = refresh_access_token({"refresh": packed_refresh, "email": acc.get("email")})
                                access_token = refreshed.get("access", "")
                                packed_refresh = refreshed.get("refresh") or packed_refresh
                                expires_ms = refreshed.get("expires")
```

Then call `sync_token_to_all_auth_stores(...)` instead of separate store writes.

**Step 4: Verify**

```bash
python3 -m pytest antigravity_auth/test_cli.py -q
python3 -m pytest antigravity_auth/test_token.py -q
python3 -m pytest antigravity_auth/ -q
```

Expected: PASS.

**Step 5: Commit**

```bash
git add antigravity_auth/cli.py antigravity_auth/test_cli.py
git commit -m "fix: preserve project id in cli refreshes"
```

---

### Task 7: Preserve rotated refresh tokens in watchdog, verification, and interceptor callers

**Objective:** Ensure every refresh caller uses `refreshed["refresh"]` when syncing runtime stores and never writes stale raw refresh tokens back.

**Risk:** HIGH. Touches interceptor and background credential refresh paths.

**Files:**
- Modify: `antigravity_auth/token_watchdog.py`
- Modify: `antigravity_auth/verification.py`
- Modify: `antigravity_auth/interceptor.py:198-274`
- Test: `antigravity_auth/test_interceptor.py`, `antigravity_auth/test_verification.py`, add/update watchdog tests if present

**Step 1: Write failing interceptor test**

Add to `antigravity_auth/test_interceptor.py`:

```python
class TestResponseHook(unittest.TestCase):
    def test_401_syncs_rotated_refresh_token_to_google_oauth(self):
        from antigravity_auth.interceptor import _antigravity_response_hook
        from antigravity_auth.storage import save_accounts

        save_accounts({
            "version": 4,
            "accounts": [{
                "email": "user@example.com",
                "refreshToken": "old-refresh",
                "projectId": "proj-1",
            }],
            "activeIndex": 0,
            "activeIndexByFamily": {"claude": 0, "gemini": 0},
        })
        req = httpx.Request("POST", "https://cloudcode-pa.googleapis.com/v1internal:generateContent")
        response = httpx.Response(401, request=req)
        synced = []

        with patch("antigravity_auth.token.refresh_access_token", return_value={
            "access": "new-access",
            "refresh": "new-refresh|proj-1",
            "expires": 123,
        }), patch("antigravity_auth.auth_sync.sync_token_to_google_oauth", side_effect=lambda **kw: synced.append(kw) or True):
            _antigravity_response_hook(response)

        self.assertEqual(synced[0]["refresh_token"], "new-refresh|proj-1")
```

Add required imports (`patch`, temp HERMES_HOME setup) using existing test patterns.

**Step 2: Run targeted test to verify failure**

```bash
python3 -m pytest antigravity_auth/test_interceptor.py::TestResponseHook::test_401_syncs_rotated_refresh_token_to_google_oauth -q
```

Expected: FAIL — current code syncs old refresh token. If Task 4 has not yet changed `interceptor.py` to import from `.auth_sync`, patching `antigravity_auth.auth_sync.sync_token_to_google_oauth` will not intercept the call; Task 7 must explicitly update `interceptor.py`, `token_watchdog.py`, and `verification.py` to import `sync_token_to_google_oauth` from `.auth_sync`, not `.cli`.

**Step 3: Implement caller fixes one file at a time**

For each file, first replace any `from .cli import sync_token_to_google_oauth` import with `from .auth_sync import sync_token_to_google_oauth`. Then build packed refresh with project ID, call `refresh_access_token(..., persist=False)`, and use `refreshed.get("refresh") or packed_refresh` when syncing.

Pattern:

```python
packed_refresh = format_refresh_parts({
    "refreshToken": account.refresh_parts.refresh_token,
    "projectId": account.refresh_parts.project_id or "",
    "managedProjectId": account.refresh_parts.managed_project_id or "",
})
refreshed = refresh_access_token({"refresh": packed_refresh, "email": account.email})
synced_refresh = refreshed.get("refresh") or packed_refresh
sync_token_to_google_oauth(
    access_token=refreshed["access"],
    refresh_token=synced_refresh,
    project_id=account.refresh_parts.project_id or "",
    email=account.email,
    expires_ms=refreshed.get("expires"),
)
```

**Step 4: Verify after each file**

After changing each of `token_watchdog.py`, `verification.py`, and `interceptor.py`, run:

```bash
python3 -m pytest antigravity_auth/test_token.py antigravity_auth/test_verification.py antigravity_auth/test_interceptor.py -q
python3 -c "from antigravity_auth.auth_sync import sync_token_to_all_auth_stores; print('AUTH_SYNC_OK')"
python3 -c "from antigravity_auth.interceptor import _antigravity_response_hook; print('INTERCEPTOR_OK')"
python3 -c "from antigravity_auth.accounts.manager import AccountManager; print('MANAGER_OK')"
```

Expected: PASS and import messages.

**Step 5: Full verify**

```bash
python3 -m pytest antigravity_auth/ -q
python3 -m compileall -q antigravity_auth plugins
```

Expected: PASS.

**Step 6: Commit**

```bash
git add antigravity_auth/token_watchdog.py antigravity_auth/verification.py antigravity_auth/interceptor.py antigravity_auth/test_interceptor.py antigravity_auth/test_verification.py
git commit -m "fix: propagate rotated refresh tokens"
```

---

### Task 8: Sync runtime credentials after account deletion and persist final removal

**Objective:** Deleting the active account must either activate/sync the next account or clear both runtime stores; `AccountManager.remove_account()` must save when the last account is removed.

**Risk:** MODERATE. Touches account manager and CLI.

**Files:**
- Modify: `antigravity_auth/cli.py:288-327`
- Modify: `antigravity_auth/accounts/manager.py:397-421`
- Test: `antigravity_auth/test_cli.py`, `antigravity_auth/accounts/test_manager.py`

**Step 1: Write failing manager test**

In `antigravity_auth/accounts/test_manager.py`, add this method to the existing `TestAccountManagerWithAccounts` class. It must patch `antigravity_auth.storage.get_accounts_json_path` during both removal and reload so the test never touches the real `~/.hermes` account store:

```python
    def test_remove_last_account_persists_empty_accounts(self) -> None:
        data = {
            "version": 4,
            "accounts": [{
                "email": "alice@example.com",
                "refreshToken": "refresh-alice",
                "projectId": "proj-a",
            }],
            "activeIndex": 0,
            "cursor": 0,
            "activeIndexByFamily": {"claude": 0, "gemini": 0},
        }
        manager = self._make_manager(data)

        with mock.patch(
            "antigravity_auth.storage.get_accounts_json_path",
            return_value=self.accounts_path,
        ):
            self.assertTrue(manager.remove_account(0))

        with open(self.accounts_path, "r", encoding="utf-8") as f:
            stored = json.load(f)
        self.assertEqual(stored["accounts"], [])
        self.assertEqual(stored["activeIndexByFamily"], {"claude": 0, "gemini": 0})
```

**Step 2: Run test to verify failure**

```bash
python3 -m pytest antigravity_auth/accounts/test_manager.py::TestAccountManagerWithAccounts::test_remove_last_account_persists_empty_accounts -q
```

Expected: FAIL — final removal returns before save, so the JSON file still contains the removed account.

**Step 3: Implement manager fix**

In `remove_account()`, before returning in the empty-account branch, call `_request_save_to_disk()` or `save_to_disk()` directly. Prefer direct save for deletion:

```python
    if not self._accounts:
      self._cursor = 0
      self._current_account_by_family["claude"] = -1
      self._current_account_by_family["gemini"] = -1
      self.save_to_disk()
      return True
```

**Step 4: Write CLI deletion test**

In `antigravity_auth/test_cli.py`, add a test that deletes index 0 from two accounts and asserts the next account is synced via `sync_token_to_all_auth_stores` with packed refresh.

**Step 5: Implement CLI fix**

After `save_accounts(accounts_data)`, add:

```python
    if accounts:
        new_idx = accounts_data.get("activeIndex", 0)
        active = accounts[new_idx]
        packed_refresh = format_refresh_parts({
            "refreshToken": active.get("refreshToken", ""),
            "projectId": active.get("projectId") or "",
            "managedProjectId": active.get("managedProjectId") or "",
        })
        try:
            from .token import refresh_access_token
            from .auth_sync import sync_token_to_all_auth_stores
            refreshed = refresh_access_token({"refresh": packed_refresh, "email": active.get("email")})
            sync_token_to_all_auth_stores(
                access_token=refreshed.get("access", ""),
                refresh_token=refreshed.get("refresh") or packed_refresh,
                project_id=active.get("projectId") or "",
                email=active.get("email"),
                expires_ms=refreshed.get("expires"),
                set_active=True,
            )
        except Exception:
            pass
    else:
        from .auth_sync import sync_token_to_all_auth_stores
        sync_token_to_all_auth_stores("", "", project_id="", email=None, set_active=False)
```

If clearing `google_oauth.json` cannot be represented with the current Hermes API, document that in a comment and at least clear `auth.json`. When clearing `auth.json`, ensure empty tokens are written under both `antigravity` and `google-gemini-cli`, and `active_provider` is left unchanged or set to `""` only if Hermes can handle no active provider. Add the explicit behavior to `test_cli.py` so deletion of the last account cannot leave deleted credentials active.

**Step 6: Verify**

```bash
python3 -m pytest antigravity_auth/test_cli.py antigravity_auth/accounts/test_manager.py -q
python3 -m pytest antigravity_auth/ -q
python3 -c "from antigravity_auth.accounts.manager import AccountManager; print('MANAGER_OK')"
```

Expected: PASS.

**Step 7: Commit**

```bash
git add antigravity_auth/cli.py antigravity_auth/accounts/manager.py antigravity_auth/test_cli.py antigravity_auth/accounts/test_manager.py
git commit -m "fix: sync credentials after account deletion"
```

---

## Phase 2: Interceptor, account rotation, and Claude request correctness

### Task 9: Add model-family and header-style helpers in interceptor

**Objective:** Stop hard-coding Gemini in response hooks and prevent `cli_first` from applying Gemini CLI headers to Claude/GPT-OSS requests.

**Risk:** HIGH. Touches interceptor heartbeat. Single-file change with import verification.

**Files:**
- Modify: `antigravity_auth/interceptor.py:149-194`
- Test: `antigravity_auth/test_interceptor.py`

**Step 1: Write failing tests**

Add to `TestRequestHook` or a new helper test class:

```python
    def test_claude_uses_antigravity_headers_even_when_cli_first_enabled(self):
        from antigravity_auth.interceptor import _select_header_style_for_model
        self.assertEqual(_select_header_style_for_model("claude-sonnet-4-6-thinking", cli_first=True), "antigravity")

    def test_gemini_uses_gemini_cli_headers_only_when_cli_first_enabled(self):
        from antigravity_auth.interceptor import _select_header_style_for_model
        self.assertEqual(_select_header_style_for_model("gemini-3.1-pro-high", cli_first=True), "gemini-cli")
        self.assertEqual(_select_header_style_for_model("gemini-3.1-pro-high", cli_first=False), "antigravity")

    def test_model_family_for_claude_and_gemini(self):
        from antigravity_auth.interceptor import _model_family_for_model
        self.assertEqual(_model_family_for_model("claude-sonnet-4-6"), "claude")
        self.assertEqual(_model_family_for_model("gemini-3.1-pro-high"), "gemini")
        self.assertEqual(_model_family_for_model("gpt-oss-120b-medium"), "gemini")
```

**Step 2: Run tests to verify failure**

```bash
python3 -m pytest antigravity_auth/test_interceptor.py -q
```

Expected: FAIL — helpers do not exist.

**Step 3: Implement helpers**

Add near top of `interceptor.py`:

```python
def _model_family_for_model(model: str) -> str:
  lower = (model or "").lower()
  if "claude" in lower:
    return "claude"
  return "gemini"


def _select_header_style_for_model(model: str, cli_first: bool) -> str:
  lower = (model or "").lower()
  if cli_first and "gemini" in lower and "claude" not in lower:
    return "gemini-cli"
  return "antigravity"
```

Then replace `header_style = "gemini-cli" if config.cli_first else "antigravity"` with:

```python
    header_style = _select_header_style_for_model(model, config.cli_first)
```

**Step 4: Verify**

```bash
python3 -m pytest antigravity_auth/test_interceptor.py -q
python3 -m pytest antigravity_auth/ -q
python3 -c "from antigravity_auth.interceptor import _select_header_style_for_model; print('INTERCEPTOR_OK')"
```

Expected: PASS and `INTERCEPTOR_OK`.

**Step 5: Commit**

```bash
git add antigravity_auth/interceptor.py antigravity_auth/test_interceptor.py
git commit -m "fix: make interceptor model family aware"
```

---

### Task 10: Select and sync the request account before each Cloud Code request

**Objective:** Make multi-account selection happen before a request, not only after a failed response; set the outgoing `Authorization` header to the selected account's fresh access token.

**Risk:** HIGH / PARENT-DIRECT. This touches `interceptor.py`, account manager, token refresh, and auth sync. Execute serially in the parent or a single focused subagent; do not parallelize with other interceptor tasks.

**Files:**
- Modify: `antigravity_auth/interceptor.py:149-191`
- Test: `antigravity_auth/test_interceptor.py`

**Step 1: Write failing request-account test**

Add to `TestRequestHook`:

```python
    def test_request_hook_sets_authorization_for_selected_account(self):
        r = self._make_request(model="claude-sonnet-4-6-thinking")

        class FakeRefreshParts:
            refresh_token = "refresh-1"
            project_id = "proj-1"
            managed_project_id = "managed-1"

        class FakeAccount:
            email = "user@example.com"
            index = 0
            refresh_parts = FakeRefreshParts()
            fingerprint = None

        class FakeManager:
            def get_current_or_next_for_family(self, family, model=None, strategy="sticky", header_style="antigravity", **kwargs):
                self.family = family
                self.model = model
                self.header_style = header_style
                return FakeAccount()
            def mark_account_used(self, account_index):
                pass
            def save_to_disk(self):
                return True

        fake_mgr = FakeManager()
        with patch("antigravity_auth.accounts.shared.get_or_create_global_manager", return_value=fake_mgr), \
             patch("antigravity_auth.token.refresh_access_token", return_value={
                 "access": "selected-access",
                 "refresh": "refresh-1|proj-1|managed-1",
                 "expires": 123,
             }), \
             patch("antigravity_auth.auth_sync.sync_token_to_all_auth_stores", return_value=True):
            self.hook(r)

        self.assertEqual(r.headers.get("Authorization"), "Bearer selected-access")
        self.assertEqual(fake_mgr.family, "claude")
        self.assertEqual(fake_mgr.header_style, "antigravity")
```

**Step 2: Run test to verify failure**

```bash
python3 -m pytest antigravity_auth/test_interceptor.py::TestRequestHook::test_request_hook_sets_authorization_for_selected_account -q
```

Expected: FAIL — request hook does not select account or replace Authorization.

**Step 3: Implement helper**

Add a private helper in `interceptor.py`:

```python
def _packed_refresh_for_account(account: Any) -> str:
  from .token import format_refresh_parts
  parts = account.refresh_parts
  return format_refresh_parts({
    "refreshToken": parts.refresh_token,
    "projectId": parts.project_id or "",
    "managedProjectId": parts.managed_project_id or "",
  })


def _select_request_account(model: str, header_style: str, config: Any) -> dict[str, Any] | None:
  try:
    from .accounts.shared import get_or_create_global_manager
    from .accounts.quota import compute_soft_quota_cache_ttl_ms
    from .token import refresh_access_token
    from .auth_sync import sync_token_to_all_auth_stores
    mgr = get_or_create_global_manager()
    family = _model_family_for_model(model)
    soft_quota_cache_ttl_ms = compute_soft_quota_cache_ttl_ms(
      config.soft_quota_cache_ttl_minutes,
      config.quota_refresh_interval_minutes,
    )
    account = mgr.get_current_or_next_for_family(
      family,
      model=model,
      strategy=config.account_selection_strategy,
      header_style=header_style,
      pid_offset_enabled=config.pid_offset_enabled,
      soft_quota_threshold_percent=config.soft_quota_threshold_percent,
      soft_quota_cache_ttl_ms=soft_quota_cache_ttl_ms,
    )
    if account is None:
      return None
    packed_refresh = _packed_refresh_for_account(account)
    refreshed = refresh_access_token({"refresh": packed_refresh, "email": account.email})
    if not refreshed.get("access"):
      return None
    sync_token_to_all_auth_stores(
      access_token=refreshed["access"],
      refresh_token=refreshed.get("refresh") or packed_refresh,
      project_id=account.refresh_parts.project_id or "",
      email=account.email,
      expires_ms=refreshed.get("expires"),
      set_active=True,
    )
    mgr.mark_account_used(account.index)
    mgr.save_to_disk()
    return {"access": refreshed["access"], "account": account, "family": family}
  except Exception as e:
    logger.warning("Request-time account selection failed: %s", e)
    return None
```

Then in `_antigravity_request_hook()`, after computing header style and before header pruning:

```python
    selected = _select_request_account(model, header_style, config)
```

After header injection:

```python
    if selected and selected.get("access"):
        request.headers["Authorization"] = f"Bearer {selected['access']}"
```

**Step 4: Verify heartbeat**

```bash
python3 -m pytest antigravity_auth/test_interceptor.py -q
python3 -m pytest antigravity_auth/accounts/test_manager.py antigravity_auth/accounts/test_ratelimit.py -q
python3 -m pytest antigravity_auth/ -q
python3 -c "from antigravity_auth.auth_sync import sync_token_to_all_auth_stores; print('AUTH_SYNC_OK')"
python3 -c "from antigravity_auth.interceptor import _select_request_account; print('INTERCEPTOR_OK')"
python3 -c "from antigravity_auth.accounts.manager import AccountManager; print('MANAGER_OK')"
python3 -c "from antigravity_auth.accounts.ratelimit import mark_rate_limited; print('RATELIMIT_OK')"
```

Expected: PASS and import messages.

**Step 5: Commit**

```bash
git add antigravity_auth/interceptor.py antigravity_auth/test_interceptor.py
git commit -m "fix: select antigravity account before requests"
```

---

### Task 11: Make response rotation family/header-style aware

**Objective:** On 403/429, mark the account family and quota pool for the actual request model/header style, not hard-coded Gemini/both-pools.

**Risk:** HIGH. Touches silently swallowed rate-limit chain.

**Files:**
- Modify: `antigravity_auth/interceptor.py:194-284`
- Test: `antigravity_auth/test_interceptor.py`

**Step 1: Write failing tests**

Add these copy-pasteable helpers/tests to `TestResponseHook` in `antigravity_auth/test_interceptor.py` (ensure `from unittest.mock import patch` exists at the top of the file):

```python
    def _make_response(self, model="gemini-3.1-pro-high", status=429, header_style="antigravity"):
        body = {"project": "proj", "model": model, "request": {"contents": []}}
        req = httpx.Request(
            "POST",
            "https://cloudcode-pa.googleapis.com/v1internal:generateContent",
            json=body,
        )
        req.read()
        req.extensions["antigravity_header_style"] = header_style
        req.extensions["antigravity_model_family"] = "claude" if "claude" in model else "gemini"
        return httpx.Response(status, request=req, headers={"Retry-After": "3"})

    def test_429_for_claude_marks_claude_family(self):
        from antigravity_auth.interceptor import _antigravity_response_hook

        class FakeAccount:
            index = 0
            email = "user@example.com"

        class FakeManager:
            def get_current_account_for_family(self, family):
                self.current_family = family
                return FakeAccount()
            def get_current_or_next_for_family(self, family, strategy="hybrid", **kwargs):
                return FakeAccount()
            def save_to_disk(self):
                return True

        mgr = FakeManager()
        calls = []
        def fake_mark(account, retry_after_ms, family, header_style="antigravity", model=None):
            calls.append((family, header_style, model))

        response = self._make_response(model="claude-sonnet-4-6-thinking", status=429)
        with patch("antigravity_auth.accounts.manager.get_or_create_global_manager", return_value=mgr), \
             patch("antigravity_auth.accounts.ratelimit.mark_rate_limited", side_effect=fake_mark):
            _antigravity_response_hook(response)

        self.assertEqual(mgr.current_family, "claude")
        self.assertEqual(calls, [("claude", "antigravity", "claude-sonnet-4-6-thinking")])

    def test_429_marks_only_actual_header_style(self):
        from antigravity_auth.interceptor import _antigravity_response_hook

        class FakeAccount:
            index = 0
            email = "user@example.com"

        class FakeManager:
            def get_current_account_for_family(self, family):
                return FakeAccount()
            def get_current_or_next_for_family(self, family, strategy="hybrid", **kwargs):
                return FakeAccount()
            def save_to_disk(self):
                return True

        calls = []
        def fake_mark(account, retry_after_ms, family, header_style="antigravity", model=None):
            calls.append((family, header_style, model))

        response = self._make_response(model="gemini-3.1-pro-high", status=429, header_style="antigravity")
        with patch("antigravity_auth.accounts.manager.get_or_create_global_manager", return_value=FakeManager()), \
             patch("antigravity_auth.accounts.ratelimit.mark_rate_limited", side_effect=fake_mark):
            _antigravity_response_hook(response)

        self.assertEqual(calls, [("gemini", "antigravity", "gemini-3.1-pro-high")])
```

**Step 2: Run tests to verify failure**

```bash
python3 -m pytest antigravity_auth/test_interceptor.py -q
```

Expected: FAIL — family is hard-coded to Gemini and both pools are marked.

**Step 3: Implement request metadata extraction**

Add helper:

```python
def _request_model_from_response(response: httpx.Response) -> str:
  try:
    body = json.loads(response.request.content)
    if isinstance(body, dict):
      return str(body.get("model") or "")
  except Exception:
    return ""
  return ""
```

Set header style into request extensions during the request hook:

```python
    request.extensions["antigravity_header_style"] = header_style
    request.extensions["antigravity_model_family"] = _model_family_for_model(model)
```

Use those in response hook:

```python
    model = _request_model_from_response(response)
    family = response.request.extensions.get("antigravity_model_family") or _model_family_for_model(model)
    header_style = response.request.extensions.get("antigravity_header_style") or _select_header_style_for_model(model, config.cli_first)
```

Replace hard-coded `"gemini"` and double `mark_rate_limited(...)` calls.

**Step 4: Verify import chain**

```bash
python3 -m pytest antigravity_auth/test_interceptor.py -q
python3 -m pytest antigravity_auth/accounts/test_ratelimit.py antigravity_auth/accounts/test_manager.py -q
python3 -m pytest antigravity_auth/ -q
python3 -c "from antigravity_auth.auth_sync import sync_token_to_all_auth_stores; print('AUTH_SYNC_OK')"
python3 -c "from antigravity_auth.accounts.ratelimit import mark_rate_limited; print('RATELIMIT_OK')"
python3 -c "from antigravity_auth.interceptor import _antigravity_response_hook; print('INTERCEPTOR_OK')"
```

Expected: PASS.

**Step 5: Commit**

```bash
git add antigravity_auth/interceptor.py antigravity_auth/test_interceptor.py
git commit -m "fix: rotate accounts by actual request family"
```

---

### Task 12: Preserve existing tool call IDs and OpenAI `tool_call_id` mappings

**Objective:** Ensure Claude function calls/responses keep stable IDs from OpenAI/Hermes tool calls and generated IDs propagate to responses.

**Risk:** MODERATE. Body transform only; not a httpx hook change.

**Files:**
- Modify: `antigravity_auth/interceptor.py:24-80`
- Modify: `antigravity_auth/transform/messages.py`
- Test: `antigravity_auth/test_inject_tool_call_ids.py`, `antigravity_auth/transform/test_messages.py`

**Step 1: Write failing test for existing IDs**

In `antigravity_auth/test_inject_tool_call_ids.py` add:

```python
    def test_existing_function_call_id_is_reused_for_matching_response(self):
        inner = {
            "contents": [
                {"role": "model", "parts": [{"functionCall": {"name": "read_file", "args": {}, "id": "call_existing"}}]},
                {"role": "user", "parts": [{"functionResponse": {"name": "read_file", "response": {"ok": True}}}]},
            ]
        }

        _inject_tool_call_ids(inner)

        fr = inner["contents"][1]["parts"][0]["functionResponse"]
        self.assertEqual(fr["id"], "call_existing")
```

**Step 2: Write failing message conversion test**

In `antigravity_auth/transform/test_messages.py` add a test where assistant `tool_calls[0].id == "call_abc"` and a later tool message has only `tool_call_id == "call_abc"`; assert `functionCall.id` and `functionResponse.id` are both `call_abc` and response name is recovered.

**Step 3: Run tests to verify failure**

```bash
python3 -m pytest antigravity_auth/test_inject_tool_call_ids.py antigravity_auth/transform/test_messages.py -q
```

Expected: FAIL.

**Step 4: Implement `_inject_tool_call_ids` fix**

Change pass 1 so every functionCall queues its ID:

```python
      if isinstance(fc, dict):
        if not fc.get("id"):
          counter += 1
          fc["id"] = f"tool-call-{counter}"
        name = str(fc.get("name") or f"tool-{counter}")
        pending.setdefault(name, []).append(fc["id"])
```

**Step 5: Implement message conversion mapping**

In `transform/messages.py`, preserve `tool_calls[].id` inside `functionCall`, and keep a local `tool_call_id_to_name` mapping while converting messages. For tool-result messages, use `tool_call_id` to recover both `id` and `name` when `name` is absent.

**Step 6: Verify**

```bash
python3 -m pytest antigravity_auth/test_inject_tool_call_ids.py antigravity_auth/transform/test_messages.py -q
python3 -m pytest antigravity_auth/ -q
python3 -c "from antigravity_auth.interceptor import _inject_tool_call_ids, install; print('INTERCEPTOR_OK')"
```

Expected: PASS.

**Step 7: Commit**

```bash
git add antigravity_auth/interceptor.py antigravity_auth/transform/messages.py antigravity_auth/test_inject_tool_call_ids.py antigravity_auth/transform/test_messages.py
git commit -m "fix: preserve tool call ids through transforms"
```

---

### Task 13: Complete Claude body transforms before wrapping

**Objective:** Create VALIDATED tool config when missing and strip stale thinking blocks before Claude requests.

**Risk:** HIGH. Touches Claude compatibility path in `wrap_code_assist_request()` patch.

**Files:**
- Modify: `antigravity_auth/interceptor.py:82-147,316-323`
- Test: `antigravity_auth/test_claude_transforms.py`

**Step 1: Write failing VALIDATED test**

In `antigravity_auth/test_claude_transforms.py`, replace/extend the existing "no toolConfig remains absent" expectation with:

```python
    def test_apply_claude_transforms_creates_validated_tool_config_when_tools_exist(self):
        inner = {
            "tools": [{"functionDeclarations": [{"name": "x", "parameters": {"type": "object", "properties": {}}}]}]
        }

        _apply_claude_transforms(inner)

        self.assertEqual(
            inner["toolConfig"]["functionCallingConfig"]["mode"],
            "VALIDATED",
        )
```

**Step 2: Write failing thinking-strip test**

Add:

```python
    def test_apply_claude_transforms_strips_stale_thinking_parts_by_default(self):
        inner = {
            "contents": [{
                "role": "model",
                "parts": [
                    {"thought": True, "text": "old reasoning", "thoughtSignature": "sig"},
                    {"text": "visible"},
                ],
            }]
        }

        _apply_claude_transforms(inner)

        self.assertEqual(inner["contents"][0]["parts"], [{"text": "visible"}])
```

**Step 3: Run tests to verify failure**

```bash
python3 -m pytest antigravity_auth/test_claude_transforms.py -q
```

Expected: FAIL.

**Step 4: Implement minimal fix**

At start of `_apply_claude_transforms()`:

```python
  config = get_config()
  if not config.keep_thinking:
    from .transform.thinking import deep_filter_thinking_blocks
    deep_filter_thinking_blocks(inner_request)
```

For tool config creation and normalization, use this exact order: detect whether tools exist, create `toolConfig` if missing, then unconditionally normalize `functionCallingConfig.mode` to `"VALIDATED"` for the resulting dict.

```python
  tools = inner_request.get("tools")
  has_function_declarations = any(
    isinstance(group, dict) and group.get("functionDeclarations")
    for group in tools
  ) if isinstance(tools, list) else False

  if has_function_declarations and not isinstance(inner_request.get("toolConfig"), dict):
    inner_request["toolConfig"] = {}

  tool_config = inner_request.get("toolConfig")
  if isinstance(tool_config, dict):
    fcc = tool_config.get("functionCallingConfig")
    if isinstance(fcc, dict):
      fcc["mode"] = "VALIDATED"
    else:
      tool_config["functionCallingConfig"] = {"mode": "VALIDATED"}
```

**Step 5: Verify heartbeat**

```bash
python3 -m pytest antigravity_auth/test_claude_transforms.py antigravity_auth/transform/test_thinking.py -q
python3 -m pytest antigravity_auth/ -q
python3 -c "from antigravity_auth.auth_sync import sync_token_to_all_auth_stores; print('AUTH_SYNC_OK')"
python3 -c "from antigravity_auth.interceptor import _apply_claude_transforms; print('CLAUDE_TRANSFORMS_OK')"
```

Expected: PASS.

**Step 6: Commit**

```bash
git add antigravity_auth/interceptor.py antigravity_auth/test_claude_transforms.py
git commit -m "fix: complete claude antigravity transforms"
```

---

## Phase 3: Transform semantics and utility correctness

### Task 14: Fix schema sanitizer enum and nested nullable semantics

**Objective:** Preserve numeric/boolean enum values and remove nullable fields from nested object `required` arrays.

**Risk:** LOW. Transform helper and tests only.

**Files:**
- Modify: `antigravity_auth/transform/schema.py`
- Test: `antigravity_auth/transform/test_schema.py`

**Step 1: Write failing tests**

Add tests:

```python
    def test_numeric_enum_values_are_not_stringified(self):
        schema = {"type": "object", "properties": {"n": {"enum": [1, 2, 3]}}}
        out = clean_json_schema(schema)
        self.assertEqual(out["properties"]["n"]["enum"], [1, 2, 3])
        self.assertEqual(out["properties"]["n"]["type"], "integer")

    def test_nested_nullable_required_field_is_removed_from_required(self):
        schema = {
            "type": "object",
            "properties": {
                "outer": {
                    "type": "object",
                    "required": ["maybe"],
                    "properties": {"maybe": {"anyOf": [{"type": "string"}, {"type": "null"}]}}
                }
            },
        }
        out = clean_json_schema(schema)
        self.assertNotIn("maybe", out["properties"]["outer"].get("required", []))
```

Use the existing `clean_json_schema` import/export pattern in `antigravity_auth/transform/test_schema.py`; do not introduce a non-existent `sanitize_schema` helper.

**Step 2: Run tests to verify failure**

```bash
python3 -m pytest antigravity_auth/transform/test_schema.py -q
```

Expected: FAIL.

**Step 3: Implement fixes**

- Do not coerce enum/const values to strings.
- Infer enum type only when all enum values share a primitive type.
- Apply nullable-required cleanup recursively inside every object schema.

**Step 4: Verify**

```bash
python3 -m pytest antigravity_auth/transform/test_schema.py -q
python3 -m pytest antigravity_auth/ -q
```

Expected: PASS.

**Step 5: Commit**

```bash
git add antigravity_auth/transform/schema.py antigravity_auth/transform/test_schema.py
git commit -m "fix: preserve schema enum semantics"
```

---

### Task 15: Make envelope wrapping immutable and model-resolution complete

**Objective:** Prevent `build_antigravity_envelope()` from mutating caller payloads, normalize `systemInstruction`, and make `resolve_model_for_header_style()` consult `MODEL_NAME_MAP` for documented aliases.

**Risk:** LOW to MODERATE. Transform helper; can affect request wrapping if wired later.

**Files:**
- Modify: `antigravity_auth/transform/envelope.py`
- Test: `antigravity_auth/transform/test_envelope.py`

**Step 1: Write failing immutability test**

```python
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
```

Add `import copy` if needed and use the existing `build_antigravity_envelope` import in `antigravity_auth/transform/test_envelope.py`.

**Step 2: Write model-resolution table test**

Parametrize/list all `ANTIGRAVITY_MODELS` from `hermes_provider_plugin.py` and assert `resolve_model_for_header_style(model, "antigravity") == MODEL_NAME_MAP.get(model, model)`. Add explicit legacy alias cases such as:

```python
    def test_resolve_model_for_header_style_uses_model_name_map_for_antigravity_aliases(self):
        self.assertEqual(
            resolve_model_for_header_style("antigravity-gemini-3.1-pro", "antigravity"),
            "gemini-3.1-pro-high",
        )
        self.assertEqual(
            resolve_model_for_header_style("antigravity-claude-sonnet-4-6-thinking", "antigravity"),
            "claude-sonnet-4-6-thinking",
        )
```

This currently fails because `resolve_model_for_header_style()` only strips `antigravity-` for `gemini-cli` and otherwise returns the input model unchanged.

**Step 3: Run tests to verify failure**

```bash
python3 -m pytest antigravity_auth/transform/test_envelope.py -q
```

Expected: FAIL for mutation and/or stale mappings.

**Step 4: Implement minimal fix**

Use a deep copy at the top of wrapping:

```python
import copy

request_payload = copy.deepcopy(request_payload)
```

Normalize snake_case:

```python
snake_system = request_payload.pop("system_instruction", None)
if snake_system is not None and "systemInstruction" not in request_payload:
    request_payload["systemInstruction"] = snake_system
```

Update `MODEL_NAME_MAP` to match `ANTIGRAVITY_MODELS` and Antigravity 2.0 naming rules. Then update `resolve_model_for_header_style()` so both header styles use the map before any special stripping:

```python
def resolve_model_for_header_style(model: str, header_style: HeaderStyle) -> str:
  mapped = MODEL_NAME_MAP.get(model, model)
  if header_style == "gemini-cli" and mapped.startswith("antigravity-"):
    return mapped[len("antigravity-"):]
  return mapped
```

**Step 5: Verify**

```bash
python3 -m pytest antigravity_auth/transform/test_envelope.py -q
python3 -m pytest antigravity_auth/ -q
```

Expected: PASS.

**Step 6: Commit**

```bash
git add antigravity_auth/transform/envelope.py antigravity_auth/transform/test_envelope.py
git commit -m "fix: make envelope wrapping immutable"
```

---

### Task 16: Fix OAuth manual fallback and PKCE verifier lifecycle

**Objective:** Code-only manual login should work, and abandoned PKCE verifier entries should not live forever.

**Risk:** LOW. OAuth CLI path only.

**Files:**
- Modify: `antigravity_auth/oauth.py`
- Modify: `antigravity_auth/cli.py:172-218`
- Test: `antigravity_auth/test_oauth.py`, `antigravity_auth/test_cli.py`

**Step 1: Write failing OAuth test**

In `test_oauth.py` assert `authorize_antigravity()` returns `state`. Patch credentials the same way as the existing `test_authorize_antigravity()` so clean git/CI installs without `_credentials.py` fail only for the intended missing-state behavior:

```python
    def test_authorize_returns_state_for_manual_code_only_flow(self):
        with patch('antigravity_auth.constants._credentials_valid', True), \
             patch('antigravity_auth.constants.ANTIGRAVITY_CLIENT_ID', 'test_client_id'), \
             patch('antigravity_auth.constants.ANTIGRAVITY_CLIENT_SECRET', 'test_client_secret'), \
             patch('antigravity_auth.oauth.ANTIGRAVITY_REDIRECT_URI', 'http://localhost:51121/oauth-callback'), \
             patch('antigravity_auth.oauth.ANTIGRAVITY_SCOPES', ['scope1', 'scope2']):
            data = authorize_antigravity(project_id="proj")

        self.assertIn("state", data)
        self.assertTrue(data["state"])
        decoded = decode_state(data["state"])
        self.assertIn("id", decoded)
```

**Step 2: Run test to verify failure**

```bash
python3 -m pytest antigravity_auth/test_oauth.py -q
```

Expected: FAIL if `state` is not returned.

**Step 3: Implement minimal fix**

Return the encoded state from `authorize_antigravity()`. Keep one local variable so URL and return value cannot diverge:

```python
encoded_state = encode_state({"id": state_id})
params = {
    # existing params...
    "state": encoded_state,
}
url = f"https://accounts.google.com/o/oauth2/v2/auth?{urlencode(params)}"
return {
    "url": url,
    "verifier": pkce["verifier"],
    "state": encoded_state,
    "projectId": project_id or "",
    "project_id": project_id or "",
}
```

Remove `br` from token request `Accept-Encoding` unless Brotli decoding is implemented:

```python
"Accept-Encoding": "gzip, deflate"
```

Add concrete TTL cleanup around `_pkce_verifier_store` entries. Keep stdlib-only:

```python
_PKCE_VERIFIER_TTL_SECONDS = 600


def _cleanup_expired_pkce_verifiers(now: float | None = None) -> None:
    current = time.time() if now is None else now
    expired = [
        state_id for state_id, value in _pkce_verifier_store.items()
        if current - float(value.get("createdAt", current)) > _PKCE_VERIFIER_TTL_SECONDS
    ]
    for state_id in expired:
        _pkce_verifier_store.pop(state_id, None)
```

Call `_cleanup_expired_pkce_verifiers()` at the start of `authorize_antigravity()` and `get_pkce_verifier()`, store `"createdAt": str(time.time())` alongside `verifier` and `projectId`, and make `get_pkce_verifier()` return `None` for expired entries. Add this test:

```python
    def test_get_pkce_verifier_expires_old_entries(self):
        _pkce_verifier_store["old"] = {
            "verifier": "v",
            "projectId": "p",
            "createdAt": "0",
        }
        with patch("antigravity_auth.oauth.time.time", return_value=999999):
            self.assertIsNone(get_pkce_verifier("old"))
        self.assertNotIn("old", _pkce_verifier_store)
```

**Step 4: Verify**

```bash
python3 -m pytest antigravity_auth/test_oauth.py antigravity_auth/test_cli.py -q
python3 -m pytest antigravity_auth/ -q
```

Expected: PASS.

**Step 5: Commit**

```bash
git add antigravity_auth/oauth.py antigravity_auth/cli.py antigravity_auth/test_oauth.py antigravity_auth/test_cli.py
git commit -m "fix: repair manual oauth fallback"
```

---

### Task 17: Harden Google Search parsing and tool active-index bounds

**Objective:** Accept camelCase grounding metadata and prevent stale activeIndex from crashing `google_antigravity_search`.

**Risk:** LOW. Tool/search path only.

**Files:**
- Modify: `antigravity_auth/search.py`
- Modify: `antigravity_auth/tools.py`
- Test: `antigravity_auth/test_search.py`

**Step 1: Write failing camelCase metadata test**

Add to `TestParseSearchResponse` in `antigravity_auth/test_search.py`:

```python
    def test_url_context_metadata_accepts_camel_case_fields(self):
        data = {
            "response": {
                "candidates": [{
                    "content": {"parts": [{"text": "URL result"}]},
                    "urlContextMetadata": {
                        "urlMetadata": [
                            {"retrievedUrl": "https://page.com", "urlRetrievalStatus": "URL_RETRIEVAL_STATUS_SUCCESS"},
                            {"retrievedUrl": "https://broken.com", "urlRetrievalStatus": "URL_RETRIEVAL_STATUS_FAILED"},
                        ],
                    },
                }],
            },
        }

        result = parse_search_response(data)

        self.assertEqual(len(result.urlsRetrieved), 2)
        self.assertEqual(result.urlsRetrieved[0]["url"], "https://page.com")
        self.assertEqual(result.urlsRetrieved[1]["status"], "URL_RETRIEVAL_STATUS_FAILED")
```

**Step 2: Write failing active-index bounds test**

Add a new test class to `antigravity_auth/test_search.py`:

```python
class TestSearchToolRegistration(unittest.TestCase):
    def test_search_handler_rejects_stale_active_index(self):
        from unittest.mock import patch
        from antigravity_auth.tools import _register_search_tool

        class FakeRegistry:
            def register(self, **kwargs):
                self.kwargs = kwargs

        registry = FakeRegistry()
        accounts_data = {
            "activeIndex": 99,
            "accounts": [{"email": "user@example.com", "refreshToken": "refresh", "projectId": "proj"}],
        }

        with patch("antigravity_auth.storage.load_accounts", return_value=accounts_data):
            _register_search_tool(registry)
            output = registry.kwargs["handler"]({"query": "hello"})

        self.assertIn("active account index is invalid", output)
```

**Step 3: Run tests to verify failure**

```bash
python3 -m pytest antigravity_auth/test_search.py -q
```

Expected: FAIL.

**Step 4: Implement minimal fixes**

In `search.py`, always read both snake_case and camelCase:

```python
url_meta = metadata.get("url_metadata") or metadata.get("urlMetadata") or []
retrieved = item.get("retrieved_url") or item.get("retrievedUrl")
status = item.get("url_retrieval_status") or item.get("urlRetrievalStatus")
```

Add `isinstance` guards before indexing nested dict/list values.

In `tools.py`, validate active index:

```python
if not (0 <= active_idx < len(accounts)):
    return "Google Antigravity search unavailable: active account index is invalid. Run `hermes antigravity accounts` to select an account."
```

**Step 5: Verify**

```bash
python3 -m pytest antigravity_auth/test_search.py -q
python3 -m pytest antigravity_auth/ -q
```

Expected: PASS.

**Step 6: Commit**

```bash
git add antigravity_auth/search.py antigravity_auth/tools.py antigravity_auth/test_search.py
git commit -m "fix: harden antigravity search parsing"
```

---

## Phase 4: Documentation, CI, and stale configuration cleanup

### Task 18: Align README/docs with actual runtime and model names

**Objective:** Remove stale installation/model/recovery claims and document what is implemented versus planned.

**Risk:** LOW. Docs only.

**Files:**
- Modify: `README.md`
- Modify: `docs/ARCHITECTURE.md`
- Modify: `docs/ANTIGRAVITY_API_SPEC.md` only if API model/status entries are stale

**Step 1: Update README install truth**

- If the package is not published to PyPI, remove `pip install hermes-antigravity-auth` as the primary LLM-agent instruction or mark it as future/PyPI-only.
- State clearly whether `_credentials.py` is bundled. If not, document `ANTIGRAVITY_CLIENT_ID` / `ANTIGRAVITY_CLIENT_SECRET` as required for git/source installs.
- Change examples to current Antigravity 2.0 model IDs:
  - `gemini-3.1-pro-high`
  - `gemini-3.1-pro-low`
  - `gemini-3.5-flash-medium`
  - `gemini-3.5-flash-high`
- Remove the stale “all Gemini models require `-preview`” claim.
- Replace “Session recovery auto-recovers” with the actual status unless Task 19 implements real recovery.

**Step 2: Update architecture doc**

- Fix `interceptor.py` line-count claims.
- State that httpx request hook is headers/account-selection only.
- State that Claude body transforms happen in `wrap_code_assist_request()` patch.
- Mark endpoint fallback and soft quota cache as implemented only if wired by current code; otherwise document as planned/internal helpers.

**Step 3: Verify docs with grep-style searches**

Run:
```bash
python3 - <<'PY'
from pathlib import Path
for path in [Path('README.md'), Path('docs/ARCHITECTURE.md')]:
    text = path.read_text()
    for stale in ['134-line', 'gemini-3.1-pro-preview', 'pip install hermes-antigravity-auth']:
        if stale in text:
            print(f'STALE {path}: {stale}')
PY
```

Expected: no stale lines unless intentionally kept with a clear caveat.

**Step 4: Commit**

```bash
git add README.md docs/ARCHITECTURE.md docs/ANTIGRAVITY_API_SPEC.md
git commit -m "docs: align antigravity runtime documentation"
```

---

### Task 19: Add CI smoke gates

**Objective:** Prevent future green-local/failed-runtime drift.

**Risk:** LOW. CI only.

**Files:**
- Create: `.github/workflows/ci.yml`

**Step 1: Create workflow**

```yaml
name: CI

on:
  push:
  pull_request:

jobs:
  test:
    runs-on: ubuntu-latest
    strategy:
      fail-fast: false
      matrix:
        python-version: ['3.10', '3.11', '3.12', '3.13']
    steps:
      - uses: actions/checkout@v6
      - uses: actions/setup-python@v6
        with:
          python-version: ${{ matrix.python-version }}
      - name: Install
        run: python -m pip install -e '.[dev]'
      - name: Tests
        run: python -m pytest antigravity_auth/ -q
      - name: Compile
        run: python -m compileall -q antigravity_auth plugins

  clean-install:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v6
      - uses: actions/setup-python@v6
        with:
          python-version: '3.11'
      - name: Build source archive smoke
        run: |
          git archive --format=tar HEAD | tar -x -C /tmp -f -
          cd /tmp
          python -m pip install -e '.[dev]'
          python -m pytest antigravity_auth/ -q
```

**Step 2: Validate YAML locally**

Run:
```bash
ruby -ryaml -e "YAML.load_file('.github/workflows/ci.yml'); puts 'YAML_OK'"
```

Expected: `YAML_OK`.

**Step 3: Verify tests**

```bash
python3 -m pytest antigravity_auth/ -q
python3 -m compileall -q antigravity_auth plugins
```

Expected: PASS.

**Step 4: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: add antigravity plugin test gates"
```

---

### Task 20: Post-implementation audit and final runtime smoke

**Objective:** Verify the final implementation, not just tests, and catch silent plugin degradation.

**Files:** all changed files.

**Step 1: Run full automated gates**

```bash
git status --short
python3 -m pytest antigravity_auth/ -q
python3 -m compileall -q antigravity_auth plugins
```

Expected:
- no unexpected uncommitted files,
- all tests pass,
- compileall exits 0.

**Step 2: Run import-chain gates**

```bash
python3 -c "from antigravity_auth.hermes_plugin import register; print('PLUGIN_OK')"
python3 -c "from antigravity_auth.interceptor import install, _antigravity_request_hook, _antigravity_response_hook; print('INTERCEPTOR_OK')"
python3 -c "from antigravity_auth.accounts.manager import AccountManager; print('MANAGER_OK')"
python3 -c "from antigravity_auth.accounts.ratelimit import mark_rate_limited; print('RATELIMIT_OK')"
python3 -c "from antigravity_auth.auth_sync import sync_token_to_all_auth_stores; print('AUTH_SYNC_OK')"
```

Expected: all `_OK` markers.

**Step 3: Run Hermes runtime smoke**

Use the smoke command from the global verification section.

Expected: `RUNTIME_PROVIDER_OK`.

**Step 4: Run manual one-shot only if credentials are present**

```bash
if /Users/reidar/.local/bin/hermes -z "Say OK" --provider antigravity --model claude-sonnet-4-6-thinking >/tmp/ag-smoke.txt 2>/tmp/ag-smoke.err; then
  cat /tmp/ag-smoke.txt
else
  cat /tmp/ag-smoke.err
fi
```

Expected if credentials/quota are available: response contains `OK`. If credentials/quota unavailable, failure must be clear and must not route to OpenRouter.

**Step 5: Review diffs for silent side-effect regressions**

Run:
```bash
git diff --stat HEAD~20..HEAD
git log --oneline -20
```

Check manually:
- `plugins/model-providers/antigravity/__init__.py` still uses intentional `import *`.
- `interceptor.py` still preserves `Authorization`, `Content-Type`, `Host`, `Accept`, `Accept-Encoding`, `Content-Length`.
- No httpx request hook mutates serialized body bytes.
- `refresh_access_token()` no longer promotes arbitrary accounts unless caller asks explicitly.
- Every refresh caller uses packed refresh and preserves rotated refresh tokens.

**Step 6: Final commit if needed**

If audit fixes produce additional changes:

```bash
git add -A
git commit -m "chore: finalize antigravity remediation audit fixes"
```

---

## Execution notes for subagent-driven-development

- Do not dispatch multiple implementation subagents in parallel for this plan. Most tasks touch shared auth/interceptor files or the same runtime chain.
- For each task, dispatch one implementer, then a spec compliance reviewer, then a code quality reviewer.
- Parent must verify actual worktree state after every subagent:

```bash
git status --short
git log --oneline -3
python3 -m pytest antigravity_auth/ -q
```

- If a subagent claims success but no commit exists, inspect files and either commit from parent or redispatch a fix.
- Treat malformed reviewer output as `REQUEST_CHANGES` until a reviewer explicitly says `PASS` / `APPROVED`.
- Any task touching `interceptor.py`, `accounts/manager.py`, `accounts/ratelimit.py`, `accounts/rotation.py`, or provider side-effect import paths must run the manual import checks before being marked complete.

## Done definition

The remediation is complete only when:
- All tasks are committed.
- `python3 -m pytest antigravity_auth/ -q` passes.
- `python3 -m compileall -q antigravity_auth plugins` passes.
- Hermes runtime smoke resolves `google-gemini-cli`, `antigravity`, and `ag` to Cloud Code, not OpenRouter.
- Docs no longer claim unimplemented features as implemented.
- A final audit confirms no silently swallowed import chain broke plugin load, interception, or rate-limit rotation.
