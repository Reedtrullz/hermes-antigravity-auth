# Audit Fixes: Critical & High Remediation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.
> **Prior deployment verification:** 672 tests pass, 2 skipped. Clean git (0b0013f).

**Goal:** Fix 9 critical and 7 high-severity issues discovered in cross-domain audit of multi-account rotation, request/response transform pipeline, and security/auth subsystems.

**Architecture:** This is a plugin monkey-patching codebase. The interceptor (`interceptor.py`) is the runtime heartbeat — its response hooks handle 401/403/429/5xx events, and its request hook injects Antigravity headers. ALL response hooks are wrapped in `try/except` that silently swallow exceptions. Any broken import in the dependency chain of `accounts/manager.py`, `accounts/ratelimit.py`, or `accounts/rotation.py` produces NO crash — just silently disabled rate-limit rotation. Plan is ordered by risk: one-liners first, then single-file-at-a-time deployment through the dependency chain ordered from lowest-risk to highest-risk.

**Tech Stack:** Python 3.10+, stdlib-only, dataclasses, 2-space indent, double quotes, colocated tests

---

## Phase 0: Safety Rules (READ BEFORE EXECUTING ANY TASK)

### Dependency Chain (the heartbeat)

```
hermes_plugin.py:register()
  └─ try/except ← SILENT SWALLOW
       └─ interceptor.install()
            ├─ _antigravity_request_hook
            │    ├─ get_config() [config.py]
            │    └─ try/except: generate_fingerprint() [fingerprint.py] ← SILENT
            └─ _antigravity_response_hook
                 ├─ 401: try/except ← SILENT
                 ├─ 403: try/except ← SILENT
                 │    └─ AccountManager.load_from_disk() [accounts/manager.py]
                 │         └─ accounts/ratelimit.py, accounts/rotation.py, accounts/state.py
                 ├─ 429: try/except ← SILENT
                 │    └─ AccountManager.load_from_disk() [accounts/manager.py]
                 │    └─ mark_rate_limited() [accounts/ratelimit.py]
                 └─ 5xx: try/except ← SILENT
```

### Risk Levels for Files Touched

| File | Risk | Why |
|------|------|-----|
| `accounts/manager.py` | HIGH | In 403/429 hook chain; broken import = silent rate-limit disable |
| `accounts/ratelimit.py` | HIGH | Imported by manager; same silent-swallow chain |
| `accounts/rotation.py` | MODERATE | Imported by manager; HealthScoreTracker fail = scores stuck at 70 |
| `interceptor.py` | MODERATE | The patch target itself; install() returns bool |
| `storage.py` | LOW | chmod addition is non-breaking; file ops already try/except |
| `transform/response.py` | LOW | NOT in interceptor try/except chain |
| `oauth.py` | LOW | Only used during login flow, not hook chain |
| `hermes_provider_plugin.py` | MODERATE | Import-time side effect; change affects plugin load |
| `transform/messages.py` | LOW | Already imported; just changing interceptor to use existing function |

### Enforcement

- **Single-file-at-a-time**: NEVER batch changes to files in the dependency chain. One file → full test suite → manual import check → commit.
- **Manual import verification after every file touching the chain**:
  ```bash
  python3 -c "from antigravity_auth.accounts.manager import AccountManager; print('OK')"
  python3 -c "from antigravity_auth.accounts.ratelimit import mark_rate_limited; print('OK')"
  python3 -c "from antigravity_auth.interceptor import install; print('OK')"
  ```
- **Full test suite after every task**: `python3 -m pytest antigravity_auth/ -v --tb=short`
- **Architecture invariants preserved**: The interceptor is headers-only; must never mutate request bodies. Critical headers (Authorization, Content-Type, Host, Accept, Content-Length) must be preserved.

---

## Phase 1: Quick Wins — One-Liners (LOW risk, immediate impact)

### Task 1: Fix Claude model detection in interceptor

**Objective:** Make the interceptor use `is_claude_model()` instead of `model.startswith("claude")` so antigravity-prefixed Claude names work.

**Files:**
- Modify: `antigravity_auth/interceptor.py:309`

**Risk:** LOW — `is_claude_model` already imported in `transform/__init__.py`, already exported.

**Step 1: Verify current behavior**

```bash
python3 -c "
from antigravity_auth.transform import is_claude_model
# Current: startswith('claude') FAILS for these
print('antigravity-claude-sonnet-4-6:', is_claude_model('antigravity-claude-sonnet-4-6'))
print('claude-opus-4-6:', is_claude_model('claude-opus-4-6'))
"
```

Expected: `True` for both.

**Step 2: Run existing tests to establish baseline**

```bash
python3 -m pytest antigravity_auth/test_claude_transforms.py antigravity_auth/test_inject_tool_call_ids.py antigravity_auth/transform/ -v
```

Expected: All pass (verified: 304 tests in transform, plus interceptor tests).

**Step 3: Make the fix**

Change `interceptor.py:309` from:
```python
    if isinstance(inner_request, dict) and isinstance(model, str) and model.startswith("claude"):
```
To:
```python
    if isinstance(inner_request, dict) and isinstance(model, str) and "claude" in model.lower():
```

This matches the semantics of `is_claude_model()` from `transform/messages.py:8-9` without adding a new import to the interceptor.

**Step 4: Run full test suite**

```bash
python3 -m pytest antigravity_auth/ -v --tb=short
```

Expected: 672 passed, 2 skipped.

**Step 5: Manual import verification**

```bash
python3 -c "from antigravity_auth.interceptor import install; print('OK')"
```

**Step 6: Commit**

```bash
git add antigravity_auth/interceptor.py
git commit -m "fix: use substring match for Claude model detection in interceptor

model.startswith('claude') fails for antigravity-claude-* model names.
Changed to 'claude' in model.lower() to match is_claude_model() behavior."
```

---

### Task 2: Add chmod 0600 to token storage files

**Objective:** Set restrictive file permissions on `auth.json` and `antigravity-accounts.json` after writes to prevent local access token theft.

**Files:**
- Modify: `antigravity_auth/storage.py:114, 182`

**Risk:** LOW — `os.chmod` is idempotent and doesn't modify file contents. Existing tests don't check file permissions.

**Step 1: Add chmod to `save_accounts()`**

At `storage.py:114`, after `os.replace(tmp_path, path)`, add:
```python
            os.chmod(path, 0o600)
```

The full block becomes:
```python
    with _accounts_store_lock:
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(storage_dict, f, indent=2)
            os.replace(tmp_path, path)
            os.chmod(path, 0o600)
        except Exception as e:
```

**Step 2: Add chmod to `sync_token_to_auth_json()`**

At `storage.py:182`, after `os.replace(tmp_path, path)`, add:
```python
            os.chmod(path, 0o600)
```

The full block becomes:
```python
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            os.replace(tmp_path, path)
            os.chmod(path, 0o600)
        except Exception as e:
```

**Step 3: Also add chmod to `_write_accounts_file()` in manager.py**

`manager.py:64`, after `os.replace(temp_path, path)`:
```python
    os.replace(temp_path, path)
    os.chmod(path, 0o600)
```

This covers the third write path that currently has no permission hardening.

**Step 4: Run full test suite**

```bash
python3 -m pytest antigravity_auth/ -v --tb=short
```

Expected: 672 passed, 2 skipped.

**Step 5: Manual import verification**

```bash
python3 -c "from antigravity_auth.storage import save_accounts, sync_token_to_auth_json; print('OK')"
python3 -c "from antigravity_auth.accounts.manager import AccountManager; print('OK')"
```

**Step 6: Commit**

```bash
git add antigravity_auth/storage.py antigravity_auth/accounts/manager.py
git commit -m "fix: set chmod 0600 on token storage files after write

Token files (auth.json, antigravity-accounts.json) were world-readable
with default umask permissions. Now restricted to owner-only read/write."
```

---

### Task 3: Fix tool_result_missing recovery asymmetry in response handler

**Objective:** Make `response.py:_handle_error_response` return `recoveryType` for `tool_result_missing` errors, matching the behavior for `thinking_block_order`.

**Files:**
- Modify: `antigravity_auth/transform/response.py:363-366`

**Risk:** LOW — `recovery.py:detect_error_type` already detects this error type independently, so existing callers that use recovery.py are unaffected. This just adds the `recoveryType` field to the response handler's error dict so callers that only check `error["recoveryType"]` also get it.

**Step 1: Read current error detection code**

The current code at `response.py:363-366`:
```python
  if "tool_use" in msg_lower and "tool_result" in msg_lower and (
    "without" in msg_lower or "immediately after" in msg_lower
  ):
    extra_headers["x-antigravity-context-error"] = "tool_pairing"
```

It only sets a header, not a `recoveryType` in the error dict.

**Step 2: Modify to return recoveryType**

Change the block to:
```python
  if "tool_use" in msg_lower and "tool_result" in msg_lower and (
    "without" in msg_lower or "immediately after" in msg_lower
  ):
    extra_headers["x-antigravity-context-error"] = "tool_pairing"
    return (
      json.dumps(error_body),
      extra_headers or None,
      {"recoveryType": "tool_result_missing"},
    )
```

Note: Use `return` (not just assignment) because a `tool_result_missing` error is severe enough that retry info extraction below it is irrelevant.

**Step 3: Add test for the new behavior**

In `antigravity_auth/transform/test_response.py`, add:

```python
def test_handle_error_tool_result_missing(self):
    from antigravity_auth.transform.response import _handle_error_response
    error_body = {
        "error": {
            "code": 400,
            "message": "tool_use without immediately after tool_result"
        }
    }
    body, headers, recovery = _handle_error_response(json.dumps(error_body).encode(), 400)
    self.assertEqual(recovery, {"recoveryType": "tool_result_missing"})
    self.assertEqual(headers.get("x-antigravity-context-error"), "tool_pairing")
```

**Step 4: Run tests**

```bash
python3 -m pytest antigravity_auth/transform/test_response.py -v
python3 -m pytest antigravity_auth/ -v --tb=short
```

Expected: All pass.

**Step 5: Commit**

```bash
git add antigravity_auth/transform/response.py antigravity_auth/transform/test_response.py
git commit -m "fix: return recoveryType for tool_result_missing errors in response handler

_response_error_handler now detects tool_result_missing errors and returns
{'recoveryType': 'tool_result_missing'}, matching the thinking_block_order
behavior. Previously only 'tool_pairing' header was set."
```

---

## Phase 2: Account Rotation Hardening (ordered by risk: LOW → MODERATE → HIGH)

### Task 4: Reconnect HealthScoreTracker to AccountManager

**Objective:** Wire `AccountManager.mark_rate_limited_with_reason()` and `mark_request_success()` to call `HealthScoreTracker` methods so health scores actually change instead of staying at 70 forever. Single-file change (manager.py only).

**Files:**
- Modify: `antigravity_auth/accounts/manager.py:354-370`

**Risk:** MODERATE — touches `manager.py` which is in the 403/429 hook dependency chain. But the change is purely additive (new method calls inside existing methods, no import changes).

**Step 1: Read current code**

At `manager.py:354-366`, `mark_rate_limited_with_reason()` currently only delegates to `mark_rate_limited_with_reason()` from ratelimit.py:

```python
  def mark_rate_limited_with_reason(
    self,
    account: ManagedAccount,
    family: ModelFamily,
    header_style: HeaderStyle,
    model: str | None,
    reason: str,
    retry_after_ms: float | None = None,
    failure_ttl_ms: float = 3600_000,
  ) -> int:
    return mark_rate_limited_with_reason(
      account, family, header_style, model, reason, retry_after_ms, failure_ttl_ms,
    )
```

**Step 2: Add health tracker calls**

Add `self._health_tracker.record_rate_limit(account.index)` after the ratelimit call:

```python
    result = mark_rate_limited_with_reason(
      account, family, header_style, model, reason, retry_after_ms, failure_ttl_ms,
    )
    self._health_tracker.record_rate_limit(account.index)
    return result
```

**Step 3: Wire `mark_request_success()`**

At `manager.py:368-370`:

```python
  def mark_request_success(self, account: ManagedAccount) -> None:
    if account.consecutive_failures:
      account.consecutive_failures = 0
```

Add health tracker call:

```python
  def mark_request_success(self, account: ManagedAccount) -> None:
    if account.consecutive_failures:
      account.consecutive_failures = 0
      self._health_tracker.record_success(account.index)
```

**Step 4: Verify HealthScoreTracker API exists**

The tracker already has `record_rate_limit(account_index: int)` and `record_success(account_index: int)` — no changes to rotation.py needed.

**Step 5: Run full test suite**

```bash
python3 -m pytest antigravity_auth/accounts/ -v
```

Expected: All pass. Existing tests for manager don't memoize health scores.

**Step 6: Manual import verification (CRITICAL — manager.py is in the hook chain)**

```bash
python3 -c "from antigravity_auth.accounts.manager import AccountManager; print('OK')"
python3 -c "from antigravity_auth.accounts.ratelimit import mark_rate_limited; print('OK')"
```

**Step 7: Commit**

```bash
git add antigravity_auth/accounts/manager.py
git commit -m "fix: wire HealthScoreTracker into AccountManager rate-limit/success calls

mark_rate_limited_with_reason() now calls health_tracker.record_rate_limit()
and mark_request_success() now calls health_tracker.record_success().
Health scores were previously initialized but never updated — always 70."
```

---

### Task 5: Fix inconsistent rate limit filtering between strategies

**Objective:** Make `_get_next_for_family()` (used by sticky/round-robin) use `is_rate_limited_for_family()` instead of `is_rate_limited_for_header_style()` so filtering is consistent with `_select_hybrid()`. For gemini accounts, this means an account is only excluded when BOTH quota pools are rate-limited, not just one.

**Files:**
- Modify: `antigravity_auth/accounts/manager.py:284`

**Risk:** HIGH — changes account filtering logic in the sticky/round-robin path which is the default strategy. Must verify with tests.

**Step 1: Understand the current inconsistency**

- `_get_next_for_family()` at line 284: `is_rate_limited_for_header_style(...)` — checks ONE pool (antigravity OR gemini-cli)
- `_select_hybrid()` at line 310: `is_rate_limited_for_family(...)` — checks BOTH pools for gemini

This means an account rate-limited on only ONE gemini pool is excluded in sticky/round-robin but included in hybrid.

**Step 2: Read the `header_style` parameter usage**

In `_get_next_for_family`, the `header_style` parameter is passed ONLY to `is_rate_limited_for_header_style` and `get_quota_key`. The `_mark_touched_for_quota` call also uses `quota_key` derived from header_style. We need to be careful: if we switch to `is_rate_limited_for_family`, we lose the header_style discrimination for claude (which only has one pool, so it's equivalent). For gemini, `is_rate_limited_for_family` checks BOTH pools.

**Step 3: Apply the fix**

Change line 284 from:
```python
      and not is_rate_limited_for_header_style(a.rate_limit_reset_times, family, header_style, model)
```
To:
```python
      and not is_rate_limited_for_family(a.rate_limit_reset_times, family, model)
```

This makes sticky/round-robin use the same filtering as hybrid. The `header_style` parameter is still used for `get_quota_key` (line 223 in the caller), which determines which quota cache key to mark as touched — that's separate from the rate-limit check.

**Step 4: Update test expectations**

Check `antigravity_auth/accounts/test_manager.py` for any tests that rely on the old single-pool filtering behavior. Search for `is_rate_limited_for_header_style` in test files:

```bash
python3 -c "
from antigravity_auth.accounts.manager import AccountManager
from antigravity_auth.accounts.state import ManagedAccount, RefreshParts, RateLimitState
import time
now = time.time() * 1000

# Create account with only gemini-antigravity pool rate-limited
a = ManagedAccount(
    index=0,
    refresh_parts=RefreshParts(refresh_token='test'),
    email='test@test.com',
    rate_limit_reset_times=RateLimitState(gemini_antigravity=now + 60000, gemini_cli=None),
)
mgr = AccountManager()
# Directly set internal state
mgr._accounts = [a]
mgr._current_account_by_family['gemini'] = 0

# Before: is_rate_limited_for_header_style('gemini', 'antigravity') = True → excluded
# After:  is_rate_limited_for_family('gemini') — gemini_cli is None → not both limited → still available
from antigravity_auth.accounts.ratelimit import is_rate_limited_for_header_style, is_rate_limited_for_family
print('header_style check:', is_rate_limited_for_header_style(a.rate_limit_reset_times, 'gemini', 'antigravity'))
print('family check:', is_rate_limited_for_family(a.rate_limit_reset_times, 'gemini'))
"
```

Expected output: `header_style check: True`, `family check: False` — confirming the fix changes behavior.

**Step 5: Run full test suite**

```bash
python3 -m pytest antigravity_auth/ -v --tb=short
```

**Step 6: Manual import verification**

```bash
python3 -c "from antigravity_auth.accounts.manager import AccountManager; print('OK')"
```

**Step 7: Commit**

```bash
git add antigravity_auth/accounts/manager.py
git commit -m "fix: use consistent rate limit filtering across all strategies

_get_next_for_family() (sticky/round-robin) now uses is_rate_limited_for_family
instead of is_rate_limited_for_header_style, matching _select_hybrid() behavior.
Gemini accounts are now only excluded when BOTH quota pools are rate-limited,
not just one — consistent across all three selection strategies."
```

---

### Task 6: Persist cursor and remove unbounded growth

**Objective:** Save `self._cursor` to disk in `save_to_disk()` and restore it in `_load_from_stored()`, then cap the increment to prevent unbounded integer growth.

**Files:**
- Modify: `antigravity_auth/accounts/manager.py:156, 294, 473-476`

**Risk:** HIGH — changes serialization format (adds new field). Must ensure backward compatibility with existing accounts files.

**Step 1: Save cursor in `save_to_disk()`**

Change line 473-476 from:
```python
    storage = {
      "version": 4,
      "accounts": accounts_data,
      "activeIndex": claude_index,
```
To:
```python
    storage = {
      "version": 4,
      "accounts": accounts_data,
      "activeIndex": self._cursor,
```

The `activeIndex` field was previously hardcoded to `claude_index` which was wrong — it should represent the round-robin cursor. Now it correctly stores the cursor value.

**Step 2: Restore cursor in `_load_from_stored()`**

At line 156, the cursor is already restored:
```python
    self._cursor = _clamp_non_negative_int(stored.get("activeIndex", 0), 0)
```

This already works correctly — no change needed. The fix is only in the save side.

**Step 3: Cap cursor to prevent unbounded growth**

Change line 293-294 from:
```python
    idx = self._cursor % len(available)
    self._cursor += 1
```
To:
```python
    idx = self._cursor % len(available)
    self._cursor = (self._cursor + 1) % 1_000_000
```

This caps the cursor at 1M, effectively a ring buffer. After 1M selections it wraps around — modulo still works correctly because `_cursor % len(available)` is computed first.

**Step 4: Run full test suite**

```bash
python3 -m pytest antigravity_auth/ -v --tb=short
```

**Step 5: Manual import verification**

```bash
python3 -c "from antigravity_auth.accounts.manager import AccountManager; mgr = AccountManager(); mgr.save_to_disk(); print('OK')"
```

**Step 6: Commit**

```bash
git add antigravity_auth/accounts/manager.py
git commit -m "fix: persist cursor in accounts storage and cap growth

save_to_disk() now writes the actual cursor value instead of claude_index.
Cursor is capped at 1M to prevent unbounded integer growth in long-running
processes. load_from_disk() already read activeIndex as cursor — no change needed."
```

---

### Task 7: Add lock protection to AccountManager state mutations

**Objective:** Protect all reads/writes of `_accounts`, `_cursor`, `_current_account_by_family` with `self._lock` so concurrent threads don't corrupt state.

**Files:**
- Modify: `antigravity_auth/accounts/manager.py:251-271, 273-295, 297-330, 332-340, 344-366, 388-428`

**Risk:** HIGH — adds lock acquisition to every method that touches mutable state. Must verify no nested lock acquisitions (deadlock risk). The debounce timer's `_do_save` callback (line 491-495) calls `save_to_disk()` which calls `_write_accounts_file()` — neither currently holds the lock. The PID offset block already holds the lock at line 241. So no nesting risk.

**Step 1: Audit for nested lock patterns**

The lock is currently acquired in:
- `get_current_or_next_for_family()` at line 241-249 (PID offset block)
- `_request_save_to_disk()` at line 485-500

Functions called from WITHIN lock-held regions:
- Line 247: `self._current_account_by_family[family] = new_index` — simple assignment, no lock needed
- Line 492: `self.save_to_disk()` — called from debounce timer thread, NOT holding lock

Functions that call INTO lock-held regions:
- None call `_request_save_to_disk()` from inside a lock-held region
- None call `get_current_or_next_for_family()` with `pid_offset_enabled=True` from inside a lock-held region

No deadlock risk. Proceed.

**Step 2: Add lock to `get_current_or_next_for_family()` sticky path**

Wrap lines 251-271 in a lock:

```python
    # Sticky (default) strategy
    if pid_offset_enabled and len(self._accounts) > 1:
        with self._lock:
            if not self._session_offset_applied.get(family, False):
                import os as _os
                pid = _os.getpid()
                pid_offset = pid % len(self._accounts)
                base_index = self._current_account_by_family.get(family, 0)
                new_index = (base_index + pid_offset) % len(self._accounts)
                self._current_account_by_family[family] = new_index
                self._session_offset_applied[family] = True

    with self._lock:
        current = self.get_current_account_for_family(family)
```

And the return paths need to be inside the lock or the values snapshotted. Since we're returning `ManagedAccount` references (not primitives), and Python's GIL protects individual operations, the lock is mainly needed for the filtering/selection logic:

```python
    with self._lock:
        current = self.get_current_account_for_family(family)
        if current:
          clear_expired_rate_limits(current.rate_limit_reset_times)
          is_limited = is_rate_limited_for_family(
            current.rate_limit_reset_times, family, model
          )
          is_over = self._is_over_soft_quota(
            current, family, soft_quota_threshold_percent, soft_quota_cache_ttl_ms, model
          )
          if not is_limited and not is_over and not is_account_cooling_down(current):
            self._mark_touched_for_quota(current, quota_key)
            return current

        next_acc = self._get_next_for_family_locked(
          family, model, header_style,
          soft_quota_threshold_percent, soft_quota_cache_ttl_ms,
        )
        if next_acc:
          self._mark_touched_for_quota(next_acc, quota_key)
          self._current_account_by_family[family] = next_acc.index
        return next_acc
```

Wait — this is getting complex because `_get_next_for_family` has its own lock needs. Let me simplify: the real risk is two threads both reading `self._cursor` at the same time. The simplest fix is to wrap the state mutation in the cursor in `_get_next_for_family` and the family-index mutation in the caller.

Actually, let me reconsider. The interceptor currently creates a NEW AccountManager per response hook. Thread-safety only matters if/when we move to a singleton (Task 9). But Tasks 7 and 9 are independent — let me do the lock protection NOW so it's safe when we introduce the singleton later.

**Simplified approach**: Add a single `with self._lock` around the cursor increment and selection in `_get_next_for_family`, and around the family-index mutation in `get_current_or_next_for_family` and `_select_hybrid`.

**Step 2: Protect `_get_next_for_family()`**

Wrap lines 281-295 (the critical section that reads `_cursor` and mutates it):

```python
  def _get_next_for_family(
    self,
    family: ModelFamily,
    model: str | None = None,
    header_style: HeaderStyle = "antigravity",
    soft_quota_threshold_percent: float = 100,
    soft_quota_cache_ttl_ms: float = 600_000,
  ) -> ManagedAccount | None:
    available = [
      a for a in self._accounts
      if a.enabled is not False
      and not is_rate_limited_for_family(a.rate_limit_reset_times, family, model)
      and not self._is_over_soft_quota(a, family, soft_quota_threshold_percent,
                                        soft_quota_cache_ttl_ms, model)
      and not is_account_cooling_down(a)
    ]

    if not available:
      return None

    with self._lock:
      idx = self._cursor % len(available)
      self._cursor = (self._cursor + 1) % 1_000_000
    return available[idx]
```

The list comprehension is read-only on `self._accounts` and the account fields — the `with self._lock` only protects the cursor mutation which is the main race condition vector.

**Step 3: Protect `_select_hybrid()` family-index mutation**

Wrap line 329:
```python
    with self._lock:
      self._current_account_by_family[family] = selected.index
```

**Step 4: Protect `get_current_or_next_for_family()` sticky path**

Wrap lines 260-270 (the `if not is_limited... return current` and fallthrough):
```python
    current = self.get_current_account_for_family(family)
    if current:
      clear_expired_rate_limits(current.rate_limit_reset_times)
      is_limited = is_rate_limited_for_family(
        current.rate_limit_reset_times, family, model
      )
      is_over = self._is_over_soft_quota(
        current, family, soft_quota_threshold_percent, soft_quota_cache_ttl_ms, model
      )
      if not is_limited and not is_over and not is_account_cooling_down(current):
        self._mark_touched_for_quota(current, quota_key)
        with self._lock:
          pass  # no mutation needed for sticky if current is good
        return current
```

Actually this is getting too invasive. Let me reconsider the approach.

The key insight: the MAIN risk is in `_get_next_for_family` where two threads read `self._cursor` simultaneously and both get the same value. The SECONDARY risk is two threads writing to `_current_account_by_family` simultaneously.

Simplest correct approach: ONLY add lock to `_get_next_for_family` cursor mutation, and to `_current_account_by_family` writes. Don't restructure the existing code flow.

```python
  def _get_next_for_family(
    self,
    family: ModelFamily,
    model: str | None = None,
    header_style: HeaderStyle = "antigravity",
    soft_quota_threshold_percent: float = 100,
    soft_quota_cache_ttl_ms: float = 600_000,
  ) -> ManagedAccount | None:
    available = [
      a for a in self._accounts
      if a.enabled is not False
      and not is_rate_limited_for_family(a.rate_limit_reset_times, family, model)
      and not self._is_over_soft_quota(a, family, soft_quota_threshold_percent,
                                        soft_quota_cache_ttl_ms, model)
      and not is_account_cooling_down(a)
    ]

    if not available:
      return None

    with self._lock:
      idx = self._cursor % len(available)
      self._cursor = (self._cursor + 1) % 1_000_000
    return available[idx]
```

And in `get_current_or_next_for_family` at line 270:
```python
    if next_acc:
      self._mark_touched_for_quota(next_acc, quota_key)
      with self._lock:
        self._current_account_by_family[family] = next_acc.index
```

And in `_select_hybrid` at line 329:
```python
    selected = candidates[0]
    if selected:
      selected.last_used = now_ms()
      self._mark_touched_for_quota(selected, quota_key)
      with self._lock:
        self._current_account_by_family[family] = selected.index
```

And in `mark_switched` at line 340:
```python
  def mark_switched(self, account: ManagedAccount, reason: str, family: ModelFamily) -> None:
    account.last_switch_reason = reason
    with self._lock:
      self._current_account_by_family[family] = account.index
```

And in `set_account_enabled` at line 402:
```python
          with self._lock:
            self._current_account_by_family[family] = next_acc.index if next_acc else -1
```

And in `remove_account` at lines 418-427:
```python
    with self._lock:
      if self._cursor > account_index:
        self._cursor -= 1
      self._cursor = self._cursor % len(self._accounts)
      for family in ("claude", "gemini"):
        idx = self._current_account_by_family.get(family, 0)
        if idx > account_index:
          idx -= 1
        if idx >= len(self._accounts):
          idx = -1
        self._current_account_by_family[family] = idx
```

**Step 2: Apply all lock additions**

Make the 6 changes described above to manager.py.

**Step 3: Run full test suite**

```bash
python3 -m pytest antigravity_auth/ -v --tb=short
```

**Step 4: Deadlock check — verify no nested lock acquisitions**

Run:
```bash
python3 -m pytest antigravity_auth/accounts/ -v --tb=long -x
```

Expected: No hangs, all pass.

**Step 5: Manual import verification**

```bash
python3 -c "from antigravity_auth.accounts.manager import AccountManager; print('OK')"
```

**Step 6: Commit**

```bash
git add antigravity_auth/accounts/manager.py
git commit -m "fix: add lock protection to AccountManager state mutations

Cursor and _current_account_by_family mutations are now protected by
self._lock. Prevents race conditions where two concurrent threads read
the same cursor value or overwrite family-to-account mappings."
```

---

### Task 8: Handle all-accounts-exhausted case

**Objective:** When `get_current_or_next_for_family()` returns `None` (all accounts rate-limited), log a warning and clear expired rate limits so the caller can retry. Also add a minimum-viable wait time so callers can implement backoff.

**Files:**
- Modify: `antigravity_auth/accounts/manager.py:290-291`
- Modify: `antigravity_auth/interceptor.py:258-259`

**Risk:** MODERATE — changes behavior when all accounts are exhausted. Currently silent no-op; now logs and attempts recovery.

**Step 1: Add logging and recovery attempt in manager**

At `manager.py:290-291`, change:
```python
    if not available:
      return None
```
To:
```python
    if not available:
      import logging
      _logger = logging.getLogger(__name__)
      _logger.warning("All %d accounts are currently rate-limited or cooling down for family=%s",
                      len(self._accounts), family)
      # Clear expired limits as a recovery attempt — they may have just expired
      for a in self._accounts:
        if a.enabled is not False:
          clear_expired_rate_limits(a.rate_limit_reset_times)
      return None
```

**Step 2: Add logging in interceptor 429 handler**

At `interceptor.py:258`, change:
```python
                next_acc = mgr.get_current_or_next_for_family("gemini", strategy="hybrid")
                if next_acc and next_acc.index != active.index:
```
To:
```python
                next_acc = mgr.get_current_or_next_for_family("gemini", strategy="hybrid")
                if next_acc is None:
                    logger.warning("All gemini accounts exhausted — cannot rotate after rate limit")
                elif next_acc.index != active.index:
```

**Step 3: Run full test suite**

```bash
python3 -m pytest antigravity_auth/ -v --tb=short
```

**Step 4: Manual import verification**

```bash
python3 -c "from antigravity_auth.accounts.manager import AccountManager; print('OK')"
python3 -c "from antigravity_auth.interceptor import install; print('OK')"
```

**Step 5: Commit**

```bash
git add antigravity_auth/accounts/manager.py antigravity_auth/interceptor.py
git commit -m "fix: log warning when all accounts exhausted instead of silent no-op

When all accounts are rate-limited or cooling down, _get_next_for_family now
logs a warning and clears expired rate limits as a recovery attempt. The
interceptor's 429 handler now explicitly logs when rotation fails."
```

---

### Task 9: Share AccountManager as singleton between interceptor and rest of system

**Objective:** Replace `AccountManager.load_from_disk()` calls in the interceptor's 403/429 handlers with a shared singleton instance so rate-limit state, cursor position, and health scores are consistent between the response hooks and any other code using the AccountManager.

**Files:**
- Create: `antigravity_auth/accounts/shared.py` (singleton holder)
- Modify: `antigravity_auth/accounts/manager.py` (add `get_global_manager()` function)
- Modify: `antigravity_auth/interceptor.py:221, 247` (use shared instance)
- Modify: `antigravity_auth/hermes_plugin.py` (initialize on load)

**Risk:** HIGH — changes how the interceptor accesses AccountManager. Must ensure the singleton is initialized before the first HTTP request and that reload works correctly.

**Step 1: Create the shared singleton holder**

Create `antigravity_auth/accounts/shared.py`:
```python
"""Shared singleton AccountManager for cross-module state consistency."""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .manager import AccountManager

_lock = threading.Lock()
_instance: AccountManager | None = None


def set_global_manager(manager: AccountManager) -> None:
    """Set the shared AccountManager singleton."""
    global _instance
    with _lock:
        _instance = manager


def get_global_manager() -> AccountManager | None:
    """Get the shared AccountManager singleton, or None if not initialized."""
    with _lock:
        return _instance


def get_or_create_global_manager() -> AccountManager:
    """Get or lazily create the shared AccountManager singleton."""
    global _instance
    with _lock:
        if _instance is None:
            from .manager import AccountManager
            _instance = AccountManager.load_from_disk()
        return _instance
```

**Step 2: Expose from manager.py**

Add to `manager.py` (at end of file):
```python
# Re-export for convenience
from .shared import get_global_manager, set_global_manager, get_or_create_global_manager
```

**Step 3: Update interceptor to use shared instance**

In `interceptor.py:220-221` (403 handler), change:
```python
            from .accounts.manager import AccountManager
            mgr = AccountManager.load_from_disk()
```
To:
```python
            from .accounts.manager import get_or_create_global_manager
            mgr = get_or_create_global_manager()
```

In `interceptor.py:245-247` (429 handler), change:
```python
            from .accounts.manager import AccountManager
            from .accounts.ratelimit import mark_rate_limited
            mgr = AccountManager.load_from_disk()
```
To:
```python
            from .accounts.manager import get_or_create_global_manager
            from .accounts.ratelimit import mark_rate_limited
            mgr = get_or_create_global_manager()
```

After `mgr.save_to_disk()` calls in both handlers, the shared instance is updated in memory AND on disk — no more state blindness.

**Step 4: Initialize on plugin load**

In `hermes_plugin.py`, after `install_interceptor()` succeeds, add:
```python
    # Initialize shared AccountManager so interceptor hooks share state
    try:
        from .accounts.shared import get_or_create_global_manager
        get_or_create_global_manager()
    except Exception:
        pass
```

**Step 5: Run full test suite**

```bash
python3 -m pytest antigravity_auth/ -v --tb=short
```

**Step 6: Manual import verification**

```bash
python3 -c "from antigravity_auth.accounts.shared import get_or_create_global_manager; mgr = get_or_create_global_manager(); print('OK:', type(mgr).__name__)"
python3 -c "from antigravity_auth.interceptor import install; print('OK')"
python3 -c "from antigravity_auth.accounts.manager import AccountManager, get_global_manager; print('OK')"
```

**Step 7: Commit**

```bash
git add antigravity_auth/accounts/shared.py antigravity_auth/accounts/manager.py antigravity_auth/interceptor.py antigravity_auth/hermes_plugin.py
git commit -m "fix: share AccountManager singleton between interceptor hooks and system

Creates accounts/shared.py with thread-safe singleton accessor. The interceptor's
403/429 handlers now use get_or_create_global_manager() instead of creating
isolated AccountManager instances from disk. This eliminates state blindness
between response hooks and the rest of the system — rate limit state, cursor
position, and health scores are now consistent."
```

---

### Task 10: Unify disk save/load through single path

**Objective:** Make `AccountManager.save_to_disk()` use `storage.save_accounts()` instead of `_write_accounts_file()` so there's a single authoritative save path with consistent locking.

**Files:**
- Modify: `antigravity_auth/accounts/manager.py:433-482`

**Risk:** HIGH — changes serialization path. Must ensure storage format compatibility.

**Step 1: Verify format compatibility**

`AccountManager.save_to_disk()` builds:
```python
{
    "version": 4,
    "accounts": [...],
    "activeIndex": <cursor>,
    "activeIndexByFamily": {"claude": ..., "gemini": ...},
}
```

`storage.save_accounts()` accepts an arbitrary `storage_dict` and writes it as-is. The format is identical — manager produces the same structure that storage expects.

`storage.load_accounts()` returns the same dict structure that `_read_accounts_file()` returns, and `_load_from_stored()` parses from that dict. So the read path is already compatible.

**Step 2: Change `save_to_disk()` to use `storage.save_accounts()`**

Replace the body of `save_to_disk()` (lines 433-482) to use `storage.save_accounts()`:

```python
  def save_to_disk(self) -> bool:
    claude_index = max(0, self._current_account_by_family.get("claude", 0))
    gemini_index = max(0, self._current_account_by_family.get("gemini", 0))

    accounts_data: list[dict[str, Any]] = []
    for a in self._accounts:
      acc_dict: dict[str, Any] = {
        "email": a.email,
        "refreshToken": a.refresh_parts.refresh_token,
        "projectId": a.refresh_parts.project_id,
        "managedProjectId": a.refresh_parts.managed_project_id,
        "addedAt": a.added_at,
        "lastUsed": a.last_used,
        "enabled": a.enabled,
        "lastSwitchReason": a.last_switch_reason,
      }

      rl_dict = a.rate_limit_reset_times.to_dict()
      if rl_dict:
        acc_dict["rateLimitResetTimes"] = rl_dict

      if a.cooling_down_until is not None:
        acc_dict["coolingDownUntil"] = a.cooling_down_until
        acc_dict["cooldownReason"] = a.cooldown_reason

      if a.fingerprint:
        acc_dict["fingerprint"] = a.fingerprint
      if a.fingerprint_history:
        acc_dict["fingerprintHistory"] = a.fingerprint_history
      if a.cached_quota:
        acc_dict["cachedQuota"] = a.cached_quota
        acc_dict["cachedQuotaUpdatedAt"] = a.cached_quota_updated_at
      if a.verification_required:
        acc_dict["verificationRequired"] = True
        acc_dict["verificationRequiredAt"] = a.verification_required_at
        acc_dict["verificationRequiredReason"] = a.verification_required_reason
        acc_dict["verificationUrl"] = a.verification_url

      accounts_data.append(acc_dict)

    storage_dict = {
      "version": 4,
      "accounts": accounts_data,
      "activeIndex": self._cursor,
      "activeIndexByFamily": {
        "claude": claude_index,
        "gemini": gemini_index,
      },
    }

    from ..storage import save_accounts
    try:
      save_accounts(storage_dict)
      return True
    except Exception:
      return False
```

**Step 3: Remove dead `_read_accounts_file` and `_write_accounts_file`**

These functions at lines 44-72 are now only used by `load_from_disk()`. Change `load_from_disk()` to use `storage.load_accounts()`:

```python
  @classmethod
  def load_from_disk(cls) -> AccountManager:
    """Load accounts from the accounts storage file."""
    manager = cls()
    from ..storage import load_accounts
    stored = load_accounts()
    if not stored or not stored.get("accounts"):
      return manager
    manager._load_from_stored(stored)
    return manager
```

Then remove `_read_accounts_file()` and `_write_accounts_file()` functions (lines 38-72) since they're now dead code.

**Step 4: Run full test suite**

```bash
python3 -m pytest antigravity_auth/ -v --tb=short
```

**Step 5: Verify disk format**

```bash
python3 -c "
from antigravity_auth.accounts.manager import AccountManager
from antigravity_auth.accounts.state import ManagedAccount, RefreshParts, RateLimitState
import time

mgr = AccountManager()
a = ManagedAccount(
    index=0,
    refresh_parts=RefreshParts(refresh_token='test_rt'),
    email='test@test.com',
    rate_limit_reset_times=RateLimitState(gemini_antigravity=time.time()*1000 + 60000),
)
mgr._accounts = [a]
mgr._current_account_by_family['gemini'] = 0
mgr.save_to_disk()

# Verify it loads back
mgr2 = AccountManager.load_from_disk()
assert len(mgr2._accounts) == 1
assert mgr2._accounts[0].email == 'test@test.com'
print('Round-trip OK')
"
```

**Step 6: Manual import verification**

```bash
python3 -c "from antigravity_auth.accounts.manager import AccountManager; mgr = AccountManager.load_from_disk(); print('OK')"
python3 -c "from antigravity_auth.storage import save_accounts, load_accounts; print('OK')"
```

**Step 7: Also add chmod to storage.save_accounts**

Storage.save_accounts already got chmod in Task 2. Verify it's still there.

**Step 8: Commit**

```bash
git add antigravity_auth/accounts/manager.py
git commit -m "refactor: unify disk I/O through storage.py single path

AccountManager.save_to_disk() now delegates to storage.save_accounts() and
load_from_disk() uses storage.load_accounts(). Removes the duplicate
_read_accounts_file/_write_accounts_file functions. Single authoritative
save/load path with consistent locking."
```

---

## Phase 3: Security Hardening

### Task 11: Separate PKCE verifier from OAuth state parameter

**Objective:** Instead of embedding the raw PKCE verifier in the browser-visible `state` parameter, generate a random state ID, store the verifier in an in-memory dict keyed by that ID, and pass only the state ID in the URL.

**Files:**
- Modify: `antigravity_auth/oauth.py:46-97`
- Modify: `antigravity_auth/cli.py:111-135` (callback handler)

**Risk:** MODERATE — changes OAuth flow. Must maintain backward compatibility with existing login sessions (though login is short-lived, so this is acceptable).

**Step 1: Add in-memory verifier store to oauth.py**

Add at module level:
```python
import secrets

# In-memory PKCE verifier store: state_id -> {"verifier": str, "projectId": str}
# Cleared after exchange. Keys are random, never exposed to browser.
_pkce_verifier_store: dict[str, dict[str, str]] = {}
```

**Step 2: Modify `generate_pkce_auth_url()`**

Change line 84-96 from:
```python
    params = {
        "client_id": ANTIGRAVITY_CLIENT_ID,
        ...
        "state": encode_state({"verifier": pkce["verifier"], "projectId": project_id or ""}),
        ...
    }
```
To:
```python
    state_id = secrets.token_urlsafe(32)
    _pkce_verifier_store[state_id] = {
        "verifier": pkce["verifier"],
        "projectId": project_id or "",
    }
    
    params = {
        "client_id": ANTIGRAVITY_CLIENT_ID,
        ...
        "state": encode_state({"id": state_id}),
        ...
    }
```

**Step 3: Add function to retrieve verifier by state ID**

```python
def get_pkce_verifier(state_id: str) -> dict[str, str] | None:
    """Retrieve and consume the PKCE verifier for a state ID."""
    return _pkce_verifier_store.pop(state_id, None)
```

**Step 4: Update callback handler in cli.py**

In `cli.py` where the callback handler calls `decode_state` and extracts the verifier:

Before:
```python
state_data = decode_state(params.get("state", ""))
verifier = state_data.get("verifier", "")
```

After:
```python
from ..oauth import get_pkce_verifier
state_data = decode_state(params.get("state", ""))
state_id = state_data.get("id", "")
pkce_data = get_pkce_verifier(state_id) if state_id else None
verifier = pkce_data.get("verifier", "") if pkce_data else ""
```

**Step 5: Run full test suite**

```bash
python3 -m pytest antigravity_auth/ -v --tb=short
```

**Step 6: Commit**

```bash
git add antigravity_auth/oauth.py antigravity_auth/cli.py
git commit -m "fix: separate PKCE verifier from OAuth state parameter

PKCE verifier is now stored server-side (in-memory dict keyed by random
state ID) instead of being embedded in the browser-visible state parameter.
Prevents verifier exposure through browser history and proxy logs."
```

---

### Task 12: Add uninstall() to interceptor

**Objective:** Add an `uninstall()` function that restores the original `GeminiCloudCodeClient.__init__` and `wrap_code_assist_request`, preventing patch chains on plugin reload.

**Files:**
- Modify: `antigravity_auth/interceptor.py:322` (after install())

**Risk:** MODERATE — adds new public function. Must guard against double-uninstall.

**Step 1: Add `uninstall()` function**

Add after line 322:
```python
def uninstall() -> bool:
  """Restore original GeminiCloudCodeClient.__init__ and wrap_code_assist_request.

  Returns True if successfully uninstalled, False if not installed."""
  global _PATCHED, _ORIGINAL_INIT, _ORIGINAL_WRAP_CODE_ASSIST
  if not _PATCHED:
    return False
  try:
    from agent.gemini_cloudcode_adapter import GeminiCloudCodeClient
    import agent.gemini_cloudcode_adapter as gca
    if _ORIGINAL_INIT is not None:
      GeminiCloudCodeClient.__init__ = _ORIGINAL_INIT
    if _ORIGINAL_WRAP_CODE_ASSIST is not None:
      gca.wrap_code_assist_request = _ORIGINAL_WRAP_CODE_ASSIST
    _PATCHED = False
    _ORIGINAL_INIT = None
    _ORIGINAL_WRAP_CODE_ASSIST = None
    logger.info("Antigravity interceptor uninstalled")
    return True
  except Exception as e:
    logger.warning("Failed to uninstall interceptor: %s", e)
    return False
```

**Step 2: Add guard in install() against double-patch**

In `install()`, add a check that `_ORIGINAL_INIT` hasn't already been replaced by a prior patch:

```python
  _ORIGINAL_INIT = GeminiCloudCodeClient.__init__
  # Guard: if already patched by another plugin, don't chain
  if getattr(_ORIGINAL_INIT, '__name__', '') == '_patched_init':
    logger.warning("Interceptor already patched — skipping install")
    return False
```

This prevents `_ORIGINAL_INIT` from capturing a previously-patched version.

**Step 3: Run full test suite**

```bash
python3 -m pytest antigravity_auth/ -v --tb=short
```

**Step 4: Test install/uninstall cycle**

```bash
python3 -c "
from antigravity_auth.interceptor import install, uninstall, is_installed
# Skip if not in Hermes runtime (ImportError is expected outside Hermes)
try:
    result = install()
    print('install:', result)
    print('is_installed:', is_installed())
    result2 = uninstall()
    print('uninstall:', result2)
    print('is_installed:', is_installed())
except ImportError as e:
    print('Not in Hermes runtime — skipping integration test:', e)
"
```

**Step 5: Commit**

```bash
git add antigravity_auth/interceptor.py
git commit -m "fix: add uninstall() to interceptor with double-patch guard

uninstall() restores original GeminiCloudCodeClient.__init__ and
wrap_code_assist_request. install() now guards against double-patching
(prevents _ORIGINAL_INIT from capturing a previously-patched version)."
```

---

### Task 13: Move credential injection from import-time to explicit call

**Objective:** Change `hermes_provider_plugin.py` so `_set_oauth_env_from_credentials()` is called explicitly during plugin registration rather than at module import time, preventing credential leakage through `os.environ` on import.

**Files:**
- Modify: `antigravity_auth/hermes_provider_plugin.py:15-33`

**Risk:** MODERATE — changes plugin initialization timing. Must ensure credentials are set before the first API call.

**Step 1: Read current code**

```python
def _set_oauth_env_from_credentials() -> None:
    """..."""
    ...

_set_oauth_env_from_credentials()  # ← module-level call
```

**Step 2: Remove module-level call, make it explicit**

Remove line 30 (`_set_oauth_env_from_credentials()`) and add a `register()` function that Hermes calls:

```python
def register_provider(ctx) -> None:
    """Register the Antigravity provider profile with Hermes.
    
    Called by Hermes plugin loader. Sets OAuth credentials from _credentials.py
    into environment for the google-gemini-cli transport.
    """
    _set_oauth_env_from_credentials()
    ctx.register_provider(PROVIDER_PROFILE)
```

**Step 3: Verify the entry point wiring**

Check that `hermes_plugin.py` or `setup.py` calls `register_provider()` at the right time. The exact wiring depends on how Hermes loads provider plugins — check the existing plugin registration pattern.

**Step 4: Run full test suite**

```bash
python3 -m pytest antigravity_auth/ -v --tb=short
```

**Step 5: Commit**

```bash
git add antigravity_auth/hermes_provider_plugin.py
git commit -m "fix: move credential env injection from import-time to explicit register()

_set_oauth_env_from_credentials() no longer runs at module import time.
Instead it runs explicitly during provider registration. Prevents credential
leakage through os.environ when the module is imported for other purposes."
```

---

## Phase 4: Medium/Low Fixes (can be batched)

### Task 14: Fix medium-severity issues

**Objective:** Address M1-M7: debug log token sanitization, endpoint failure TTL, PII logging, phone-home opt-in, temp file naming, SSE multi-line parsing.

**Files:**
- Modify: `antigravity_auth/debug.py:228-284`
- Modify: `antigravity_auth/endpoints.py:31-33`
- Modify: `antigravity_auth/token_watchdog.py:85`
- Modify: `antigravity_auth/version.py:73`
- Modify: `antigravity_auth/storage.py:108, 144`
- Modify: `antigravity_auth/transform/response.py:176-196`

**Risk:** LOW — no interceptor dependency chain impact.

**Step 1: Sanitize tokens in debug log bodies (M1)**

In `debug.py`, add a `_sanitize_body()` function that redacts `access_token` and `refresh_token` values from JSON bodies before logging:

```python
import re

def _sanitize_body(body: str) -> str:
    """Redact access_token/refresh_token values from debug log bodies."""
    body = re.sub(r'"access_token"\s*:\s*"[^"]+"', '"access_token":"[REDACTED]"', body)
    body = re.sub(r'"refresh_token"\s*:\s*"[^"]+"', '"refresh_token":"[REDACTED]"', body)
    body = re.sub(r'"id_token"\s*:\s*"[^"]+"', '"id_token":"[REDACTED]"', body)
    return body
```

Call `_sanitize_body()` before logging in `start_antigravity_debug_request()` and `log_antigravity_debug_response()`.

**Step 2: Add TTL to endpoint failure marks (M2)**

In `endpoints.py`, add a `_FAILURE_TTL_SECONDS = 300` and in `is_endpoint_failed()`, check if the failure mark has expired:

```python
_FAILURE_TTL_SECONDS = 300
_failed_endpoints: dict[str, float] = {}

def mark_endpoint_failed(endpoint: str) -> None:
    import time
    _failed_endpoints[endpoint] = time.time()

def is_endpoint_failed(endpoint: str) -> bool:
    import time
    mark_time = _failed_endpoints.get(endpoint)
    if mark_time is None:
        return False
    if time.time() - mark_time > _FAILURE_TTL_SECONDS:
        _failed_endpoints.pop(endpoint, None)
        return False
    return True
```

**Step 3: Downgrade PII log to DEBUG (M3)**

In `token_watchdog.py:85`, change `logger.info(...)` to:
```python
logger.debug("Proactively refreshed token for %s", acc.get("email", "unknown"))
```

**Step 4: Add opt-in flag for version check (M4)**

In `version.py`, add an env var check before phoning home:
```python
if not os.environ.get("HERMES_ANTIGRAVITY_VERSION_CHECK", "1") == "1":
    logger.info("Version check disabled via HERMES_ANTIGRAVITY_VERSION_CHECK=0")
    return
```

**Step 5: Add random suffix to temp file names (M6)**

In `storage.py:108` and `storage.py:144`, add a random component:
```python
import secrets
tmp_path = path.with_suffix(f".json.{os.getpid()}.{secrets.token_hex(4)}.tmp")
```

**Step 6: Fix SSE multi-line data parsing (M7)**

In `response.py:_extract_usage_from_sse_payload`, accumulate multi-line data blocks. Replace the single-line check with a multiline accumulator:

```python
def _extract_usage_from_sse_payload(body: str) -> dict[str, Any] | None:
    if not isinstance(body, str):
        return None
    lines = body.split("\n")
    current_data: list[str] = []
    for line in lines:
        if line.startswith("data: "):
            current_data.append(line[6:])
        elif line == "" and current_data:
            # Blank line = end of SSE block
            data_str = "".join(current_data)
            try:
                parsed = json.loads(data_str)
            except json.JSONDecodeError:
                current_data = []
                continue
            usage = _extract_from_parsed(parsed)
            if usage:
                return usage
            current_data = []
    return None
```

**Step 7: Run full test suite**

```bash
python3 -m pytest antigravity_auth/ -v --tb=short
```

**Step 8: Commit**

```bash
git add antigravity_auth/debug.py antigravity_auth/endpoints.py antigravity_auth/token_watchdog.py antigravity_auth/version.py antigravity_auth/storage.py antigravity_auth/transform/response.py
git commit -m "fix: address medium-severity audit findings

- Debug logs now redact access_token/refresh_token values (M1)
- Endpoint failure marks expire after 5-minute TTL (M2)
- Token watchdog PII downgraded from INFO to DEBUG (M3)
- Version check respects HERMES_ANTIGRAVITY_VERSION_CHECK=0 (M4)
- Temp file names include random 8-hex suffix (M6)
- SSE usage extraction handles multi-line data blocks (M7)"
```

---

## Post-Implementation Verification

After all phases complete:

```bash
# Full test suite
python3 -m pytest antigravity_auth/ -v --tb=short

# Manual import chain verification
python3 -c "
from antigravity_auth.interceptor import install, uninstall, is_installed; print('interceptor OK')
from antigravity_auth.accounts.manager import AccountManager; print('manager OK')
from antigravity_auth.accounts.shared import get_or_create_global_manager; print('shared OK')
from antigravity_auth.accounts.ratelimit import mark_rate_limited; print('ratelimit OK')
from antigravity_auth.storage import save_accounts, load_accounts; print('storage OK')
"

# Verify token files have 0600 permissions (after creating them)
python3 -c "
from antigravity_auth.storage import save_accounts, sync_token_to_auth_json
save_accounts({'version': 4, 'accounts': [], 'activeIndex': 0})
import os, stat
path = os.path.expanduser('~/.hermes/antigravity-accounts.json')
mode = os.stat(path).st_mode
expected = stat.S_IRUSR | stat.S_IWUSR
assert mode & 0o777 == expected, f'Expected 0600, got {oct(mode)}'
print('Permissions OK:', oct(mode))
"
```

---

## Deferred: Fingerprint Regeneration (C3)

Fingerprint regeneration on capacity exhaustion requires a new feature: detecting model-capacity-exhausted errors in the response hook, generating a fresh device fingerprint for the affected account, persisting it to the account's `fingerprint` field, and tracking history in `fingerprint_history` (max 5 entries, per `MAX_FINGERPRINT_HISTORY = 5`). This is ~8-12 tasks and should be its own plan. The existing code generates a random fingerprint on every request, which works but isn't per-account. A separate plan should:

1. Detect `model_capacity_exhausted` error in response hook
2. Generate new fingerprint and store on account
3. Push old fingerprint to history (cap at 5)
4. Use stored fingerprint in request hook instead of generating new each time
5. Fall back to random if no fingerprint exists
