# Code Review Remediation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Fix all issues from the 2026-05-31 code review: trace file security, credential file hygiene, dead code removal, redaction gaps, and documentation updates.

**Architecture:** This is a plugin codebase with a silently-swallowed interceptor dependency chain. The interceptor's `try/except` blocks mean broken imports produce NO crash — just silently disabled functionality. Tasks touching `interceptor.py`, `redaction.py`, or `storage.py` are in the chain and carry MODERATE risk. Each task is one file at a time with import verification.

**Tech Stack:** Python 3.10+, stdlib-only (urllib, json, dataclasses), pytest, httpx (dependency)

**Runtime dependency chain (from ARCHITECTURE.md):**
```
hermes_plugin.py → interceptor.install()
  → interceptor._antigravity_request_hook
      → config.get_config()
      → fingerprint.build_fingerprint_headers()
      → transform.envelope.build_antigravity_headers()
      → interceptor._select_request_account()
          → accounts.shared.get_or_create_global_manager()
          → token.refresh_access_token()
          → auth_sync.sync_token_to_all_auth_stores()
          → storage.update_accounts()
  → interceptor._antigravity_response_hook
      → accounts.manager.get_or_create_global_manager()
      → accounts.ratelimit.mark_rate_limited()
      → token.refresh_access_token()
```

All tasks touching files in this chain include manual import verification.

---

## Task 1: Clean stale build artifacts

**Objective:** Remove the `build/` directory which contains a stale copy of `_credentials.py` with a real OAuth client secret.

**Risk:** NONE — build/ is gitignored and purely local.

**Files:**
- Delete: `build/` (entire directory)

**Step 1: Remove build directory**

```bash
rm -rf build/
```

**Step 2: Verify removal**

```bash
test -d build && echo "FAIL: build/ still exists" || echo "OK: build/ removed"
```
Expected: `OK: build/ removed`

**Step 3: Commit**

```bash
git add -A
git commit -m "chore: remove stale build directory with leaked credentials"
```

---

## Task 2: Fix trace directory and file permissions

**Objective:** Make `~/.hermes/antigravity-traces/` directory 0o700 and trace files 0o600, matching the debug.py security discipline. Add trace file rotation (max 50 files).

**Risk:** MODERATE — touches `interceptor.py` which is in the runtime dependency chain.

**Files:**
- Modify: `antigravity_auth/interceptor.py` (lines 34-61, `_trace` function)
- Test: `antigravity_auth/test_interceptor.py`

**Step 1: Write failing test for trace file permissions**

Add to `antigravity_auth/test_interceptor.py`:

```python
class TestTracePermissions(unittest.TestCase):
    def test_trace_directory_is_private(self):
        """Trace directory must be created with 0o700 permissions."""
        import tempfile
        import stat
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as tmp:
            trace_dir = Path(tmp) / "antigravity-traces"
            with patch("antigravity_auth.interceptor._TRACE_DIR", None):
                with patch("antigravity_auth.interceptor.get_hermes_home", return_value=Path(tmp)):
                    from antigravity_auth.interceptor import _trace
                    _trace("test-event", key="value")

            self.assertTrue(trace_dir.exists(), "trace directory was not created")
            mode = stat.S_IMODE(trace_dir.stat().st_mode)
            self.assertEqual(mode, 0o700, f"trace dir mode is {oct(mode)}, expected 0o700")

    def test_trace_file_is_private(self):
        """Trace files must be created with 0o600 permissions."""
        import tempfile
        import stat
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as tmp:
            trace_dir = Path(tmp) / "antigravity-traces"
            with patch("antigravity_auth.interceptor._TRACE_DIR", None):
                with patch("antigravity_auth.interceptor.get_hermes_home", return_value=Path(tmp)):
                    from antigravity_auth.interceptor import _trace
                    _trace("test-event", key="value")

            trace_files = list(trace_dir.iterdir())
            self.assertGreater(len(trace_files), 0, "no trace files created")
            for tf in trace_files:
                mode = stat.S_IMODE(tf.stat().st_mode)
                self.assertEqual(mode, 0o600, f"trace file {tf.name} mode is {oct(mode)}, expected 0o600")
```

**Step 2: Run test to verify failure**

```bash
python3 -m pytest antigravity_auth/test_interceptor.py::TestTracePermissions -v
```
Expected: FAIL — trace dir is 0o755, files are 0o644.

**Step 3: Fix the `_trace` function in interceptor.py**

Replace the `_trace` function (lines 34-61) with:

```python
def _trace(event: str, **kwargs: Any) -> None:
    """Write a trace marker to the interceptor debug log.

    Creates one file per Hermes process (keyed by PID) under
    ``~/.hermes/antigravity-traces/`` so you can verify whether the
    httpx event hooks fire and what decisions the hook makes.

    Trace files are created with private permissions (0o600) in a
    private directory (0o700), matching the debug.py security discipline.
    """
    global _TRACE_DIR
    import time as _time

    if _TRACE_DIR is None:
        try:
            from .storage import get_hermes_home
            _TRACE_DIR = get_hermes_home() / "antigravity-traces"
            _TRACE_DIR.mkdir(parents=True, exist_ok=True)
            try:
                os.chmod(_TRACE_DIR, 0o700)
            except Exception:
                pass
            _cleanup_old_traces(_TRACE_DIR, max_files=50)
        except Exception:
            return

    try:
        ts = _time.time()
        pid = os.getpid()
        trace_file = _TRACE_DIR / f"trace-{pid}.log"
        extra = " ".join(f"{k}={v}" for k, v in kwargs.items()) if kwargs else ""
        line = f"{ts:.3f} {event} {extra}\n"
        with open(trace_file, "a", opener=_private_trace_opener) as f:
            f.write(line)
    except Exception:
        pass


def _private_trace_opener(path: str, flags: int) -> int:
    """Open trace files with private permissions (0o600)."""
    return os.open(path, flags | os.O_CREAT, 0o600)


def _cleanup_old_traces(traces_dir: Any, max_files: int = 50) -> None:
    """Remove oldest trace files when count exceeds max_files."""
    try:
        from pathlib import Path
        traces_path = Path(traces_dir)
        if not traces_path.is_dir():
            return
        files = [
            f for f in traces_path.iterdir()
            if f.is_file() and f.name.startswith("trace-") and f.name.endswith(".log")
        ]
        if len(files) <= max_files:
            return
        sorted_files = sorted(files, key=lambda f: f.stat().st_mtime, reverse=True)
        for f in sorted_files[max_files:]:
            try:
                f.unlink()
            except Exception:
                pass
    except Exception:
        pass
```

**Step 4: Run test to verify pass**

```bash
python3 -m pytest antigravity_auth/test_interceptor.py::TestTracePermissions -v
```
Expected: PASS

**Step 5: Run full test suite**

```bash
python3 -m pytest antigravity_auth/ -v --tb=short 2>&1 | tail -5
```
Expected: All tests pass.

**Step 6: Manual import chain verification**

```bash
python3 -c "from antigravity_auth.interceptor import install, is_installed; print('interceptor OK')"
```
Expected: `interceptor OK`

**Step 7: Commit**

```bash
git add antigravity_auth/interceptor.py antigravity_auth/test_interceptor.py
git commit -m "fix: write antigravity trace files with private permissions and rotation"
```

---

## Task 3: Add `session_token` / `sessionToken` to redaction

**Objective:** Ensure fingerprint `sessionToken` values are redacted in debug logs and diagnostic output.

**Risk:** LOW — `redaction.py` is a utility module, not in the critical import chain.

**Files:**
- Modify: `antigravity_auth/redaction.py` (line 10, `_SECRET_KEY_FRAGMENTS`)
- Modify: `antigravity_auth/test_redaction.py`

**Step 1: Write failing test**

Add to `antigravity_auth/test_redaction.py`:

```python
class TestSessionTokenRedaction(unittest.TestCase):
    def test_session_token_snake_case_is_secret(self):
        from antigravity_auth.redaction import _is_secret_key
        self.assertTrue(_is_secret_key("session_token"))

    def test_sessionToken_camelCase_is_secret(self):
        from antigravity_auth.redaction import _is_secret_key
        self.assertTrue(_is_secret_key("sessionToken"))

    def test_device_session_token_is_secret(self):
        from antigravity_auth.redaction import _is_secret_key
        self.assertTrue(_is_secret_key("device_session_token"))

    def test_fingerprint_session_token_redacted_in_dict(self):
        from antigravity_auth.redaction import redact_secrets
        fp = {
            "deviceId": "abc-123",
            "sessionToken": "deadbeef1234567890abcdef",
            "userAgent": "Mozilla/5.0",
        }
        redacted = redact_secrets(fp)
        self.assertEqual(redacted["deviceId"], "abc-123")
        self.assertEqual(redacted["sessionToken"], "[REDACTED]")
        self.assertEqual(redacted["userAgent"], "Mozilla/5.0")
```

**Step 2: Run test to verify failure**

```bash
python3 -m pytest antigravity_auth/test_redaction.py::TestSessionTokenRedaction -v
```
Expected: FAIL — `_is_secret_key("session_token")` returns False.

**Step 3: Add session_token to _SECRET_KEY_FRAGMENTS**

In `antigravity_auth/redaction.py`, add `"session_token"` and `"sessiontoken"` to the `_SECRET_KEY_FRAGMENTS` tuple:

```python
_SECRET_KEY_FRAGMENTS = (
  "access_token",
  "accesstoken",
  "refresh_token",
  "refreshtoken",
  "id_token",
  "idtoken",
  "authorization",
  "client_secret",
  "clientsecret",
  "code_verifier",
  "codeverifier",
  "oauth_code",
  "oauthcode",
  "session_token",
  "sessiontoken",
)
```

**Step 4: Run test to verify pass**

```bash
python3 -m pytest antigravity_auth/test_redaction.py::TestSessionTokenRedaction -v
```
Expected: PASS

**Step 5: Run full test suite**

```bash
python3 -m pytest antigravity_auth/ -v --tb=short 2>&1 | tail -5
```
Expected: All tests pass.

**Step 6: Commit**

```bash
git add antigravity_auth/redaction.py antigravity_auth/test_redaction.py
git commit -m "fix: redact sessionToken values in debug logs and diagnostics"
```

---

## Task 4: Remove dead `_global_stream` hook

**Objective:** Remove the `_global_stream` override from the global httpx hook installation. It builds a request object and discards it, calling the original stream with the unmodified arguments. This provides false confidence that stream requests are intercepted by the global safety net.

**Risk:** MODERATE — touches `interceptor.py` in the runtime chain. The per-instance event hooks on `GeminiCloudCodeClient._http` still intercept stream requests; this is only removing a dead global fallback.

**Files:**
- Modify: `antigravity_auth/interceptor.py` (lines 1210-1226 in `_install_global_httpx_hook`)

**Step 1: Remove `_global_stream` from `_install_global_httpx_hook`**

Remove lines 1210-1226 (the `_original_client_stream` save, `_global_stream` definition, and `httpx.Client.stream` assignment). The function should end after the `httpx.Client.post = _global_post` assignment (line 1208), followed by the existing trace marker.

The resulting `_install_global_httpx_hook` function should look like:

```python
def _install_global_httpx_hook() -> None:
    """Monkey-patch httpx.Client.send and .post to catch every request.

    We've seen evidence that some code paths use httpx differently —
    possibly through subclasses that override send/post.  Patching both
    entry-points guarantees interception.
    """
    global _GLOBAL_HTTPX_HOOK_INSTALLED
    if _GLOBAL_HTTPX_HOOK_INSTALLED:
        return
    _GLOBAL_HTTPX_HOOK_INSTALLED = True

    # ── Level 1: override send (catches internal Client usage) ──
    _original_client_send = httpx.Client.send

    def _global_send(client_self, request, *args, **kwargs):
        _trace("global-send-called", url=str(request.url)[:120])
        try:
            _antigravity_request_hook(request)
        except Exception:
            pass
        response = _original_client_send(client_self, request, *args, **kwargs)
        try:
            _antigravity_response_hook(response)
        except Exception:
            pass
        return response

    httpx.Client.send = _global_send  # type: ignore[method-assign]

    # ── Level 2: override post — ensures post() routes through our overridden send() ──
    _original_client_post = httpx.Client.post

    def _global_post(client_self, url, *, json=None, content=None, data=None,
                     files=None, headers=None, params=None, **kwargs):
        request = client_self.build_request(
            "POST", url, json=json, content=content, data=data,
            files=files, headers=headers, params=params, **kwargs,
        )
        return client_self.send(request)

    httpx.Client.post = _global_post  # type: ignore[method-assign]
    _trace("global-httpx-hook-installed")
```

**Step 2: Run full test suite**

```bash
python3 -m pytest antigravity_auth/ -v --tb=short 2>&1 | tail -5
```
Expected: All tests pass.

**Step 3: Manual import chain verification**

```bash
python3 -c "from antigravity_auth.interceptor import install, is_installed; print('interceptor OK')"
```
Expected: `interceptor OK`

**Step 4: Commit**

```bash
git add antigravity_auth/interceptor.py
git commit -m "fix: remove dead _global_stream hook that discarded built request"
```

---

## Task 5: Add MANIFEST.in for defense-in-depth credential exclusion

**Objective:** Add a `MANIFEST.in` file that explicitly excludes `_credentials.py` from sdist builds, providing a second layer of protection beyond the `setup.py` packaging guard.

**Risk:** NONE — new file, no runtime impact.

**Files:**
- Create: `MANIFEST.in`

**Step 1: Create MANIFEST.in**

```
# Exclude local credentials from source distributions.
# Defense-in-depth: setup.py packaging_guard also blocks builds when
# antigravity_auth/_credentials.py exists, but this catches bypass paths.
exclude antigravity_auth/_credentials.py
```

**Step 2: Verify it parses**

```bash
python3 -c "
from setuptools.dist import Distribution
d = Distribution(attrs={'name': 'test'})
from setuptools.command.sdist import sdist
cmd = sdist(d)
cmd.ensure_finalized()
print('MANIFEST.in parsed OK')
"
```
Expected: `MANIFEST.in parsed OK`

**Step 3: Commit**

```bash
git add MANIFEST.in
git commit -m "chore: add MANIFEST.in to exclude _credentials.py from sdist"
```

---

## Task 6: Log a clear warning when account selection fails and auth is stripped

**Objective:** When `_select_request_account()` returns None and the Authorization header is deleted, emit a WARNING-level log that explains the situation to the user, rather than silently letting the request proceed unauthenticated.

**Risk:** MODERATE — touches `interceptor.py` request hook path.

**Files:**
- Modify: `antigravity_auth/interceptor.py` (lines 758-761)

**Step 1: Add warning log to account selection failure path**

Replace lines 758-761:

```python
    else:
        request.extensions["antigravity_account_selection_failed"] = True
        if "Authorization" in request.headers:
            del request.headers["Authorization"]
```

With:

```python
    else:
        request.extensions["antigravity_account_selection_failed"] = True
        logger.warning(
            "Antigravity account selection failed for model=%s; "
            "request will proceed without Authorization (expect 401). "
            "Run 'hermes antigravity login' to add accounts or check "
            "'hermes antigravity accounts' for account health.",
            model,
        )
        if "Authorization" in request.headers:
            del request.headers["Authorization"]
```

**Step 2: Run full test suite**

```bash
python3 -m pytest antigravity_auth/ -v --tb=short 2>&1 | tail -5
```
Expected: All tests pass.

**Step 3: Manual import chain verification**

```bash
python3 -c "from antigravity_auth.interceptor import install, is_installed; print('interceptor OK')"
```
Expected: `interceptor OK`

**Step 4: Commit**

```bash
git add antigravity_auth/interceptor.py
git commit -m "fix: log clear warning when antigravity account selection fails"
```

---

## Task 7: Extract shared `_decompress` to avoid duplication

**Objective:** Remove the duplicate `_decompress` function in `token.py` and `oauth.py` by having both import from a shared location.

**Risk:** LOW — `_decompress` is a simple utility. Both `token.py` and `oauth.py` are in the interceptor dependency chain but only the function reference changes, not its behavior.

**Files:**
- Create: `antigravity_auth/_http_utils.py`
- Modify: `antigravity_auth/token.py` (line 29-33, remove `_decompress`)
- Modify: `antigravity_auth/oauth.py` (lines 139-143, remove `_decompress`)

**Step 1: Create `_http_utils.py`**

```python
"""Shared HTTP utility helpers."""
from __future__ import annotations

import gzip


def decompress_response(body: bytes, response) -> bytes:
    """Decompress gzip-encoded HTTP response bodies."""
    encoding = response.headers.get("Content-Encoding", "")
    if "gzip" in encoding:
        return gzip.decompress(body)
    return body
```

**Step 2: Update token.py**

Remove the `_decompress` function (lines 29-33) and replace `import gzip` with:

```python
from ._http_utils import decompress_response as _decompress
```

(In the fallback import block, add the corresponding bare import:)

```python
from _http_utils import decompress_response as _decompress
```

Remove `import gzip` from the top-level imports in token.py (line 2).

**Step 3: Update oauth.py**

Remove the `_decompress` function (lines 139-143) and replace `import gzip` (line 9) with:

```python
from ._http_utils import decompress_response as _decompress
```

(In the fallback import block, add the corresponding bare import:)

```python
from _http_utils import decompress_response as _decompress
```

Remove `import gzip` from the top-level imports in oauth.py (line 9).

**Step 4: Run full test suite after each file change**

```bash
# After creating _http_utils.py:
python3 -c "from antigravity_auth._http_utils import decompress_response; print('OK')"

# After updating token.py:
python3 -m pytest antigravity_auth/test_token.py -v --tb=short
python3 -c "from antigravity_auth.token import refresh_access_token; print('token OK')"

# After updating oauth.py:
python3 -m pytest antigravity_auth/test_oauth.py -v --tb=short
python3 -c "from antigravity_auth.oauth import exchange_antigravity; print('oauth OK')"

# Full suite:
python3 -m pytest antigravity_auth/ -v --tb=short 2>&1 | tail -5
```
Expected: All pass.

**Step 5: Commit**

```bash
git add antigravity_auth/_http_utils.py antigravity_auth/token.py antigravity_auth/oauth.py
git commit -m "refactor: extract shared _decompress to _http_utils module"
```

---

## Task 8: Fix existing trace directory permissions on disk

**Objective:** Retroactively fix the permissions on the existing `~/.hermes/antigravity-traces/` directory and its files. This is a one-time runtime migration since Task 2 only fixes new file creation.

**Risk:** LOW — only touches `interceptor.py` at init time.

**Files:**
- Modify: `antigravity_auth/interceptor.py` (inside `_trace`, the `_TRACE_DIR is None` initialization block)

**Step 1: Add permission repair to trace dir initialization**

The `_TRACE_DIR is None` block in `_trace()` (from Task 2's implementation) already does `os.chmod(_TRACE_DIR, 0o700)`. Add file permission repair for existing trace files immediately after `_cleanup_old_traces`:

After the line `_cleanup_old_traces(_TRACE_DIR, max_files=50)`, add:

```python
            # Repair permissions on existing trace files created before
            # the 0o600 opener was added.
            try:
                for existing in _TRACE_DIR.iterdir():
                    if existing.is_file() and existing.name.startswith("trace-"):
                        try:
                            os.chmod(existing, 0o600)
                        except Exception:
                            pass
            except Exception:
                pass
```

**Step 2: Run full test suite**

```bash
python3 -m pytest antigravity_auth/ -v --tb=short 2>&1 | tail -5
```
Expected: All tests pass.

**Step 3: Manual import chain verification**

```bash
python3 -c "from antigravity_auth.interceptor import install, is_installed; print('interceptor OK')"
```
Expected: `interceptor OK`

**Step 4: Commit**

```bash
git add antigravity_auth/interceptor.py
git commit -m "fix: repair existing trace file permissions to 0o600 on startup"
```

---

## Task 9: Update ARCHITECTURE.md with security discipline notes

**Objective:** Document the trace file and debug log security discipline, and the credential file permissions expectations.

**Risk:** NONE — documentation only.

**Files:**
- Modify: `docs/ARCHITECTURE.md`

**Step 1: Add security discipline section**

After the "Debugging" section (line 269), before the "Troubleshooting" section, add:

```markdown
## Security Discipline

### File Permissions

All credential-bearing and diagnostic files use private permissions:

| Directory / File | Mode | Purpose |
|------------------|------|---------|
| `~/.hermes/antigravity-accounts.json` | 0o600 | OAuth refresh tokens, access tokens, fingerprints |
| `~/.hermes/auth.json` | 0o600 | Runtime auth state |
| `~/.hermes/logs/antigravity/` | 0o700 | Debug log directory |
| `~/.hermes/logs/antigravity/*.log` | 0o600 | Debug log files (25-file rotation) |
| `~/.hermes/antigravity-traces/` | 0o700 | Interceptor trace directory |
| `~/.hermes/antigravity-traces/*.log` | 0o600 | Trace files (50-file rotation) |

### Credential Packaging Guards

The `antigravity_auth/_credentials.py` file is gitignored and blocked from wheel/sdist builds by `setup.py`'s `packaging_guard` module. `MANIFEST.in` provides a second layer of exclusion for sdist builds.

### Redaction

`antigravity_auth/redaction.py` redacts OAuth tokens, bearer strings, session tokens, client secrets, and auth codes from debug output. Both snake_case and camelCase key variants are covered.

---
```

**Step 2: Commit**

```bash
git add docs/ARCHITECTURE.md
git commit -m "docs: add security discipline section to architecture guide"
```

---

## Task 10: Update AGENTS.md with review findings

**Objective:** Add a note about the security discipline and the trace file rotation to the root AGENTS.md so future agents are aware.

**Risk:** NONE — documentation only.

**Files:**
- Modify: `AGENTS.md`

**Step 1: Add security note to Key Design Patterns section**

After pattern 6 (Fingerprint System) in AGENTS.md, add:

```markdown
### 7. Security Discipline
Debug logs (0o700 dir, 0o600 files, 25-file rotation) and trace files (0o700 dir, 0o600 files, 50-file rotation) use private permissions. `redaction.py` strips OAuth tokens, bearer strings, session tokens, and client secrets from all diagnostic output. The `packaging_guard.py` module plus `MANIFEST.in` prevent `_credentials.py` from shipping in wheels/sdist.
```

**Step 2: Commit**

```bash
git add AGENTS.md
git commit -m "docs: document security discipline in AGENTS.md"
```

---

## Task 11: Push to GitHub

**Objective:** Push all committed changes to the remote repository.

**Risk:** NONE.

**Step 1: Verify all tests pass one final time**

```bash
python3 -m pytest antigravity_auth/ -v --tb=short 2>&1 | tail -5
```
Expected: All tests pass.

**Step 2: Verify git log looks correct**

```bash
git log --oneline -10
```
Expected: 10 new commits from Tasks 1-10.

**Step 3: Push**

```bash
git push origin main
```
Expected: Successful push.

---

## Summary

| Task | Severity | Risk | What |
|------|----------|------|------|
| 1 | HIGH | NONE | Remove build/ with leaked credentials |
| 2 | HIGH | MOD | Fix trace file permissions + add rotation |
| 3 | MEDIUM | LOW | Add sessionToken to redaction |
| 4 | MEDIUM | MOD | Remove dead _global_stream hook |
| 5 | MEDIUM | NONE | Add MANIFEST.in credential exclusion |
| 6 | MEDIUM | MOD | Log warning on auth selection failure |
| 7 | LOW | LOW | Extract shared _decompress |
| 8 | HIGH | LOW | Repair existing trace file permissions |
| 9 | — | NONE | Update ARCHITECTURE.md |
| 10 | — | NONE | Update AGENTS.md |
| 11 | — | NONE | Push to GitHub |

**Post-implementation:** After all tasks, run:
```bash
python3 -m pytest antigravity_auth/ -v
python3 -c "from antigravity_auth.interceptor import install, is_installed; print('interceptor OK')"
python3 -c "from antigravity_auth.redaction import _is_secret_key; assert _is_secret_key('sessionToken'); print('redaction OK')"
ls -la ~/.hermes/antigravity-traces/ | head -3  # verify 0o700 dir, 0o600 files
```
