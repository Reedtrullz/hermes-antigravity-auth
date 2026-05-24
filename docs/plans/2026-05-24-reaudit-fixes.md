# Re-Audit Regression Fixes

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.
> **Prior state:** 675 tests pass, clean git at 36f7470. 13 commits on main.

**Goal:** Fix 3 regressions found in post-fix re-audit: (1) activeIndex semantics hijack breaking 5 callers, (2) inconsistent rate-limit filter in sticky path, (3) missing exhaustion log in 403 handler.

**Architecture:** Task 6 repurposed `activeIndex` from "account index" to "cursor" in the JSON storage. The fix adds a separate `cursor` field and restores `activeIndex` to its original account-index semantics. All other consumers (interceptor 401, token_watchdog, tools, cli) are untouched — they keep reading `activeIndex` as account index. Two additional one-liners fix the sticky filter inconsistency and the missing 403 log.

**Tech Stack:** Python 3.10+, stdlib-only, dataclasses, 2-space indent, double quotes, colocated tests.

---

## Phase 0: Safety Rules

### Dependency Chain (the heartbeat)

```
hermes_plugin.py:register()
  └─ try/except ← SILENT SWALLOW
       └─ interceptor.install()
            └─ _antigravity_response_hook
                 ├─ 401: try/except ← SILENT (uses load_accounts() raw)
                 ├─ 403: try/except ← SILENT (uses get_or_create_global_manager)
                 ├─ 429: try/except ← SILENT (uses get_or_create_global_manager)
                 └─ 5xx: try/except ← SILENT
```

### Files Touched & Risk

| Task | File | Risk | Why |
|------|------|------|-----|
| R1 | `accounts/manager.py` | HIGH | In 403/429 hook chain; broken import = silent rate-limit disable |
| R2 | `accounts/manager.py` | HIGH | Same file, one-line change to line 225 |
| R3 | `interceptor.py` | MODERATE | The patch target itself; one-line addition |

### Enforcement

- Single-file-at-a-time deployment
- Full test suite + manual import check after each task
- Tasks R1+R2 can be combined (same file, same risk level)

---

### Task R1: Fix activeIndex semantics — add cursor field, restore account index

**Objective:** Add a `cursor` field to the accounts JSON storage and restore `activeIndex` to store the claude family account index (as it did before Task 6). AccountManager reads/writes both fields. All other consumers of `activeIndex` (interceptor 401, token_watchdog, tools, cli) use it correctly as account index — zero changes needed.

**Files:**
- Modify: `antigravity_auth/accounts/manager.py:127, 461-469`

**Background:** Before Task 6, `activeIndex` stored `claude_index` (a valid account index, always 0..N-1). Task 6 changed it to `self._cursor` (a round-robin counter, can be 0..999999). Five callers outside AccountManager read `activeIndex` expecting a valid account index. The guard `0 <= idx < len(accounts)` prevents crashes but silently no-ops when the cursor value exceeds the account count.

**Design:** The `cursor` field is new and optional — old files without it default to 0 on load. `activeIndex` is restored to `claude_index` on save and read as cursor on load (backward compatible with pre-Task-6 files, but handle the case where it may be out of range).

**Step 1: Modify `save_to_disk()` — write both fields**

At `manager.py:461-469`, add `cursor` alongside the restored `activeIndex`:

```python
    storage_dict = {
      "version": 4,
      "accounts": accounts_data,
      "activeIndex": claude_index,
      "cursor": self._cursor,
      "activeIndexByFamily": {
        "claude": claude_index,
        "gemini": gemini_index,
      },
    }
```

Change `"activeIndex": self._cursor` → `"activeIndex": claude_index` (restore original), add `"cursor": self._cursor`.

**Step 2: Modify `_load_from_stored()` — read cursor from new field, with backward compat**

At `manager.py:127`, change:
```python
    self._cursor = _clamp_non_negative_int(stored.get("activeIndex", 0), 0)
```
To:
```python
    # Read cursor from dedicated field (added May 2026), fall back to activeIndex
    # for backward compatibility with files written before the cursor field existed.
    if "cursor" in stored:
      self._cursor = _clamp_non_negative_int(stored.get("cursor", 0), 0)
    else:
      self._cursor = _clamp_non_negative_int(stored.get("activeIndex", 0), 0)
```

**Step 3: Update test_manager.py fixtures — add `cursor` field**

Find all test fixtures that write stored dicts with `"activeIndex": 0` (the test_manager.py matches from the grep). Add `"cursor": 0` to each. These are the test fixtures that simulate persisted storage — they need the new field for round-trip fidelity.

The test fixtures are at lines 79, 97, 117, 135, 164, 188, 210, 231, 249, 276. Add `"cursor": 0,` after `"activeIndex": 0,` on each.

**Step 4: Run full test suite**

```bash
python3.10 -m pytest antigravity_auth/ -q --tb=short
```

Expected: 675 passed.

**Step 5: Manual import verification**

```bash
python3.10 -c "from antigravity_auth.accounts.manager import AccountManager; print('OK')"
```

**Step 6: Verify round-trip**

```bash
python3.10 -c "
from antigravity_auth.accounts.manager import AccountManager
from antigravity_auth.accounts.state import ManagedAccount, RefreshParts, RateLimitState
import time, os, json
# Create temp storage
from antigravity_auth.storage import get_accounts_json_path, save_accounts
path = get_accounts_json_path()

# Create manager with one account
mgr = AccountManager()
a = ManagedAccount(index=0, refresh_parts=RefreshParts(refresh_token='test_rt'), email='test@test.com',
    rate_limit_reset_times=RateLimitState(gemini_antigravity=time.time()*1000+60000))
mgr._accounts = [a]
mgr._current_account_by_family['gemini'] = 0
mgr._cursor = 42  # simulate cursor advance

# Save and reload
mgr.save_to_disk()
mgr2 = AccountManager.load_from_disk()

# Verify cursor survived round-trip
assert mgr2._cursor == 42, f'Cursor lost: {mgr2._cursor}'
# Verify activeIndex is still 0 (claude_index)
with open(path) as f:
    raw = json.load(f)
assert raw['activeIndex'] == 0, f'activeIndex corrupted: {raw[\"activeIndex\"]}'
assert raw['cursor'] == 42, f'cursor field missing: {raw.get(\"cursor\")}'
print('Round-trip OK: cursor=%d activeIndex=%d accounts=%d' % (mgr2._cursor, raw['activeIndex'], len(mgr2._accounts)))

# Verify old file (no cursor field) still loads
old_data = {'version': 4, 'accounts': raw['accounts'], 'activeIndex': 0, 'activeIndexByFamily': {'claude': 0, 'gemini': 0}}
save_accounts(old_data)
mgr3 = AccountManager.load_from_disk()
assert mgr3._cursor == 0, f'Backward compat broken: cursor={mgr3._cursor}'
print('Backward compat OK')
"
```

**Step 7: Commit**

```bash
git add antigravity_auth/accounts/manager.py antigravity_auth/accounts/test_manager.py
git commit -m "fix: add cursor field to storage, restore activeIndex as account index

Task 6 repurposed activeIndex from account index to cursor, breaking 5 callers
that read it expecting a valid account index (interceptor 401, token_watchdog,
tools, cli). Added a dedicated cursor field and restored activeIndex to store
the claude family index. Backward compatible with old files."
```

---

### Task R2: Fix sticky path rate-limit filter inconsistency

**Objective:** Change the sticky strategy's current-account check at line 225 to use `is_rate_limited_for_family` (both-pools) instead of `is_rate_limited_for_header_style` (single-pool), matching the fallthrough path at line 256.

**Files:**
- Modify: `antigravity_auth/accounts/manager.py:225-227`

**Step 1: Apply the fix**

Change lines 225-227 from:
```python
      is_limited = is_rate_limited_for_header_style(
        current.rate_limit_reset_times, family, header_style, model
      )
```
To:
```python
      is_limited = is_rate_limited_for_family(
        current.rate_limit_reset_times, family, model
      )
```

**Step 2: Run tests**

```bash
python3.10 -m pytest antigravity_auth/accounts/ -q --tb=short
```

Expected: 169 passed.

**Step 3: Manual import verification**

```bash
python3.10 -c "from antigravity_auth.accounts.manager import AccountManager; print('OK')"
```

**Step 4: Commit**

```bash
git add antigravity_auth/accounts/manager.py
git commit -m "fix: use consistent rate-limit filter in sticky current-account check

The sticky strategy's current-account check now uses is_rate_limited_for_family
(both-pools for gemini) instead of is_rate_limited_for_header_style
(single-pool), matching the fallthrough _get_next_for_family path."
```

---

### Task R3: Add exhaustion log to 403 handler

**Objective:** Add the same `elif next_acc is None` exhaustion warning to the 403 handler that the 429 handler got in Task 8.

**Files:**
- Modify: `antigravity_auth/interceptor.py:228-229`

**Step 1: Apply the fix**

Change lines 228-229 from:
```python
                next_acc = mgr.get_current_or_next_for_family("gemini", strategy="hybrid")
                if next_acc and next_acc.index != active.index:
```
To:
```python
                next_acc = mgr.get_current_or_next_for_family("gemini", strategy="hybrid")
                if next_acc is None:
                    logger.warning("All gemini accounts exhausted — cannot rotate after 403")
                elif next_acc.index != active.index:
```

**Step 2: Run full test suite**

```bash
python3.10 -m pytest antigravity_auth/ -q --tb=short
```

Expected: 675 passed.

**Step 3: Manual import verification**

```bash
python3.10 -c "from antigravity_auth.interceptor import install; print('OK')"
```

**Step 4: Commit**

```bash
git add antigravity_auth/interceptor.py
git commit -m "fix: add exhaustion log to 403 handler

Matches the 429 handler pattern: logs a warning when all gemini accounts
are exhausted and rotation after 403 is impossible."
```

---

## Post-Implementation Verification

```bash
# Full test suite
python3.10 -m pytest antigravity_auth/ -q --tb=short

# Import chain
python3.10 -c "
from antigravity_auth.accounts.manager import AccountManager; print('manager OK')
from antigravity_auth.interceptor import install; print('interceptor OK')
"

# Verify activeIndex consumers can read it correctly
python3.10 -c "
from antigravity_auth.storage import load_accounts
# Create a fresh storage file via AccountManager (which writes both cursor and activeIndex)
from antigravity_auth.accounts.manager import AccountManager
from antigravity_auth.accounts.state import ManagedAccount, RefreshParts, RateLimitState
import time
mgr = AccountManager()
a = ManagedAccount(index=0, refresh_parts=RefreshParts(refresh_token='test'), email='test@test.com',
    rate_limit_reset_times=RateLimitState())
mgr._accounts = [a]
mgr._current_account_by_family['gemini'] = 0
mgr._cursor = 99
mgr.save_to_disk()

# Now simulate what the 401 handler does
d = load_accounts()
idx = d.get('activeIndex', 0)
accs = d.get('accounts', [])
assert 0 <= idx < len(accs), f'activeIndex {idx} is not a valid account index!'
print('401 handler compat OK: activeIndex=%d accounts=%d cursor=%d' % (idx, len(accs), d.get('cursor', -1)))
"
```
