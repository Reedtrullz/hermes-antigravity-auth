# accounts/ — Multi-Account Management

Account lifecycle, quota tracking, rate limiting, and rotation for Antigravity OAuth.

## Structure

```
accounts/
├── manager.py       # AccountManager: selection, rotation, disk persistence
├── state.py         # ManagedAccount, RateLimitState dataclasses
├── quota.py         # Dual quota pool (Antigravity + Gemini CLI) tracking
├── ratelimit.py     # Rate limit dedup, exponential backoff, cooldowns
├── rotation.py      # HealthScoreTracker for account scoring
├── quota_display.py # Color-coded quota progress bars for CLI
└── test_*.py        # Colocated tests per file
```

## Key Patterns

- **Dual quota pools**: Each account tracks 2 pools — `antigravity` and `gemini_cli` — independently
- **PID-based isolation**: Temp file writes use `os.getpid()` to prevent multi-process collisions (storage.py pattern)
- **Thread-safe persistence**: `_request_save_to_disk()` debounces with timer cancel on lock (not boolean flag)
- **Health-score rotation**: `HealthScoreTracker` scores accounts by success rate, penalty on failures
- **State is flat dicts**: No ORM/dataclass nesting — accounts stored as `dict[str, Any]` in JSON

## Anti-Patterns

- **No singleton manager**: `AccountManager` is created per-use, not global — don't add global state
- **No blocking on quota checks**: `_is_over_quota_simple()` and `_is_over_soft_quota()` check thresholds, never sleep
- **No nested locks**: Lock ordering is `manager._lock` → sub-component — never invert

## Testing

```bash
python3 -m pytest antigravity_auth/accounts/ -v
```

Test files are colocated (`test_*` next to source). Mock `urllib.request.urlopen` for network calls.