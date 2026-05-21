# antigravity_auth — Hermes Antigravity Plugin (Python)

Hermes Agent plugin for Google Antigravity OAuth (Python). OAuth, request transform, quota management, multi-account rotation, session recovery. Ported from the original `opencode-antigravity-auth` npm plugin.

## Structure

```
antigravity_auth/
├── config.py             # Config dataclass, YAML loader, env var overrides
├── oauth.py              # PKCE authorization + token exchange
├── token.py              # Access token refresh, expiry detection, OAuth errors
├── storage.py            # ~/.hermes/antigravity-accounts.json + auth.json
├── cli.py                # OAuth callback server, login flow, account management
├── search.py             # Google Search tool via Antigravity API
├── recovery.py           # Session recovery: tool_result_missing, thinking block errors
├── debug.py              # Structured logging with 25-file rotation
├── verification.py       # Account health probe, validation_required detection
├── endpoints.py          # Endpoint fallback chain (daily → autopush → prod)
├── fingerprint.py        # Per-account device identity generation
├── constants.py          # Platform detection, default Antigravity headers
├── accounts/             # Multi-account management
│   ├── manager.py        # AccountManager: selection, rotation, disk persistence
│   ├── state.py          # ManagedAccount, RateLimitState dataclasses
│   ├── quota.py          # Dual quota pool (Antigravity + Gemini CLI) tracking
│   ├── ratelimit.py      # Rate limit dedup window, exponential backoff, cooldowns
│   ├── rotation.py       # HealthScoreTracker for account scoring
│   └── quota_display.py  # Color-coded quota progress bars for CLI
└── transform/            # Request/response transformation pipeline
    ├── messages.py       # OpenAI messages → Gemini contents[].parts[]
    ├── thinking.py       # Claude thinking block stripping + signature handling
    ├── schema.py         # JSON schema allowlist sanitization
    ├── envelope.py       # Antigravity request envelope + header building
    └── response.py       # SSE streaming response → chat-completions format
```

## Key Patterns

- **Stdlib-only**: Uses `urllib.request`, `json`, `dataclasses`, `threading` — no heavy deps
- **Graceful degradation**: Defensive try/except with fallback values (never crashes)
- **Config cascade**: YAML file → env var overrides → defaults
- **Colocated tests**: `test_*.py` next to source
- **No empty __init__.py**: `transform/__init__.py` explicitly exports all submodule APIs

## Testing

```bash
python3 -m pytest antigravity_auth/ -v                        # Full suite
python3 -m pytest antigravity_auth/test_config.py             # Single file
python3 -m pytest -k "test_name_pattern"                      # Filtered
```

## Dependencies

- Python >= 3.11 (no f-string backslashes, modern setuptools)
- `pyyaml` (optional — for YAML config)
- `pytest`, `pytest-cov` (dev)

## Submodule Guidance

- [accounts/AGENTS.md](accounts/AGENTS.md) — Multi-account management, quota, rotation
- [transform/AGENTS.md](transform/AGENTS.md) — Messages, schema, envelope, response pipeline
