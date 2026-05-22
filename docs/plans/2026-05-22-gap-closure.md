# Gap Closure Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Close every gap (known and unknown) identified in the v1.6.0 codebase review — hygiene bugs, runtime bugs, 17 untested modules, code quality issues, and missing documentation.

**Architecture:** Six phases executed sequentially. Each phase is self-contained and produces independently verifiable results. Phase 1 (hygiene) must complete first because it touches .gitignore which affects all subsequent phases.

**Tech Stack:** Python 3.11+, pytest, unittest, git, pyproject.toml

**Safe-first principle:** The interceptor (`interceptor.py`) is the heartbeat of the plugin. Its response hook lazy-imports `AccountManager` from `accounts.manager` and `mark_rate_limited` from `accounts.ratelimit`. These are wrapped in `try/except` — a broken import SILENTLY disables rate-limit rotation. No crash, no error, just mysterious 429s. Every task that touches the accounts subsystem must be verified by running the full test suite AND a manual import chain check. No exceptions.

**Architecture constraints from docs:**
- The interceptor is **headers-only** — it MUST NOT mutate request bodies
- Critical headers preserved: `Authorization`, `Content-Type`, `Host`, `Accept`, `Content-Length`
- `hermes_provider_plugin.py` uses module-level side effects (`register_provider()`, `_patch_hermes_model_picker()`) — the `import *` in the plugin wrapper is CORRECT and intentional
- The API spec lists `autopush` endpoint as "unavailable" but `constants.py` still references it — `select_endpoint()` ignores the fallback chain anyway (always returns PROD)

**Orphaned modules discovered via Round 1 audit:**
- `verification.py` — `verify_account_access()` never called at runtime. Wire into `cli.py::check_quotas_and_verify()` using the already-refreshed token (do NOT call `probe_account_health()` which does a duplicate `refresh_access_token()` — the Antigravity API rate-limits aggressively)
- `transform/__init__.py` exports `clean_json_schema` and `to_gemini_schema` — no runtime code in this repo imports them. If Hermes itself imports them externally, that's fine; if not, these are dead exports.

---

## Phase 1: Hygiene (git, LICENSE, version consistency)

### Task 1: Bump requires-python to >=3.11

**Objective:** Align pyproject.toml with the actual minimum Python version required by `datetime.fromisoformat()` Z-suffix support and AGENTS.md claim.

**Files:**
- Modify: `pyproject.toml:11`

**Step 1: Edit requires-python**

```toml
requires-python = ">=3.11"
```

**Step 2: Verify metadata parses**

Run: `python3 -c "import tomllib; d=tomllib.load(open('pyproject.toml','rb')); print(d['project']['requires-python'])"`
Expected: `>=3.11`

**Step 3: Commit**

```bash
git add pyproject.toml
git commit -m "fix: bump requires-python to >=3.11 to match actual usage"
```

---

### Task 2: Add MIT LICENSE file

**Objective:** The README says MIT but no LICENSE file exists — this is a legal requirement.

**Files:**
- Create: `LICENSE`

**Step 1: Write LICENSE file**

Run this to get the current year and write the standard MIT license:

```bash
python3 -c "
year = '2025-2026'
text = f'''MIT License

Copyright (c) {year} NoeFabris & Reedtrullz

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the \"Software\"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED \"AS IS\", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
'''
with open('LICENSE', 'w') as f:
    f.write(text)
print('LICENSE written')
"
```

**Step 2: Verify**

Run: `ls -la LICENSE`
Expected: file exists, ~1KB

**Step 3: Commit**

```bash
git add LICENSE
git commit -m "docs: add MIT LICENSE file"
```

---

### Task 3: Clean .DS_Store and __pycache__ from git tracking

**Objective:** Remove macOS and Python cache artifacts committed to the repo.

**Files:**
- Delete from git: `.DS_Store`, `antigravity_auth/.DS_Store`, `.pytest_cache/.DS_Store`
- Delete from git: `plugins/model-providers/antigravity/__pycache__/`, `plugins/antigravity_tools/__pycache__/`

**Step 1: Remove tracked files and add to .gitignore**

```bash
git rm --cached .DS_Store antigravity_auth/.DS_Store .pytest_cache/.DS_Store 2>/dev/null
git rm -r --cached plugins/model-providers/antigravity/__pycache__/ 2>/dev/null
git rm -r --cached plugins/antigravity_tools/__pycache__/ 2>/dev/null
```

**Step 2: Verify**

Run: `git status`
Expected: the above files shown as deleted (staged)

**Step 3: Commit**

```bash
git add -u
git commit -m "chore: remove .DS_Store and __pycache__ from git tracking"
```

---

### Task 4: Add missing .gitignore entries

**Objective:** Ensure .DS_Store and __pycache__ won't be re-committed.

**Files:**
- Modify: `.gitignore` (create if missing)

**Step 1: Read current .gitignore**

```bash
cat .gitignore 2>/dev/null || echo "(no .gitignore)"
```

**Step 2: Append missing entries**

```bash
cat >> .gitignore << 'EOF'
# macOS
.DS_Store

# Python
__pycache__/
*.pyc
*.pyo
EOF
```

**Step 3: Verify entries are not duplicated**

Run: `sort .gitignore | uniq -d`
Expected: no output (no duplicates)

Run: `grep -c '.DS_Store' .gitignore`
Expected: `1`

**Step 4: Commit**

```bash
git add .gitignore
git commit -m "chore: add .DS_Store and __pycache__ to .gitignore"
```

---

## Phase 2: Runtime Bugs

### Task 5: Fix token_watchdog.py log message bug

**Objective:** Line 100 logs `_watchdog_thread.name` ("antigravity-token-watchdog") instead of the actual interval.

**Files:**
- Modify: `antigravity_auth/token_watchdog.py:100-101`

**Step 1: Read the current code**

Read lines 98-101 of `antigravity_auth/token_watchdog.py` to confirm the bug.

**Step 2: Fix the log line**

Replace:
```python
    logger.info("Token watchdog started (interval=%ss)", 
                _watchdog_thread.name)
```
With:
```python
    logger.info("Token watchdog started (interval=%ss)", check_interval)
```

But `check_interval` is in local scope of `_watchdog_loop`. The simplest fix: hardcode the default or log after the first config read.

Better: pass the config interval explicitly from `start_watchdog`:

In `start_watchdog()`, after line 96:
```python
    from .config import DEFAULT_CONFIG
    interval = DEFAULT_CONFIG.proactive_refresh_check_interval_seconds
```

Then change line 100-101 to:
```python
    logger.info("Token watchdog started (interval=%ss)", interval)
```

**Step 3: Verify the fix**

Run: `python3 -c "from antigravity_auth.token_watchdog import start_watchdog; start_watchdog()"`
Expected: no crash, log message should contain the interval number (300) not "antigravity-token-watchdog"

**Step 4: Commit**

```bash
git add antigravity_auth/token_watchdog.py
git commit -m "fix: log correct interval in token watchdog startup message"
```

---

### Task 6: Document the dual token-sync architecture

**Objective:** Two token sync paths exist (`auth.json` and `auth/google_oauth.json`). This is by design but undocumented. Add a comment block explaining the dual-store architecture.

**Files:**
- Modify: `antigravity_auth/storage.py` (add docstring to `sync_token_to_auth_json`)
- Modify: `antigravity_auth/cli.py` (add docstring to `sync_token_to_google_oauth`)

**Step 1: Add architecture comment to storage.py**

At the `sync_token_to_auth_json` function (line 123), prepend to the existing docstring:

```python
  """
  Dual-store architecture: Hermes v0.14 routes Cloud Code requests through
  agent.google_oauth which reads auth/google_oauth.json, while the Antigravity
  CLI and plugin manage auth.json. This function writes to auth.json.
  Use sync_token_to_google_oauth() in cli.py for the google_oauth.json store.
  
  Updates or inserts the 'antigravity' key in auth.json provider list.
  ...
  """
```

**Step 2: Add architecture comment to cli.py**

At the `sync_token_to_google_oauth` function (line 137), prepend:

```python
    """Dual-store architecture: writes to auth/google_oauth.json (Hermes runtime).
    
    This is the companion to storage.sync_token_to_auth_json (writes auth.json).
    Both must be called when switching the active account to prevent the two
    stores from diverging.
    """
```

**Step 3: Commit**

```bash
git add antigravity_auth/storage.py antigravity_auth/cli.py
git commit -m "docs: document dual token-sync architecture (auth.json + google_oauth.json)"
```

---

### Task 7: Ensure consistent UTF-8 decode error handling

**Objective:** Some decode calls use `errors="ignore"`, some use `errors="replace"`, some use no error handling (strict, crashes on non-UTF-8). Standardize on `errors="ignore"` per the transform/AGENTS.md pattern.

**Files:**
- Modify: `antigravity_auth/oauth.py`
- Modify: `antigravity_auth/token.py`

**Step 1: Audit current decode calls**

Run: `grep -rn 'decode.*utf' antigravity_auth/*.py antigravity_auth/*/*.py | grep -v test_ | grep -v __pycache__`

**Step 2: Fix oauth.py — add errors="ignore" to bare decode calls**

In `exchange_antigravity()` around line 223:
```python
token_payload = json.loads(token_bytes.decode("utf-8"))  # BARE
```
Change to:
```python
token_payload = json.loads(token_bytes.decode("utf-8", errors="ignore"))
```

In `exchange_antigravity()` around line 220:
```python
error_text = token_bytes.decode("utf-8", errors="ignore")  # already has it? check
```

And around line 249:
```python
user_info = json.loads(user_bytes.decode("utf-8"))  # BARE
```
Change to:
```python
user_info = json.loads(user_bytes.decode("utf-8", errors="ignore"))
```

**Step 3: Fix token.py — add errors="ignore" to bare decode calls**

In `refresh_access_token()` around line 187:
```python
payload = json.loads(resp_bytes.decode("utf-8"))  # BARE
```
Change to:
```python
payload = json.loads(resp_bytes.decode("utf-8", errors="ignore"))
```

**Step 4: Run the full test suite AND verify transform pipeline**

```bash
python3 -m pytest antigravity_auth/ -v
# Also verify the interceptor's import chain is intact
python3 -c "from antigravity_auth.interceptor import _antigravity_request_hook, _antigravity_response_hook; print('interceptor OK')"
```
Expected: 386 passed (same as before), `interceptor OK`

**Step 5: Commit**

```bash
git add antigravity_auth/oauth.py antigravity_auth/token.py
git commit -m "fix: use consistent errors='ignore' on utf-8 decode calls"
```

---

## Phase 3: Test Coverage — Accounts Subsystem

### Task 8: Add tests for accounts/ratelimit.py

**Objective:** Test rate limit key generation, reason parsing, backoff calculation, and expiry clearing.

**Files:**
- Create: `antigravity_auth/accounts/test_ratelimit.py`

**Step 1: Write failing test file**

Create `antigravity_auth/accounts/test_ratelimit.py`:

```python
"""Tests for antigravity_auth.accounts.ratelimit."""
import time
import unittest
from antigravity_auth.accounts.ratelimit import (
    get_quota_key,
    parse_rate_limit_reason,
    calculate_backoff_ms,
    clear_expired_rate_limits,
    is_rate_limited_for_quota_key,
    is_rate_limited_for_family,
    is_rate_limited_for_header_style,
    is_account_cooling_down,
    mark_rate_limited,
    mark_rate_limited_with_reason,
)
from antigravity_auth.accounts.state import (
    ManagedAccount,
    RefreshParts,
    RateLimitState,
    RATE_LIMIT_REASON_QUOTA_EXHAUSTED,
    RATE_LIMIT_REASON_RATE_LIMIT_EXCEEDED,
    RATE_LIMIT_REASON_MODEL_CAPACITY_EXHAUSTED,
    RATE_LIMIT_REASON_SERVER_ERROR,
    RATE_LIMIT_REASON_UNKNOWN,
)


class TestGetQuotaKey(unittest.TestCase):
    def test_claude_returns_claude(self):
        self.assertEqual(get_quota_key("claude", "antigravity"), "claude")
        self.assertEqual(get_quota_key("claude", "gemini-cli"), "claude")

    def test_gemini_antigravity(self):
        key = get_quota_key("gemini", "antigravity")
        self.assertEqual(key, "gemini-antigravity")

    def test_gemini_cli(self):
        key = get_quota_key("gemini", "gemini-cli")
        self.assertEqual(key, "gemini-cli")

    def test_model_specific_key(self):
        key = get_quota_key("gemini", "gemini-cli", model="gemini-3-flash-preview")
        self.assertEqual(key, "gemini-cli:gemini-3-flash-preview")


class TestParseRateLimitReason(unittest.TestCase):
    def test_529_capacity_exhausted(self):
        result = parse_rate_limit_reason(None, None, status=529)
        self.assertEqual(result, RATE_LIMIT_REASON_MODEL_CAPACITY_EXHAUSTED)

    def test_503_capacity_exhausted(self):
        result = parse_rate_limit_reason(None, None, status=503)
        self.assertEqual(result, RATE_LIMIT_REASON_MODEL_CAPACITY_EXHAUSTED)

    def test_500_server_error(self):
        result = parse_rate_limit_reason(None, None, status=500)
        self.assertEqual(result, RATE_LIMIT_REASON_SERVER_ERROR)

    def test_explicit_quota_exhausted(self):
        result = parse_rate_limit_reason("QUOTA_EXHAUSTED", None)
        self.assertEqual(result, RATE_LIMIT_REASON_QUOTA_EXHAUSTED)

    def test_explicit_rate_limit(self):
        result = parse_rate_limit_reason("RATE_LIMIT_EXCEEDED", None)
        self.assertEqual(result, RATE_LIMIT_REASON_RATE_LIMIT_EXCEEDED)

    def test_message_capacity(self):
        result = parse_rate_limit_reason(None, "model is overloaded")
        self.assertEqual(result, RATE_LIMIT_REASON_MODEL_CAPACITY_EXHAUSTED)

    def test_message_rpm(self):
        result = parse_rate_limit_reason(None, "too many requests per minute")
        self.assertEqual(result, RATE_LIMIT_REASON_RATE_LIMIT_EXCEEDED)

    def test_message_quota(self):
        result = parse_rate_limit_reason(None, "quota exhausted for today")
        self.assertEqual(result, RATE_LIMIT_REASON_QUOTA_EXHAUSTED)

    def test_429_defaults_to_unknown(self):
        result = parse_rate_limit_reason(None, None, status=429)
        self.assertEqual(result, RATE_LIMIT_REASON_UNKNOWN)


class TestCalculateBackoff(unittest.TestCase):
    def test_respects_explicit_retry_after(self):
        ms = calculate_backoff_ms(RATE_LIMIT_REASON_UNKNOWN, retry_after_ms=5000)
        self.assertEqual(ms, 5000)

    def test_quota_exhausted_first_tier(self):
        ms = calculate_backoff_ms(RATE_LIMIT_REASON_QUOTA_EXHAUSTED, consecutive_failures=0)
        self.assertEqual(ms, 60_000)  # 1 min

    def test_quota_exhausted_second_tier(self):
        ms = calculate_backoff_ms(RATE_LIMIT_REASON_QUOTA_EXHAUSTED, consecutive_failures=1)
        self.assertEqual(ms, 300_000)  # 5 min

    def test_rate_limit_exceeded(self):
        ms = calculate_backoff_ms(RATE_LIMIT_REASON_RATE_LIMIT_EXCEEDED)
        self.assertEqual(ms, 30_000)

    def test_capacity_exhausted_in_range(self):
        ms = calculate_backoff_ms(RATE_LIMIT_REASON_MODEL_CAPACITY_EXHAUSTED)
        self.assertGreaterEqual(ms, 30_000)  # 45000 - 15000 jitter
        self.assertLessEqual(ms, 60_000)     # 45000 + 15000 jitter

    def test_min_backoff_enforced(self):
        ms = calculate_backoff_ms(RATE_LIMIT_REASON_UNKNOWN, retry_after_ms=500)
        self.assertGreaterEqual(ms, 2_000)


class TestRateLimitState(unittest.TestCase):
    def setUp(self):
        self.state = RateLimitState()

    def test_empty_state_not_limited(self):
        self.assertFalse(is_rate_limited_for_quota_key(self.state, "claude"))

    def test_active_limit(self):
        now = time.time() * 1000
        self.state.set("claude", now + 60_000)  # 1 min in future
        self.assertTrue(is_rate_limited_for_quota_key(self.state, "claude"))

    def test_expired_limit_cleared(self):
        now = time.time() * 1000
        self.state.set("claude", now - 1000)  # 1 sec in past
        clear_expired_rate_limits(self.state)
        self.assertIsNone(self.state.get("claude"))

    def test_family_check_dual_pool(self):
        now = time.time() * 1000
        # Mark both antigravity and gemini-cli as rate limited
        self.state.set("gemini-antigravity", now + 60_000)
        self.state.set("gemini-cli", now + 60_000)
        self.assertTrue(is_rate_limited_for_family(self.state, "gemini"))

    def test_family_check_only_one_pool_limited(self):
        now = time.time() * 1000
        self.state.set("gemini-antigravity", now + 60_000)
        # gemini-cli is not limited, so family should not be fully limited
        self.assertFalse(is_rate_limited_for_family(self.state, "gemini"))


class TestMarking(unittest.TestCase):
    def setUp(self):
        self.account = ManagedAccount(
            index=0,
            refresh_parts=RefreshParts(refresh_token="test-refresh"),
        )

    def test_mark_rate_limited_sets_reset_time(self):
        now = time.time() * 1000
        mark_rate_limited(self.account, 30_000, "gemini", "antigravity")
        reset = self.account.rate_limit_reset_times.get("gemini-antigravity")
        self.assertIsNotNone(reset)
        self.assertGreater(reset, now)

    def test_mark_with_reason_increments_failures(self):
        self.account.consecutive_failures = 0
        mark_rate_limited_with_reason(
            self.account, "gemini", "antigravity", None,
            RATE_LIMIT_REASON_QUOTA_EXHAUSTED,
        )
        self.assertEqual(self.account.consecutive_failures, 1)

    def test_account_not_cooling_down_by_default(self):
        self.assertFalse(is_account_cooling_down(self.account))

    def test_account_cooling_down(self):
        now = time.time() * 1000
        self.account.cooling_down_until = now + 86_400_000  # 24 hours
        self.assertTrue(is_account_cooling_down(self.account))
```

**Step 2: Run tests to verify they pass**

```bash
python3 -m pytest antigravity_auth/accounts/test_ratelimit.py -v
```
Expected: 20+ passed

**Step 3: Commit**

```bash
git add antigravity_auth/accounts/test_ratelimit.py
git commit -m "test: add comprehensive tests for accounts/ratelimit.py"
```

---

### Task 9: Add tests for accounts/quota.py

**Objective:** Test quota group classification, soft quota threshold logic, and remaining fraction normalization.

**Files:**
- Create: `antigravity_auth/accounts/test_quota.py`

**Step 1: Write test file**

Create `antigravity_auth/accounts/test_quota.py`:

```python
"""Tests for antigravity_auth.accounts.quota."""
import unittest
from antigravity_auth.accounts.quota import (
    normalize_remaining_fraction,
    classify_quota_group,
    resolve_quota_group,
    compute_soft_quota_cache_ttl_ms,
    is_over_soft_quota_threshold,
    parse_reset_time,
)


class TestNormalizeRemainingFraction(unittest.TestCase):
    def test_valid_fraction(self):
        self.assertEqual(normalize_remaining_fraction(0.5), 0.5)
        self.assertEqual(normalize_remaining_fraction(0.0), 0.0)
        self.assertEqual(normalize_remaining_fraction(1.0), 1.0)

    def test_clamped_above_1(self):
        self.assertEqual(normalize_remaining_fraction(1.5), 1.0)

    def test_clamped_below_0(self):
        self.assertEqual(normalize_remaining_fraction(-0.5), 0.0)

    def test_non_numeric_returns_zero(self):
        self.assertEqual(normalize_remaining_fraction("abc"), 0.0)
        self.assertEqual(normalize_remaining_fraction(None), 0.0)


class TestClassifyQuotaGroup(unittest.TestCase):
    def test_claude_model(self):
        self.assertEqual(classify_quota_group("claude-opus-4-6-thinking"), "claude")

    def test_gemini_flash(self):
        self.assertEqual(classify_quota_group("gemini-3-flash-preview"), "gemini-flash")

    def test_gemini_pro(self):
        self.assertEqual(classify_quota_group("gemini-3-pro-preview"), "gemini-pro")

    def test_unknown_returns_none(self):
        self.assertIsNone(classify_quota_group("unknown-model"))


class TestResolveQuotaGroup(unittest.TestCase):
    def test_claude_family(self):
        self.assertEqual(resolve_quota_group("claude"), "claude")

    def test_gemini_family_defaults_to_pro(self):
        self.assertEqual(resolve_quota_group("gemini"), "gemini-pro")

    def test_model_overrides_family(self):
        result = resolve_quota_group("gemini", model="gemini-3-flash-preview")
        self.assertEqual(result, "gemini-flash")

    def test_unknown_model_falls_back_to_family(self):
        result = resolve_quota_group("gemini", model="unknown-model")
        self.assertEqual(result, "gemini-pro")


class TestComputeCacheTTL(unittest.TestCase):
    def test_auto_uses_2x_refresh_interval(self):
        ms = compute_soft_quota_cache_ttl_ms("auto", 15)
        self.assertEqual(ms, 2 * 15 * 60 * 1000)

    def test_minimum_10_minutes(self):
        ms = compute_soft_quota_cache_ttl_ms("auto", 3)
        self.assertEqual(ms, 10 * 60 * 1000)

    def test_explicit_integer(self):
        ms = compute_soft_quota_cache_ttl_ms(30, 15)
        self.assertEqual(ms, 30 * 60 * 1000)


class TestSoftQuotaThreshold(unittest.TestCase):
    def test_threshold_disabled_returns_false(self):
        result = is_over_soft_quota_threshold(None, None, "gemini", 100, 600_000)
        self.assertFalse(result)

    def test_no_cache_returns_false(self):
        result = is_over_soft_quota_threshold(None, 1000.0, "gemini", 90, 600_000)
        self.assertFalse(result)

    def test_over_threshold(self):
        import time
        quota = {"gemini-pro": {"remainingFraction": 0.05}}  # 95% used
        result = is_over_soft_quota_threshold(quota, time.time() * 1000, "gemini", 90, 600_000)
        self.assertTrue(result)

    def test_under_threshold(self):
        import time
        quota = {"gemini-pro": {"remainingFraction": 0.50}}  # 50% used
        result = is_over_soft_quota_threshold(quota, time.time() * 1000, "gemini", 90, 600_000)
        self.assertFalse(result)


class TestParseResetTime(unittest.TestCase):
    def test_valid_iso(self):
        result = parse_reset_time("2026-05-20T12:00:00Z")
        self.assertIsNotNone(result)

    def test_empty_returns_none(self):
        self.assertIsNone(parse_reset_time(None))
        self.assertIsNone(parse_reset_time(""))
```

**Step 2: Run tests**

```bash
python3 -m pytest antigravity_auth/accounts/test_quota.py -v
```
Expected: 15+ passed

**Step 3: Commit**

```bash
git add antigravity_auth/accounts/test_quota.py
git commit -m "test: add tests for accounts/quota.py"
```

---

### Task 10: Add tests for accounts/rotation.py

**Objective:** Test HealthScoreTracker scoring, success/penalty recording, and usability checks.

**Files:**
- Create: `antigravity_auth/accounts/test_rotation.py`

**Step 1: Write test file**

Create `antigravity_auth/accounts/test_rotation.py`:

```python
"""Tests for antigravity_auth.accounts.rotation."""
import unittest
from antigravity_auth.accounts.rotation import HealthScoreTracker


class TestHealthScoreTracker(unittest.TestCase):
    def setUp(self):
        self.tracker = HealthScoreTracker()

    def test_initial_score(self):
        score = self.tracker.get_score(0)
        self.assertEqual(score, 70)

    def test_success_increases_score(self):
        self.tracker.record_success(0)
        score = self.tracker.get_score(0)
        self.assertEqual(score, 71)

    def test_rate_limit_decreases_score(self):
        self.tracker.record_rate_limit(0)
        score = self.tracker.get_score(0)
        self.assertEqual(score, 60)

    def test_failure_decreases_score_more(self):
        self.tracker.record_failure(0)
        score = self.tracker.get_score(0)
        self.assertEqual(score, 50)

    def test_score_never_below_zero(self):
        # 4 failures should take 70 to -10, clamped at 0
        for i in range(10):
            self.tracker.record_failure(0)
        score = self.tracker.get_score(0)
        self.assertGreaterEqual(score, 0)

    def test_score_never_above_max(self):
        for i in range(50):
            self.tracker.record_success(0)
        score = self.tracker.get_score(0)
        self.assertLessEqual(score, 100)

    def test_min_usable_threshold(self):
        self.assertTrue(self.tracker.is_usable(0))  # 70 >= 50
        # Force score down
        self.tracker.record_failure(0)  # 70 -> 50
        self.tracker.record_failure(0)  # 50 -> 30
        self.assertFalse(self.tracker.is_usable(0))

    def test_custom_config(self):
        t = HealthScoreTracker(config={"initial": 50, "min_usable": 40})
        self.assertEqual(t.get_score(0), 50)
        self.assertTrue(t.is_usable(0))

    def test_different_accounts_independent(self):
        self.tracker.record_success(0)
        self.assertEqual(self.tracker.get_score(0), 71)
        self.assertEqual(self.tracker.get_score(1), 70)
```

**Step 2: Run tests**

```bash
python3 -m pytest antigravity_auth/accounts/test_rotation.py -v
```
Expected: 9 passed

**Step 3: Commit**

```bash
git add antigravity_auth/accounts/test_rotation.py
git commit -m "test: add tests for accounts/rotation.py"
```

---

### Task 11: Add tests for accounts/state.py

**Objective:** Test ManagedAccount dataclass, RateLimitState get/set/delete/keys/to_dict/from_dict.

**Files:**
- Create: `antigravity_auth/accounts/test_state.py`

**Step 1: Write test file**

Create `antigravity_auth/accounts/test_state.py`:

```python
"""Tests for antigravity_auth.accounts.state."""
import unittest
from antigravity_auth.accounts.state import (
    ManagedAccount,
    RefreshParts,
    RateLimitState,
)


class TestRateLimitState(unittest.TestCase):
    def setUp(self):
        self.state = RateLimitState()

    def test_empty_state(self):
        self.assertIsNone(self.state.get("claude"))
        self.assertIsNone(self.state.get("gemini-antigravity"))

    def test_set_and_get_builtin_keys(self):
        self.state.set("claude", 1000.0)
        self.assertEqual(self.state.get("claude"), 1000.0)

        self.state.set("gemini-antigravity", 2000.0)
        self.assertEqual(self.state.get("gemini-antigravity"), 2000.0)

        self.state.set("gemini-cli", 3000.0)
        self.assertEqual(self.state.get("gemini-cli"), 3000.0)

    def test_set_and_get_extras(self):
        self.state.set("gemini-antigravity:custom-model", 4000.0)
        self.assertEqual(self.state.get("gemini-antigravity:custom-model"), 4000.0)

    def test_delete(self):
        self.state.set("claude", 1000.0)
        self.state.delete("claude")
        self.assertIsNone(self.state.get("claude"))

    def test_delete_extras(self):
        self.state.set("custom-key", 1000.0)
        self.state.delete("custom-key")
        self.assertIsNone(self.state.get("custom-key"))

    def test_keys_returns_all_non_none(self):
        self.state.set("claude", 1000.0)
        self.state.set("gemini-cli", 2000.0)
        self.state.set("extras-key", 3000.0)
        keys = self.state.keys()
        self.assertIn("claude", keys)
        self.assertIn("gemini-cli", keys)
        self.assertIn("extras-key", keys)
        self.assertNotIn("gemini-antigravity", keys)  # was never set

    def test_to_dict(self):
        self.state.set("claude", 1000.0)
        self.state.set("extras-key", 2000.0)
        d = self.state.to_dict()
        self.assertEqual(d["claude"], 1000.0)
        self.assertEqual(d["extras-key"], 2000.0)

    def test_from_dict(self):
        data = {"claude": 1000.0, "custom": 2000.0}
        state = RateLimitState.from_dict(data)
        self.assertEqual(state.get("claude"), 1000.0)
        self.assertEqual(state.get("custom"), 2000.0)
        self.assertIsNone(state.get("gemini-antigravity"))

    def test_from_dict_none(self):
        state = RateLimitState.from_dict(None)
        self.assertIsNone(state.get("claude"))


class TestManagedAccount(unittest.TestCase):
    def test_default_values(self):
        acc = ManagedAccount(
            index=0,
            refresh_parts=RefreshParts(refresh_token="test-token"),
        )
        self.assertEqual(acc.index, 0)
        self.assertEqual(acc.refresh_parts.refresh_token, "test-token")
        self.assertIsNone(acc.email)
        self.assertTrue(acc.enabled)
        self.assertIsInstance(acc.rate_limit_reset_times, RateLimitState)

    def test_disabled_account(self):
        acc = ManagedAccount(
            index=0,
            refresh_parts=RefreshParts(refresh_token="test-token"),
            enabled=False,
        )
        self.assertFalse(acc.enabled)

    def test_with_fingerprint(self):
        acc = ManagedAccount(
            index=0,
            refresh_parts=RefreshParts(refresh_token="test-token"),
            fingerprint={"deviceId": "abc123"},
        )
        self.assertEqual(acc.fingerprint["deviceId"], "abc123")
```

**Step 2: Run tests**

```bash
python3 -m pytest antigravity_auth/accounts/test_state.py -v
```
Expected: 12+ passed

**Step 3: Commit**

```bash
git add antigravity_auth/accounts/test_state.py
git commit -m "test: add tests for accounts/state.py"
```

---

### Task 12: Add tests for accounts/manager.py (core scenarios)

**Objective:** Test AccountManager loading, account selection strategies, rate limit rotation, and persistence.

**Files:**
- Create: `antigravity_auth/accounts/test_manager.py`

**Step 1: Write test file**

Create `antigravity_auth/accounts/test_manager.py`:

```python
"""Tests for antigravity_auth.accounts.manager."""
import json
import os
import tempfile
import time
import unittest
from unittest import mock
from pathlib import Path

from antigravity_auth.accounts.manager import AccountManager
from antigravity_auth.accounts.state import ManagedAccount, RefreshParts


class TestAccountManagerEmpty(unittest.TestCase):
    def setUp(self):
        self.mgr = AccountManager()

    def test_empty_manager_has_no_accounts(self):
        self.assertEqual(self.mgr.get_account_count(), 0)
        self.assertEqual(self.mgr.get_total_account_count(), 0)

    def test_empty_manager_returns_none_for_family(self):
        self.assertIsNone(self.mgr.get_current_account_for_family("gemini"))
        self.assertIsNone(self.mgr.get_current_account_for_family("claude"))

    def test_get_current_or_next_returns_none_when_empty(self):
        result = self.mgr.get_current_or_next_for_family("gemini")
        self.assertIsNone(result)


class TestAccountManagerWithAccounts(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.accounts_path = Path(self.tmpdir) / "antigravity-accounts.json"

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _write_accounts(self, data):
        with open(self.accounts_path, "w") as f:
            json.dump(data, f)

    def _make_manager(self, accounts_data):
        self._write_accounts(accounts_data)
        with mock.patch("antigravity_auth.accounts.manager.get_accounts_json_path",
                        return_value=self.accounts_path):
            return AccountManager.load_from_disk()

    def test_loads_accounts_from_disk(self):
        data = {
            "version": 4,
            "accounts": [
                {
                    "email": "alice@example.com",
                    "refreshToken": "refresh-alice",
                    "projectId": "proj-a",
                },
            ],
            "activeIndex": 0,
            "activeIndexByFamily": {"claude": 0, "gemini": 0},
        }
        mgr = self._make_manager(data)
        self.assertEqual(mgr.get_account_count(), 1)
        self.assertEqual(mgr.get_total_account_count(), 1)

    def test_gets_current_account_for_family(self):
        data = {
            "version": 4,
            "accounts": [
                {
                    "email": "alice@example.com",
                    "refreshToken": "refresh-alice",
                    "projectId": "proj-a",
                },
            ],
            "activeIndex": 0,
            "activeIndexByFamily": {"claude": 0, "gemini": 0},
        }
        mgr = self._make_manager(data)
        acc = mgr.get_current_account_for_family("gemini")
        self.assertIsNotNone(acc)
        self.assertEqual(acc.email, "alice@example.com")

    def test_skips_disabled_accounts(self):
        data = {
            "version": 4,
            "accounts": [
                {
                    "email": "alice@example.com",
                    "refreshToken": "refresh-alice",
                    "enabled": False,
                },
            ],
            "activeIndex": 0,
            "activeIndexByFamily": {"claude": 0, "gemini": 0},
        }
        mgr = self._make_manager(data)
        self.assertEqual(mgr.get_account_count(), 0)
        self.assertIsNone(mgr.get_current_account_for_family("gemini"))

    def test_sticky_strategy_returns_current(self):
        data = {
            "version": 4,
            "accounts": [
                {
                    "email": "alice@example.com",
                    "refreshToken": "refresh-alice",
                },
            ],
            "activeIndex": 0,
            "activeIndexByFamily": {"claude": 0, "gemini": 0},
        }
        mgr = self._make_manager(data)
        acc = mgr.get_current_or_next_for_family("gemini", strategy="sticky")
        self.assertIsNotNone(acc)
        self.assertEqual(acc.email, "alice@example.com")

    def test_skips_rate_limited_accounts(self):
        """Account with active rate limit should be skipped."""
        now = time.time() * 1000
        data = {
            "version": 4,
            "accounts": [
                {
                    "email": "alice@example.com",
                    "refreshToken": "refresh-alice",
                    "rateLimitResetTimes": {
                        "gemini-antigravity": now + 60_000,
                        "gemini-cli": now + 60_000,
                    },
                },
                {
                    "email": "bob@example.com",
                    "refreshToken": "refresh-bob",
                },
            ],
            "activeIndex": 0,
            "activeIndexByFamily": {"claude": 0, "gemini": 0},
        }
        mgr = self._make_manager(data)
        acc = mgr.get_current_or_next_for_family("gemini", strategy="sticky")
        self.assertIsNotNone(acc)
        # Should have rotated to bob (alice is rate limited)
        self.assertEqual(acc.email, "bob@example.com")

    def test_multiple_accounts_default_to_first(self):
        data = {
            "version": 4,
            "accounts": [
                {"email": "alice@example.com", "refreshToken": "refresh-alice"},
                {"email": "bob@example.com", "refreshToken": "refresh-bob"},
            ],
            "activeIndex": 0,
            "activeIndexByFamily": {"claude": 0, "gemini": 0},
        }
        mgr = self._make_manager(data)
        self.assertEqual(mgr.get_account_count(), 2)

    def test_remove_account_reindexes(self):
        data = {
            "version": 4,
            "accounts": [
                {"email": "alice@example.com", "refreshToken": "refresh-alice"},
                {"email": "bob@example.com", "refreshToken": "refresh-bob"},
            ],
            "activeIndex": 0,
            "activeIndexByFamily": {"claude": 0, "gemini": 0},
        }
        mgr = self._make_manager(data)
        mgr.remove_account(0)
        self.assertEqual(mgr.get_total_account_count(), 1)
        remaining = mgr.get_accounts()
        self.assertEqual(remaining[0].index, 0)
        self.assertEqual(remaining[0].email, "bob@example.com")

    def test_set_account_enabled(self):
        data = {
            "version": 4,
            "accounts": [
                {"email": "alice@example.com", "refreshToken": "refresh-alice"},
            ],
            "activeIndex": 0,
            "activeIndexByFamily": {"claude": 0, "gemini": 0},
        }
        mgr = self._make_manager(data)
        self.assertTrue(mgr.set_account_enabled(0, False))
        self.assertEqual(mgr.get_account_count(), 0)

    def test_accounts_snapshot(self):
        data = {
            "version": 4,
            "accounts": [
                {"email": "alice@example.com", "refreshToken": "refresh-alice"},
            ],
            "activeIndex": 0,
            "activeIndexByFamily": {"claude": 0, "gemini": 0},
        }
        mgr = self._make_manager(data)
        snap = mgr.get_accounts_snapshot()
        self.assertEqual(len(snap), 1)
        self.assertEqual(snap[0]["email"], "alice@example.com")

    def test_skips_cooling_down_accounts(self):
        now = time.time() * 1000
        data = {
            "version": 4,
            "accounts": [
                {
                    "email": "alice@example.com",
                    "refreshToken": "refresh-alice",
                    "coolingDownUntil": now + 86_400_000,
                    "cooldownReason": "auth-failure",
                },
                {
                    "email": "bob@example.com",
                    "refreshToken": "refresh-bob",
                },
            ],
            "activeIndex": 0,
            "activeIndexByFamily": {"claude": 0, "gemini": 0},
        }
        mgr = self._make_manager(data)
        acc = mgr.get_current_or_next_for_family("gemini", strategy="sticky")
        self.assertIsNotNone(acc)
        self.assertEqual(acc.email, "bob@example.com")
```

**Step 2: Run tests**

```bash
python3 -m pytest antigravity_auth/accounts/test_manager.py -v
```
Expected: 12+ passed

**Step 3: Commit**

```bash
git add antigravity_auth/accounts/test_manager.py
git commit -m "test: add tests for accounts/manager.py (core scenarios)"
```

---

## Phase 4: Test Coverage — Integration & Utility Modules

### Task 13: Add tests for fingerprint.py

**Objective:** Test device fingerprint generation and header building.

**Files:**
- Create: `antigravity_auth/test_fingerprint.py`

**Step 1: Write test file**

Create `antigravity_auth/test_fingerprint.py`:

```python
"""Tests for antigravity_auth.fingerprint."""
import unittest
from antigravity_auth.fingerprint import (
    generate_fingerprint,
    generate_device_id,
    generate_session_token,
    build_fingerprint_headers,
    update_fingerprint_version,
)


class TestGenerateDeviceId(unittest.TestCase):
    def test_returns_uuid_string(self):
        did = generate_device_id()
        self.assertIsInstance(did, str)
        self.assertEqual(len(did), 36)  # UUID format

    def test_unique_each_call(self):
        ids = {generate_device_id() for _ in range(10)}
        self.assertEqual(len(ids), 10)


class TestGenerateSessionToken(unittest.TestCase):
    def test_returns_hex_string(self):
        tok = generate_session_token()
        self.assertIsInstance(tok, str)
        self.assertEqual(len(tok), 32)  # 16 bytes = 32 hex chars


class TestGenerateFingerprint(unittest.TestCase):
    def test_has_required_keys(self):
        fp = generate_fingerprint()
        self.assertIn("deviceId", fp)
        self.assertIn("sessionToken", fp)
        self.assertIn("userAgent", fp)
        self.assertIn("apiClient", fp)
        self.assertIn("clientMetadata", fp)
        self.assertIn("createdAt", fp)

    def test_client_metadata_structure(self):
        fp = generate_fingerprint()
        cm = fp["clientMetadata"]
        self.assertIn("ideType", cm)
        self.assertIn("platform", cm)
        self.assertIn("pluginType", cm)

    def test_user_agent_contains_antigravity(self):
        fp = generate_fingerprint()
        self.assertIn("Antigravity", fp["userAgent"])


class TestBuildFingerprintHeaders(unittest.TestCase):
    def test_none_returns_empty(self):
        self.assertEqual(build_fingerprint_headers(None), {})

    def test_valid_fingerprint_returns_ua(self):
        fp = {"userAgent": "TestAgent/1.0"}
        headers = build_fingerprint_headers(fp)
        self.assertEqual(headers["User-Agent"], "TestAgent/1.0")

    def test_missing_ua_returns_empty(self):
        fp = {"deviceId": "abc"}
        self.assertEqual(build_fingerprint_headers(fp), {})


class TestUpdateFingerprintVersion(unittest.TestCase):
    def test_adds_missing_created_at(self):
        fp = {"deviceId": "abc"}
        changed = update_fingerprint_version(fp)
        self.assertTrue(changed)
        self.assertIn("createdAt", fp)

    def test_adds_missing_api_client(self):
        fp = {"deviceId": "abc", "createdAt": 1000}
        changed = update_fingerprint_version(fp)
        self.assertTrue(changed)
        self.assertIn("apiClient", fp)

    def test_no_change_when_complete(self):
        fp = generate_fingerprint()
        changed = update_fingerprint_version(fp)
        self.assertFalse(changed)
```

**Step 2: Run tests**

```bash
python3 -m pytest antigravity_auth/test_fingerprint.py -v
```
Expected: 9+ passed

**Step 3: Commit**

```bash
git add antigravity_auth/test_fingerprint.py
git commit -m "test: add tests for fingerprint.py"
```

---

### Task 14: Add tests for endpoints.py

**Objective:** Test EndpointProvider fallback chain and failure marking.

**Files:**
- Create: `antigravity_auth/test_endpoints.py`

**Step 1: Write test file**

Create `antigravity_auth/test_endpoints.py`:

```python
"""Tests for antigravity_auth.endpoints."""
import unittest
from antigravity_auth.endpoints import (
    EndpointProvider,
    select_endpoint,
    mark_endpoint_failed,
    reset_endpoint_failures,
)
from antigravity_auth.constants import (
    ANTIGRAVITY_ENDPOINT_PROD,
    ANTIGRAVITY_ENDPOINT_DAILY,
    ANTIGRAVITY_ENDPOINT_AUTOPUSH,
)


class TestEndpointProvider(unittest.TestCase):
    def setUp(self):
        self.provider = EndpointProvider()

    def test_antigravity_returns_fallback_chain(self):
        eps = self.provider.get_endpoints("antigravity")
        self.assertIn(ANTIGRAVITY_ENDPOINT_DAILY, eps)
        self.assertIn(ANTIGRAVITY_ENDPOINT_AUTOPUSH, eps)
        self.assertIn(ANTIGRAVITY_ENDPOINT_PROD, eps)

    def test_gemini_cli_returns_prod_only(self):
        eps = self.provider.get_endpoints("gemini-cli")
        self.assertEqual(len(eps), 1)
        self.assertEqual(eps[0], ANTIGRAVITY_ENDPOINT_PROD)

    def test_mark_failed(self):
        self.provider.mark_failed(ANTIGRAVITY_ENDPOINT_PROD)
        self.assertTrue(self.provider.is_failed(ANTIGRAVITY_ENDPOINT_PROD))
        self.assertIn(ANTIGRAVITY_ENDPOINT_PROD, self.provider.failed_endpoints)

    def test_reset_clears_failures(self):
        self.provider.mark_failed(ANTIGRAVITY_ENDPOINT_PROD)
        self.provider.reset()
        self.assertFalse(self.provider.is_failed(ANTIGRAVITY_ENDPOINT_PROD))

    def test_failed_endpoints_is_copy(self):
        self.provider.mark_failed(ANTIGRAVITY_ENDPOINT_PROD)
        copy = self.provider.failed_endpoints
        copy.add("another")
        self.assertNotIn("another", self.provider.failed_endpoints)


class TestSelectEndpoint(unittest.TestCase):
    def test_returns_prod_by_default(self):
        self.assertEqual(select_endpoint(), ANTIGRAVITY_ENDPOINT_PROD)


class TestModuleLevelFunctions(unittest.TestCase):
    def test_mark_and_reset(self):
        mark_endpoint_failed("https://test.example.com")
        reset_endpoint_failures()
        # No assertion needed — just verifying no crash
```

**Step 2: Run tests**

```bash
python3 -m pytest antigravity_auth/test_endpoints.py -v
```
Expected: 6+ passed

**Step 3: Commit**

```bash
git add antigravity_auth/test_endpoints.py
git commit -m "test: add tests for endpoints.py"
```

---

### Task 15: Add tests for transform/envelope.py

**Objective:** Test header building, model name resolution, envelope construction.

**Files:**
- Create: `antigravity_auth/transform/test_envelope.py`

**Step 1: Write test file**

Create `antigravity_auth/transform/test_envelope.py`:

```python
"""Tests for antigravity_auth.transform.envelope."""
import json
import unittest
from antigravity_auth.transform.envelope import (
    build_antigravity_headers,
    build_antigravity_envelope,
    build_antigravity_url,
    resolve_model_for_header_style,
    is_antigravity_request,
    generate_synthetic_project_id,
    extract_model_from_url,
)


class TestBuildHeaders(unittest.TestCase):
    def test_gemini_cli_style(self):
        headers = build_antigravity_headers(header_style="gemini-cli")
        self.assertIn("User-Agent", headers)
        self.assertIn("google-api-nodejs-client", headers["User-Agent"])

    def test_antigravity_style(self):
        headers = build_antigravity_headers(header_style="antigravity")
        self.assertIn("Client-Metadata", headers)
        cm = json.loads(headers["Client-Metadata"])
        self.assertEqual(cm["ideType"], "ANTIGRAVITY")


class TestResolveModel(unittest.TestCase):
    def test_strips_antigravity_prefix_for_gemini_cli(self):
        result = resolve_model_for_header_style(
            "antigravity-gemini-3-pro", "gemini-cli"
        )
        self.assertEqual(result, "gemini-3-pro")

    def test_passthrough_for_antigravity_style(self):
        result = resolve_model_for_header_style(
            "antigravity-gemini-3-pro", "antigravity"
        )
        self.assertEqual(result, "antigravity-gemini-3-pro")


class TestBuildUrl(unittest.TestCase):
    def test_streaming_url(self):
        url = build_antigravity_url(
            "https://cloudcode-pa.googleapis.com",
            "gemini-3-flash-preview",
        )
        self.assertIn("?alt=sse", url)
        self.assertIn("streamGenerateContent", url)

    def test_non_streaming_url(self):
        url = build_antigravity_url(
            "https://cloudcode-pa.googleapis.com",
            "gemini-3-flash-preview",
            action="generateContent",
            streaming=False,
        )
        self.assertNotIn("alt=sse", url)


class TestBuildEnvelope(unittest.TestCase):
    def test_antigravity_envelope_includes_system_instruction(self):
        payload = {
            "contents": [{"role": "user", "parts": [{"text": "Hello"}]}],
        }
        envelope = build_antigravity_envelope(
            payload, "gemini-3-flash-preview", "test-project"
        )
        self.assertIn("request", envelope)
        self.assertIn("systemInstruction", envelope["request"])
        self.assertIn("requestType", envelope)
        self.assertEqual(envelope["requestType"], "agent")

    def test_preserves_existing_system_instruction(self):
        payload = {
            "systemInstruction": {
                "parts": [{"text": "You are helpful."}],
            },
            "contents": [{"role": "user", "parts": [{"text": "Hello"}]}],
        }
        envelope = build_antigravity_envelope(
            payload, "gemini-3-flash-preview", "test-project"
        )
        si = envelope["request"]["systemInstruction"]
        self.assertIn("Antigravity", si["parts"][0]["text"])
        self.assertIn("You are helpful", si["parts"][0]["text"])


class TestUtilities(unittest.TestCase):
    def test_is_antigravity_request(self):
        self.assertTrue(is_antigravity_request(
            "https://generativelanguage.googleapis.com/v1/models/gemini:generateContent"
        ))

    def test_not_antigravity_request(self):
        self.assertFalse(is_antigravity_request(
            "https://api.anthropic.com/v1/messages"
        ))

    def test_project_id_format(self):
        pid = generate_synthetic_project_id()
        parts = pid.split("-")
        self.assertEqual(len(parts), 3)

    def test_extract_model_from_url(self):
        model = extract_model_from_url(
            "https://cloudcode-pa.googleapis.com/v1internal:generateContent/models/gemini-3-flash-preview:streamGenerateContent"
        )
        self.assertEqual(model, "gemini-3-flash-preview")
```

**Step 2: Run tests**

```bash
python3 -m pytest antigravity_auth/transform/test_envelope.py -v
```
Expected: 10+ passed

**Step 3: Commit**

```bash
git add antigravity_auth/transform/test_envelope.py
git commit -m "test: add tests for transform/envelope.py"
```

---

### Task 16: Add tests for transform/response.py

**Objective:** Test SSE parsing, usage extraction, error handling, and preview access rewrites.

**Files:**
- Create: `antigravity_auth/transform/test_response.py`

**Step 1: Write test file**

Create `antigravity_auth/transform/test_response.py`:

```python
"""Tests for antigravity_auth.transform.response."""
import json
import unittest
from antigravity_auth.transform.response import (
    transform_antigravity_response,
    extract_usage_from_body,
    extract_retry_info,
    rewrite_preview_access_error,
    inject_debug_thinking,
)


class TestExtractUsage(unittest.TestCase):
    def test_usage_from_response_field(self):
        body = json.dumps({
            "response": {
                "usageMetadata": {
                    "totalTokenCount": 150,
                    "promptTokenCount": 100,
                    "candidatesTokenCount": 50,
                }
            }
        })
        usage = extract_usage_from_body(body)
        self.assertEqual(usage["totalTokenCount"], 150)

    def test_no_usage_returns_none(self):
        body = json.dumps({"response": {"candidates": []}})
        self.assertIsNone(extract_usage_from_body(body))

    def test_usage_from_list(self):
        body = json.dumps([
            {"usageMetadata": {"totalTokenCount": 200}}
        ])
        usage = extract_usage_from_body(body)
        self.assertEqual(usage["totalTokenCount"], 200)


class TestExtractRetryInfo(unittest.TestCase):
    def test_valid_retry_info(self):
        body = {
            "error": {
                "details": [
                    {
                        "@type": "type.googleapis.com/google.rpc.RetryInfo",
                        "retryDelay": "30s",
                    }
                ]
            }
        }
        info = extract_retry_info(body)
        self.assertIsNotNone(info)
        self.assertEqual(info["retryDelayMs"], 30000)

    def test_no_retry_info(self):
        self.assertIsNone(extract_retry_info({"error": {"message": "fail"}}))


class TestRewritePreviewAccess(unittest.TestCase):
    def test_rewrites_404_for_claude(self):
        body = {"error": {"message": "Model not found"}}
        result = rewrite_preview_access_error(body, 404, "claude-opus-4-6-thinking")
        self.assertIsNotNone(result)
        self.assertIn("preview access", result["error"]["message"])

    def test_no_rewrite_for_200(self):
        body = {"response": {"candidates": []}}
        result = rewrite_preview_access_error(body, 200, "claude-opus-4-6-thinking")
        self.assertIsNone(result)


class TestInjectDebugThinking(unittest.TestCase):
    def test_injects_into_candidates(self):
        body = {
            "candidates": [{
                "content": {"parts": [{"text": "Hello"}]}
            }]
        }
        result = inject_debug_thinking(body, "Debug info")
        first_part = result["candidates"][0]["content"]["parts"][0]
        self.assertEqual(first_part["text"], "Debug info")
        self.assertTrue(first_part.get("thought"))


class TestTransformResponse(unittest.TestCase):
    def test_non_json_passthrough(self):
        body, headers, err = transform_antigravity_response(
            "plain text", streaming=False,
            headers={"content-type": "text/plain"}
        )
        self.assertEqual(body, "plain text")
        self.assertIsNone(err)

    def test_error_response(self):
        error_body = json.dumps({
            "error": {"message": "thinking block order error: expected thinking, found text"}
        })
        body, headers, err = transform_antigravity_response(
            error_body, streaming=False,
            status_code=400,
            headers={"content-type": "application/json"}
        )
        self.assertIsNotNone(err)
        self.assertEqual(err["recoveryType"], "thinking_block_order")
```

**Step 2: Run tests**

```bash
python3 -m pytest antigravity_auth/transform/test_response.py -v
```
Expected: 7+ passed

**Step 3: Commit**

```bash
git add antigravity_auth/transform/test_response.py
git commit -m "test: add tests for transform/response.py"
```

---

## Phase 5: Code Quality

### Task 17: Wire verification.py into the runtime (SAFE)

**Objective:** `verification.py` is orphaned — `verify_account_access()` is tested but never called. Wire it into `cli.py::check_quotas_and_verify()` using the ALREADY-REFRESHED access token from the existing flow. Do NOT call `probe_account_health()` — it does a duplicate `refresh_access_token()` API call, and the Antigravity API rate-limits aggressively.

**Why this is safe:** `check_quotas_and_verify()` is only called by the CLI commands `hermes antigravity check` and `hermes antigravity quota`. It is NEVER called by the plugin runtime (interceptor, watchdog, recovery). This cannot break the running plugin.

**Files:**
- Modify: `antigravity_auth/cli.py:330-384` (the `check_quotas_and_verify` function)

**Step 1: Read current check_quotas_and_verify()**

Read lines 330-384 of `antigravity_auth/cli.py`.

**Step 2: Add account health probe AFTER the existing quota display loop**

After the quota display loop (after the `print("=" * 60)` at line 383), add the following block. Note: this uses the `access_token` already refreshed in the loop — no duplicate API call.

```python
    # Run account health probes using already-refreshed tokens
    print("\nRunning account health probes...")
    from .verification import verify_account_access
    for idx, acc in enumerate(accounts):
        email = acc.get("email", "Unknown")
        refresh_token = acc.get("refreshToken", "")
        if not refresh_token:
            print(f"  [{idx}] {email}: SKIPPED (no credentials)")
            continue
        try:
            from .token import refresh_access_token
            refreshed = refresh_access_token({"refresh": refresh_token})
            access_token = refreshed.get("access", "")
        except Exception:
            access_token = ""
        if not access_token:
            print(f"  [{idx}] {email}: ERROR (token refresh failed)")
            continue
        result = verify_account_access(acc, access_token,
                                        project_id=acc.get("projectId"))
        status_icon = "OK" if result.status == "ok" else "BLOCKED" if result.status == "blocked" else "ERROR"
        print(f"  [{idx}] {email}: {status_icon}")
        if result.verify_url:
            print(f"      Verification URL: {result.verify_url}")
        if result.message and result.status != "ok":
            print(f"      {result.message}")
    print("=" * 60)
```

Wait — this still does `refresh_access_token` per account. But the EXISTING loop already refreshes tokens (lines 349-356). So we should capture the token from the first loop instead. Rethink:

Actually, the existing `check_quotas_and_verify()` already iterates accounts and refreshes each token (lines 348-356 in the for loop). The cleanest approach: within the SAME loop iteration, after printing the quota info, call `verify_account_access()` with the same `access_token` already computed.

Replace the entire function body's inner loop to add the verification call after the quota display:

In the for loop at lines 339-383, after the quota display block (after the `else` clause that handles raw response), add:

```python

        # ---- Account health probe (uses same access_token from above) ----
        try:
            from .verification import verify_account_access
            probe = verify_account_access(acc, access_token, project_id=project_id)
            if probe.status == "blocked":
                print(f"    HEALTH: BLOCKED — {probe.message}")
                if probe.verify_url:
                    print(f"    Verification URL: {probe.verify_url}")
            elif probe.status != "ok":
                print(f"    HEALTH: ERROR — {probe.message}")
        except Exception:
            pass  # health probe is informational only — never fail the check command
```

**Step 3: Run existing tests**

```bash
python3 -m pytest antigravity_auth/test_cli.py antigravity_auth/test_verification.py -v
```
Expected: all existing tests pass

**Step 4: Verify the CLI command works (dry run — won't make API calls without real accounts)**

```bash
python3 -c "from antigravity_auth.cli import check_quotas_and_verify; print('import OK')"
```
Expected: `import OK`

**Step 5: Commit**

```bash
git add antigravity_auth/cli.py
git commit -m "feat: wire verification.py into cli health checks (uses existing token, no duplicate refresh)"
```

---

### Task 18: Extract shared `_now_ms()` to common utility (MODERATE RISK — run AFTER Phases 3+4)

**Objective:** `_now_ms()` is copy-pasted into 5 files. Extract to a shared utility module.

**CRITICAL SAFETY CONSTRAINT:** This task touches 4 files in the interceptor's dependency chain (`accounts/manager.py`, `accounts/ratelimit.py`, `accounts/rotation.py`, `accounts/quota.py`). If the import of `_time_utils` fails in any of them, the interceptor's response hook silently loses rate-limit handling. NO crash — just mysterious 429s with no account rotation.

**SAFETY RULE:** Run the full test suite after EVERY file change. Do NOT batch the changes.

**Files:**
- Create: `antigravity_auth/_time_utils.py`
- Modify: `antigravity_auth/accounts/manager.py`
- Modify: `antigravity_auth/accounts/ratelimit.py`
- Modify: `antigravity_auth/accounts/quota.py`
- Modify: `antigravity_auth/accounts/rotation.py`
- Modify: `antigravity_auth/fingerprint.py`

**Step 1: Create _time_utils.py**

```python
from __future__ import annotations

import time


def now_ms() -> float:
    """Return current time in epoch milliseconds."""
    return time.time() * 1000
```

**Step 2: Verify the new module imports cleanly**

```bash
python3 -c "from antigravity_auth._time_utils import now_ms; print(now_ms())"
```
Expected: prints a number like `1747920000000.0`

**Step 3: Replace in fingerprint.py (LOWEST risk — not in interceptor's import chain)**

Remove private `_now_ms()` (line 106-108). Add import at top: `from ._time_utils import now_ms`. Replace all 2 call sites.

```bash
python3 -m pytest antigravity_auth/test_fingerprint.py -v  # if exists
python3 -m pytest antigravity_auth/ -v  # full suite
```

**Step 4: Replace in accounts/quota.py**

Quota is NOT imported by the interceptor's response hook (it's only used by CLI). Lowest risk in accounts/.

Remove `_now_ms()` (line 15-16). Add `from .._time_utils import now_ms`. Replace call site at line 105.

```bash
python3 -m pytest antigravity_auth/ -v
```

**Step 5: Replace in accounts/rotation.py**

Rotation IS imported by manager which IS imported by the interceptor. MEDIUM risk.

Remove `_now_ms()` (line 69-70). Add `from .._time_utils import now_ms`. Replace 4 call sites (lines 32, 38, 48, 57).

```bash
python3 -m pytest antigravity_auth/ -v
# Extra safety: verify interceptor can still import its dependencies
python3 -c "from antigravity_auth.accounts.manager import AccountManager; print('manager OK')"
python3 -c "from antigravity_auth.accounts.ratelimit import mark_rate_limited; print('ratelimit OK')"
```

**Step 6: Replace in accounts/ratelimit.py**

Ratlimit IS imported directly by the interceptor. HIGH risk. Proceed one line at a time.

Remove `_now_ms()` (line 39-40). Add `from .._time_utils import now_ms`. Replace 6 call sites (lines 133, 143, 193, 213, 229, 250, 287).

```bash
python3 -m pytest antigravity_auth/ -v
python3 -c "from antigravity_auth.accounts.ratelimit import mark_rate_limited, is_account_cooling_down; print('ratelimit OK')"
```

**Step 7: Replace in accounts/manager.py**

Highest risk — the interceptor imports `AccountManager` from here.

Remove `_now_ms()` (line 36-37). Add `from .._time_utils import now_ms`. Replace 6 call sites (lines 120, 329, 337, 514, 531, 565).

```bash
python3 -m pytest antigravity_auth/ -v
python3 -c "from antigravity_auth.accounts.manager import AccountManager; mgr = AccountManager(); print('manager OK, accounts:', mgr.get_account_count())"
```

**Step 8: Final verification — manual import chain check**

```bash
python3 -c "
from antigravity_auth.accounts.state import RateLimitState, ManagedAccount
from antigravity_auth.accounts.rotation import HealthScoreTracker
from antigravity_auth.accounts.ratelimit import mark_rate_limited, is_account_cooling_down
from antigravity_auth.accounts.manager import AccountManager
from antigravity_auth.accounts.quota import classify_quota_group
print('All accounts module imports OK')
"
```
Expected: `All accounts module imports OK`

**Step 9: Verify no remaining private _now_ms**

```bash
grep -rn "def _now_ms" antigravity_auth/
```
Expected: no output (all removed)

**Step 10: Commit**

```bash
git add antigravity_auth/_time_utils.py antigravity_auth/accounts/manager.py antigravity_auth/accounts/ratelimit.py antigravity_auth/accounts/quota.py antigravity_auth/accounts/rotation.py antigravity_auth/fingerprint.py
git commit -m "refactor: extract shared _now_ms() to _time_utils.py"
```

---

### Task 19: Document why wildcard import is intentional (SAFE — no code changes)

**Objective:** The plugin wrapper `__init__.py` uses `from antigravity_auth.hermes_provider_plugin import *`. This LOOKS wrong but is CORRECT: `hermes_provider_plugin.py` relies entirely on module-level side effects (`register_provider()`, `_patch_hermes_model_picker()`, `_set_oauth_env_from_credentials()`). All the important work happens when the module is first imported. The `*` import triggers all side effects — explicit named imports would too, but changing it risks silent breakage and gains nothing. Document why.

**Files:**
- Modify: `antigravity_auth/install_plugins.py:22-24` (add comment to PROVIDER_INIT template)
- Modify: `plugins/model-providers/antigravity/__init__.py:1-3` (add comment to shipped plugin)

**Step 1: Add comment to install_plugins.py PROVIDER_INIT template**

Change the PROVIDER_INIT string (lines 22-24) from:
```python
PROVIDER_INIT = '''"""Google Antigravity provider aliases for Hermes' Cloud Code runtime."""

from antigravity_auth.hermes_provider_plugin import *  # noqa: F401,F403
'''
```

To:
```python
PROVIDER_INIT = '''"""Google Antigravity provider aliases for Hermes' Cloud Code runtime."""

# This import * is intentional — hermes_provider_plugin relies on
# module-level side effects (register_provider, _patch_hermes_model_picker,
# _set_oauth_env_from_credentials). Explicit named imports would trigger
# the same side effects but add fragility. DO NOT refactor without
# understanding the side-effect-driven registration pattern.
from antigravity_auth.hermes_provider_plugin import *  # noqa: F401,F403
'''
```

**Step 2: Add same comment to shipped plugin __init__.py**

Change `plugins/model-providers/antigravity/__init__.py` from:
```python
"""Google Antigravity provider aliases for Hermes' Cloud Code runtime."""

from antigravity_auth.hermes_provider_plugin import *  # noqa: F401,F403
```

To:
```python
"""Google Antigravity provider aliases for Hermes' Cloud Code runtime."""

# This import * is intentional — hermes_provider_plugin relies on
# module-level side effects (register_provider, _patch_hermes_model_picker,
# _set_oauth_env_from_credentials). Explicit named imports would trigger
# the same side effects but add fragility. DO NOT refactor without
# understanding the side-effect-driven registration pattern.
from antigravity_auth.hermes_provider_plugin import *  # noqa: F401,F403
```

**Step 3: Verify no change in behavior**

```bash
python3 -c "
# Verify the shipped plugin file still has the import *
with open('plugins/model-providers/antigravity/__init__.py') as f:
    content = f.read()
assert 'import *' in content, 'missing import * — this would break plugin loading'
print('OK: import * preserved')
"
```
Expected: `OK: import * preserved`

**Step 4: Commit**

```bash
git add antigravity_auth/install_plugins.py plugins/model-providers/antigravity/__init__.py
git commit -m "docs: document why wildcard import is intentional in plugin wrapper"
```

---

### Task 20: Add module docstrings to all 27 files missing them

**Objective:** Every Python module should have a one-line docstring describing its purpose.

**Files:** 27 source files (see review for full list)

**Step 1: Add docstrings to top-level modules**

For each file, add a one-line docstring as the first line:

| File | Docstring |
|------|-----------|
| `antigravity_auth/__init__.py` | `"""Hermes Agent plugin for Google Antigravity OAuth model access."""` |
| `antigravity_auth/cli.py` | `"""CLI subcommands for OAuth login, account management, and quota checks."""` |
| `antigravity_auth/config.py` | `"""Configuration dataclass with YAML loader and TTL cache."""` |
| `antigravity_auth/constants.py` | `"""OAuth client credentials, endpoints, default headers, and platform detection."""` |
| `antigravity_auth/debug.py` | `"""Structured logging with file rotation and debug diagnostics."""` |
| `antigravity_auth/endpoints.py` | `"""Antigravity API endpoint fallback chain (daily → autopush → prod)."""` |
| `antigravity_auth/fingerprint.py` | `"""Per-account device fingerprint generation for Antigravity header spoofing."""` |
| `antigravity_auth/oauth.py` | `"""PKCE OAuth 2.0 authorization and token exchange for Google Antigravity."""` |
| `antigravity_auth/recovery.py` | `"""Session error detection and recovery toast notifications."""` |
| `antigravity_auth/search.py` | `"""Google Search tool via Antigravity API with URL context analysis."""` |
| `antigravity_auth/storage.py` | `"""Persistent account and credential storage for Hermes Antigravity plugin."""` |
| `antigravity_auth/token.py` | `"""Access token refresh, expiry detection, and OAuth error parsing."""` |
| `antigravity_auth/verification.py` | `"""Account health probing and Google verification-required detection."""` |
| `antigravity_auth/_credentials.py` | `"""OAuth client credentials — bundled or overridden via environment variables."""` |

**Step 2: Add docstrings to accounts/ modules**

| File | Docstring |
|------|-----------|
| `accounts/__init__.py` | `"""Multi-account management: selection, rotation, quota, and rate limiting."""` |
| `accounts/manager.py` | `"""AccountManager: in-memory multi-account selection with sticky rotation."""` |
| `accounts/quota.py` | `"""Dual quota pool classification, soft threshold checks, and live quota API."""` |
| `accounts/quota_display.py` | `"""Color-coded CLI progress bars for account quota visualization."""` |
| `accounts/ratelimit.py` | `"""Rate limit handling: reason parsing, exponential backoff, and cooldowns."""` |
| `accounts/rotation.py` | `"""HealthScoreTracker: scores accounts by success rate for rotation decisions."""` |
| `accounts/state.py` | `"""Dataclasses for ManagedAccount, RateLimitState, and rate limit constants."""` |

**Step 3: Add docstrings to transform/ modules**

| File | Docstring |
|------|-----------|
| `transform/__init__.py` | Already has docstring-equivalent exports — add `"""Request/response transformation pipeline for Antigravity API format conversion."""` at top |
| `transform/envelope.py` | `"""Antigravity request envelope wrapping with header building and URL construction."""` |
| `transform/messages.py` | `"""OpenAI-format messages → Gemini contents[].parts[] format conversion."""` |
| `transform/response.py` | `"""SSE streaming response parsing, usage extraction, and error rewriting."""` |
| `transform/schema.py` | `"""JSON Schema sanitization for Antigravity API compatibility (const, $ref removal)."""` |
| `transform/thinking.py` | `"""Claude thinking block stripping, sanitization, and deep recursive filtering."""` |

**Step 4: Run full test suite**

```bash
python3 -m pytest antigravity_auth/ -v
```
Expected: all tests still pass (docstrings don't change behavior)

**Step 5: Commit**

```bash
git add antigravity_auth/
git commit -m "docs: add module docstrings to all source files"
```

---

## Phase 6: Documentation

### Task 21: Add CHANGELOG.md

**Objective:** Document version history in a standard format.

**Files:**
- Create: `CHANGELOG.md`

**Step 1: Write CHANGELOG.md**

```markdown
# Changelog

All notable changes to hermes-antigravity-auth.

## [1.6.0] — 2026-05-21

### Added
- 403 handling: shadow-banned/ineligible accounts placed on 24h cooldown with auto-rotation
- Rate limit marking for both header styles (antigravity + gemini-cli) on 429 responses
- Account quota cache: soft threshold rotation before hard rate limits
- `token_watchdog.py`: background daemon for proactive access token refresh

### Changed
- Interceptor rewritten as headers-only httpx event hooks (removed broken `_HttpProxy`)
- Endpoint routing defaults to PROD — daily sandbox rejects free-tier Claude accounts
- Gemini CLI header style only uses PROD endpoint (sandbox endpoints skipped)
- `transform/__init__.py` explicitly exports all submodule symbols

### Fixed
- Account rotation actually skips exhausted accounts (both header styles marked)
- Token refresh persists refreshed tokens to both auth.json and google_oauth.json stores

## [1.5.0] — 2026-05-20

### Added
- Multi-account management with health-score-based rotation
- Dual quota pool tracking (Antigravity + Gemini CLI)
- Device fingerprint generation per account
- `antigravity login`, `antigravity accounts`, `antigravity check` CLI commands
- Hermes provider profile with Antigravity branding in `/model` picker
- Google Search tool registration (`google_antigravity_search`)
- Session recovery: tool_result_missing, thinking_block_order detection

### Changed
- Python port from TypeScript: complete rewrite as pip-installable package
- Stdlib-only (urllib, json, dataclasses) — no heavy framework dependencies

## [1.0.0] — 2026-05-19

### Added
- Initial release: port of opencode-antigravity-auth (NoeFabris)
- PKCE OAuth 2.0 flow for Google Antigravity
- Request/response transformation pipeline (messages, schema, envelope, thinking)
- Claude thinking block stripping and signature handling
- JSON Schema sanitization for Antigravity API compatibility
```

**Step 2: Verify**

Run: `wc -l CHANGELOG.md`

**Step 3: Commit**

```bash
git add CHANGELOG.md
git commit -m "docs: add CHANGELOG.md with version history"
```

---

### Task 22: Update AGENTS.md and pyproject.toml for consistency

**Objective:** Ensure all docs agree on Python >= 3.11 requirement.

**Files:**
- Modify: `antigravity_auth/AGENTS.md` (confirm 3.11)
- Modify: `pyproject.toml` (already done in Task 1)
- Modify: `README.md:6` (badge says 3.11+, confirm)

**Step 1: Verify README badge matches**

Run: `grep "python" README.md | head -5`
Expected: shows Python 3.11+

**Step 2: Run final test suite**

```bash
python3 -m pytest antigravity_auth/ -v
```
Expected: all tests pass (target: 445+ tests after all additions)

**Step 3: Commit**

```bash
git add README.md antigravity_auth/AGENTS.md
git commit -m "docs: ensure Python 3.11+ requirement is consistent across all docs"
```

---

## Verification Checklist

After all 22 tasks complete, verify:

- [ ] `python3 -m pytest antigravity_auth/ -v` — all tests pass (target: 445+)
- [ ] `python3 -c "import tomllib; d=tomllib.load(open('pyproject.toml','rb')); print(d['project']['requires-python'])"` — shows `>=3.11`
- [ ] `git status` — no .DS_Store, no __pycache__ files
- [ ] `ls LICENSE` — file exists
- [ ] `ls CHANGELOG.md` — file exists
- [ ] `grep -r "import \*" antigravity_auth/install_plugins.py` — no wildcard imports in generated code
- [ ] `grep -r "def _now_ms" antigravity_auth/accounts/ antigravity_auth/fingerprint.py` — no remaining private copies of _now_ms
- [ ] `grep -rn 'decode.*utf.*\"' antigravity_auth/*.py antigravity_auth/*/*.py | grep -v errors | grep -v test_` — no bare decode calls without errors= parameter
- [ ] `python3 -c "from antigravity_auth.accounts.ratelimit import *"` — no ImportError
- [ ] `python3 -c "from antigravity_auth.verification import probe_account_health; print('OK')"` — imports without Hermes runtime

---

## Task Count Summary

| Phase | Tasks | Risk | New Files | Touches Interceptor Chain |
|-------|-------|------|-----------|---------------------------|
| 1: Hygiene | 4 | NONE | LICENSE, .gitignore | No |
| 2: Runtime Bugs | 3 | LOW | — | Yes (token_watchdog, oauth imports) |
| 3: Accounts Tests | 5 | NONE | test_ratelimit, test_quota, test_rotation, test_state, test_manager | No (tests only) |
| 4: Integration Tests | 4 | NONE | test_fingerprint, test_endpoints, test_envelope, test_response | No (tests only) |
| 5: Code Quality | 4 | MIXED | _time_utils.py | Yes (Task 18 — guarded by 10-step safety procedure) |
| 6: Documentation | 2 | NONE | CHANGELOG.md | No |
| **Total** | **22** | | **11 new files** | |

**Risk key:**
- **NONE** — new files or comments only; cannot break the running plugin
- **LOW** — modifies runtime code, but the changes are confined to CLI-only code paths or verified by tests
- **MODERATE** — Task 18 (extract `_now_ms`); touches the interceptor's dependency chain; mitigated by one-file-at-a-time deployment with manual import verification after every file
- **HIGH** — no tasks remain at this level (Task 19 was demoted from code change to comment-only)

---

## Phase 7: Auto-Update Version Check (Bonus)

### Task 23: Add version check on plugin load

**Objective:** The plugin fires a daemon thread on startup that compares the installed version against the latest GitHub release. If a newer version exists, it prints a notification. Checks are cached to once per day.

**Why this is safe:** This is a read-only HTTP GET to a public GitHub API. It runs in a daemon thread — never blocks plugin load. Uses `urllib.request` (already depended on). The cache file (`~/.hermes/antigravity-version-check.json`) is written atomically. If GitHub is unreachable, it fails silently.

**Design:**
- On plugin load, `hermes_plugin.py::register()` calls `version.start_version_check()`
- `start_version_check()` spawns a daemon thread that:
  1. Reads `~/.hermes/antigravity-version-check.json` — if checked in the last 24 hours, exit
  2. GETs `https://api.github.com/repos/Reedtrullz/hermes-antigravity-auth/releases/latest`
  3. Parses `"tag_name"` (expects `v1.7.0` format) and compares against installed version from `pyproject.toml`
  4. If GitHub version > installed, prints a message to stderr
  5. Writes cache file with timestamp of last check

**Files:**
- Create: `antigravity_auth/version.py`
- Modify: `antigravity_auth/hermes_plugin.py:57-62` (add version check call alongside existing watchdog/tools startup)

**Step 1: Create version.py**

Create `antigravity_auth/version.py`:

```python
"""Version check — compares installed version against latest GitHub release."""
from __future__ import annotations

import json
import os
import threading
import time
import urllib.request
from pathlib import Path

GITHUB_API_URL = "https://api.github.com/repos/Reedtrullz/hermes-antigravity-auth/releases/latest"
CHECK_INTERVAL_SECONDS = 86400  # 24 hours
REQUEST_TIMEOUT_SECONDS = 5

_version_thread: threading.Thread | None = None


def _get_installed_version() -> str:
    """Read version from the installed package metadata."""
    try:
        from importlib.metadata import version
        return version("hermes-antigravity-auth")
    except Exception:
        pass
    # Fallback: read pyproject.toml if running from source checkout
    try:
        import tomllib
        repo_root = Path(__file__).resolve().parent.parent
        pyproject = repo_root / "pyproject.toml"
        if pyproject.exists():
            with open(pyproject, "rb") as f:
                data = tomllib.load(f)
            return data.get("project", {}).get("version", "0.0.0")
    except Exception:
        pass
    return "0.0.0"


def _parse_github_tag(tag: str) -> str:
    """Strip leading 'v' from tag names like 'v1.7.0'."""
    return tag.lstrip("v")


def _get_cache_path() -> Path:
    from antigravity_auth.storage import get_hermes_home
    return get_hermes_home() / "antigravity-version-check.json"


def _is_cache_fresh() -> bool:
    cache_path = _get_cache_path()
    if not cache_path.exists():
        return False
    try:
        with open(cache_path) as f:
            data = json.load(f)
        last_check = data.get("last_check", 0)
        return (time.time() - last_check) < CHECK_INTERVAL_SECONDS
    except Exception:
        return False


def _write_cache() -> None:
    cache_path = _get_cache_path()
    tmp_path = cache_path.with_suffix(f".json.{os.getpid()}.tmp")
    try:
        with open(tmp_path, "w") as f:
            json.dump({"last_check": time.time()}, f)
        os.replace(tmp_path, cache_path)
    except Exception:
        pass


def _check_version() -> None:
    """Compare installed version against latest GitHub release."""
    if _is_cache_fresh():
        return

    installed = _get_installed_version()

    try:
        req = urllib.request.Request(
            GITHUB_API_URL,
            headers={"Accept": "application/vnd.github+json",
                     "User-Agent": "hermes-antigravity-auth-version-check"},
        )
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_SECONDS) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="ignore"))
        latest = _parse_github_tag(data.get("tag_name", "0.0.0"))
    except Exception:
        _write_cache()  # cache even on failure — don't hammer GitHub
        return

    _write_cache()

    if _version_newer(latest, installed):
        _notify_update(installed, latest)


def _version_newer(latest: str, installed: str) -> bool:
    """Compare two semver strings. Returns True if latest > installed."""
    try:
        latest_parts = [int(x) for x in latest.split(".")]
        installed_parts = [int(x) for x in installed.split(".")]
        # Pad to same length
        while len(latest_parts) < 3:
            latest_parts.append(0)
        while len(installed_parts) < 3:
            installed_parts.append(0)
        return latest_parts > installed_parts
    except (ValueError, AttributeError):
        return latest != installed  # non-semver: just compare strings


def _notify_update(installed: str, latest: str) -> None:
    """Print update notification to stderr."""
    print(
        f"\n[antigravity] Update available: v{installed} → v{latest}\n"
        f"  Run: pip install --upgrade git+https://github.com/Reedtrullz/hermes-antigravity-auth.git\n",
        flush=True,
    )


def start_version_check() -> None:
    """Start a daemon thread that checks for plugin updates. Idempotent."""
    global _version_thread
    if _version_thread is not None and _version_thread.is_alive():
        return
    _version_thread = threading.Thread(
        target=_check_version, daemon=True, name="antigravity-version-check"
    )
    _version_thread.start()
```

**Step 2: Wire into hermes_plugin.py**

Add the version check call in `hermes_plugin.py::register()`, alongside the existing watchdog/tools startup (after line 62):

```python
  # Start background version check (non-blocking, cached to once per day)
  try:
    from .version import start_version_check
    start_version_check()
  except Exception:
    pass
```

Full context of the register() function tail (after all existing startup calls):

```python
def register(ctx):
  """Register Hermes CLI commands when loaded via entry points."""
  ctx.register_cli_command(
    name="antigravity",
    help="Google Antigravity utilities",
    setup_fn=setup_cli,
    handler_fn=handle_cli,
  )

  # Activate the HTTP interceptor
  try:
    from .interceptor import install as install_interceptor
    install_interceptor()
  except Exception:
    pass

  # Register pre_api_request hook for session recovery
  try:
    ...
  except Exception:
    pass

  # Register Antigravity tools (search, etc.)
  try:
    from .tools import register_tools
    register_tools()
  except Exception:
    pass

  # Start background token refresh watchdog
  try:
    from .token_watchdog import start_watchdog
    start_watchdog()
  except Exception:
    pass

  # Start background version check (non-blocking, cached to once per day)
  try:
    from .version import start_version_check
    start_version_check()
  except Exception:
    pass
```

**Step 3: Create test file**

Create `antigravity_auth/test_version.py`:

```python
"""Tests for antigravity_auth.version."""
import unittest
from antigravity_auth.version import (
    _parse_github_tag,
    _version_newer,
    _get_installed_version,
)


class TestParseGitHubTag(unittest.TestCase):
    def test_strips_v_prefix(self):
        self.assertEqual(_parse_github_tag("v1.7.0"), "1.7.0")

    def test_no_prefix_passthrough(self):
        self.assertEqual(_parse_github_tag("1.7.0"), "1.7.0")

    def test_empty_string(self):
        self.assertEqual(_parse_github_tag(""), "")


class TestVersionNewer(unittest.TestCase):
    def test_latest_is_newer(self):
        self.assertTrue(_version_newer("1.7.0", "1.6.0"))

    def test_installed_is_same(self):
        self.assertFalse(_version_newer("1.6.0", "1.6.0"))

    def test_installed_is_newer(self):
        self.assertFalse(_version_newer("1.5.0", "1.6.0"))

    def test_patch_version(self):
        self.assertTrue(_version_newer("1.6.1", "1.6.0"))

    def test_minor_version(self):
        self.assertTrue(_version_newer("2.0.0", "1.9.9"))

    def test_non_semver_fallback(self):
        # Non-semver tags compare as strings
        self.assertTrue(_version_newer("beta-2", "beta-1"))

    def test_different_lengths(self):
        self.assertTrue(_version_newer("2.0", "1.9.9"))


class TestGetInstalledVersion(unittest.TestCase):
    def test_returns_string(self):
        v = _get_installed_version()
        self.assertIsInstance(v, str)
        # From this repo's pyproject.toml, should be "1.6.0"
        self.assertEqual(v, "1.6.0")
```

**Step 4: Run tests**

```bash
python3 -m pytest antigravity_auth/test_version.py -v
```
Expected: 10 passed

```bash
python3 -m pytest antigravity_auth/ -v
```
Expected: all tests pass (455+ with all new tests)

**Step 5: Manual verification — import without Hermes**

```bash
python3 -c "from antigravity_auth.version import start_version_check, _get_installed_version; print('installed:', _get_installed_version())"
```
Expected: `installed: 1.6.0`

**Step 6: Manual verification — cache file works**

```bash
python3 -c "
from antigravity_auth.version import _is_cache_fresh, _check_version, _get_cache_path
import os
path = _get_cache_path()
if path.exists():
    os.remove(path)
# Run check — should hit GitHub and write cache
_check_version()
print('Cache fresh:', _is_cache_fresh())
print('Cache path:', path)
"
```
Expected: `Cache fresh: True` (if GitHub is reachable), or `Cache fresh: True` (even on failure, cache is written)

**Step 7: Commit**

```bash
git add antigravity_auth/version.py antigravity_auth/hermes_plugin.py antigravity_auth/test_version.py
git commit -m "feat: add version check on plugin load (GitHub releases, cached daily)"
```

---

### Updated Task Count Summary

| Phase | Tasks | Risk | New Files | Touches Interceptor Chain |
|-------|-------|------|-----------|---------------------------|
| 1: Hygiene | 4 | NONE | LICENSE, .gitignore | No |
| 2: Runtime Bugs | 3 | LOW | — | Yes (token_watchdog, oauth imports) |
| 3: Accounts Tests | 5 | NONE | test_ratelimit, test_quota, test_rotation, test_state, test_manager | No |
| 4: Integration Tests | 4 | NONE | test_fingerprint, test_endpoints, test_envelope, test_response | No |
| 5: Code Quality | 4 | MIXED | _time_utils.py | Yes (Task 18 — guarded) |
| 6: Documentation | 2 | NONE | CHANGELOG.md | No |
| 7: Auto-Update (bonus) | 1 | NONE | version.py, test_version.py | No |
| **Total** | **23** | | **13 new files** | |
