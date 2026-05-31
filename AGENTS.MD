# AGENTS.md

Guidance for AI agents working with this repository.

## Overview

Hermes Agent plugin for Google Antigravity OAuth. Python package (`antigravity_auth/`) that handles OAuth, request transformation, quota management, multi-account rotation, session recovery, and CLI utilities for accessing Claude and Gemini models via Google credentials.

Originally forked from [opencode-antigravity-auth](https://github.com/NoeFabris/opencode-antigravity-auth) (TypeScript npm plugin). The Python port now lives at `antigravity_auth/` as the primary implementation. Legacy TypeScript source has been removed.

## Build & Test Commands

```bash
pip install -e ".[dev]"                    # Editable install with dev deps
python3 -m pytest antigravity_auth/ -v     # Run all Python tests
python3 -m pytest antigravity_auth/test_config.py  # Single test file
python3 -m pytest -k "test_name"           # Single test by name
```

## Python Conventions

- Tests colocated (`test_*.py` next to source)
- Stdlib-only (urllib, json, dataclasses) — no heavy frameworks
- Try/except with graceful degradation (fallback values, not crashes)
- Named imports only — `from .module import Name`
- PascalCase for classes, snake_case for functions/variables
- 2-space indentation, double quotes, no semicolons
- Config via `~/.hermes/config.yaml` with `HERMES_ANTIGRAVITY_*` env overrides

## Module Structure

```
antigravity_auth/              # Python package (Hermes plugin)
├── config.py                  # YAML config loader with env var overrides
├── oauth.py                   # PKCE OAuth flow (authorize + exchange)
├── token.py                   # Token refresh & validation
├── storage.py                 # Persistent account/credential storage
├── cli.py                     # CLI subcommand handlers (login, accounts, check)
├── search.py                  # Google Search tool via Antigravity API
├── recovery.py                # Session recovery (tool_result_missing)
├── debug.py                   # Structured logging with rotation
├── verification.py            # Account health probe
├── endpoints.py               # Endpoint fallback chain (daily → autopush → prod)
├── fingerprint.py             # Device fingerprint generation
├── constants.py               # Platform detection, default headers
├── accounts/                  # Multi-account management
│   ├── manager.py             # Account rotation, selection strategies
│   ├── state.py               # Account state dataclasses
│   ├── quota.py               # Dual quota pool tracking
│   ├── ratelimit.py           # Rate limit handling & backoff
│   ├── rotation.py            # Health-score-based rotation
│   └── quota_display.py       # CLI quota progress bars
└── transform/                 # Request/response transformation
    ├── messages.py            # OpenAI → Gemini contents format
    ├── thinking.py            # Claude thinking block stripping
    ├── schema.py              # JSON schema sanitization
    ├── envelope.py            # Antigravity request wrapping
    └── response.py            # SSE streaming response transformation
```

## Key Design Patterns

### 1. Request Interception & Transformation
Plugin transforms OpenAI-format messages to Gemini-format `contents[].parts[]`, then wraps them in the Antigravity envelope. Two header styles: `antigravity` (Electron-style UA + fingerprint) and `gemini-cli` (nodejs-client UA).

### 2. Claude Thinking Blocks
ALL thinking blocks are stripped from outgoing requests for Claude models. Claude generates fresh thinking each turn. This eliminates signature validation errors.

### 3. Session Recovery
When tool execution is interrupted (ESC/timeout), the plugin injects synthetic `tool_result` blocks to recover the session without starting over.

### 4. Schema Sanitization
Tool schemas are cleaned via allowlist. Unsupported fields (`const`, `$ref`, `$defs`) are removed or converted to Antigravity-compatible format.

### 5. Multi-Account Load Balancing
Accounts rotate on rate limits. Gemini has dual quota pools (Antigravity headers + Gemini CLI headers). Fingerprints are per-account and regenerated on capacity exhaustion.

### 6. Fingerprint System
Per-account device fingerprints stored in `antigravity-accounts.json`. Each fingerprint includes deviceId, sessionToken, userAgent, and clientMetadata. History tracked (max 5), restorable.

### 7. Security Discipline
Debug logs (0o700 dir, 0o600 files, 25-file rotation) and trace files (0o700 dir, 0o600 files, 50-file rotation) use private permissions. `redaction.py` strips OAuth tokens, bearer strings, session tokens, and client secrets from all diagnostic output. The `packaging_guard.py` module plus `MANIFEST.in` prevent `_credentials.py` from shipping in wheels/sdist.

## Documentation

- [README.md](README.md) — Installation & usage
- [MIGRATION.md](MIGRATION.md) — OpenCode → Hermes migration guide
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — Detailed architecture guide
- [docs/ANTIGRAVITY_API_SPEC.md](docs/ANTIGRAVITY_API_SPEC.md) — Antigravity API reference
- [antigravity_auth/AGENTS.md](antigravity_auth/AGENTS.md) — Python module deep-dive
- [antigravity_auth/accounts/AGENTS.md](antigravity_auth/accounts/AGENTS.md) — Multi-account management
- [antigravity_auth/transform/AGENTS.md](antigravity_auth/transform/AGENTS.md) — Request/response transform

## Testing

- Framework: **pytest** with unittest.TestCase
- Run: `python3 -m pytest antigravity_auth/ -v`
- Tests colocated: `test_*.py` next to source
- Config: no conftest.py or pytest.ini needed