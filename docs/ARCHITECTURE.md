# Architecture Guide

**Last Updated:** May 2026

This document describes the Hermes Agent Python implementation for Google Antigravity OAuth, account management, request transformation, and CLI integration.

---

## Overview

```
Hermes Agent
  ├─ antigravity-cli plugin: hermes antigravity ...
  ├─ antigravity provider alias: antigravity / ag
  ├─ Hermes Cloud Code runtime: google-gemini-cli
  └─ Antigravity auth package: OAuth, storage, accounts, transforms
```

The plugin authenticates Google accounts, stores Antigravity account state under `HERMES_HOME`, and registers Antigravity provider aliases that route through Hermes' native `google-gemini-cli` Cloud Code transport (`cloudcode-pa://google`). Login also syncs credentials into Hermes' `auth/google_oauth.json` store so the native runtime can refresh and attach OAuth tokens.

---

## Runtime Components

```
antigravity_auth/
├── cli.py                    # hermes antigravity login/accounts/list/delete/check/quota
├── hermes_plugin.py          # Hermes entry point for the CLI plugin
├── config.py                 # ~/.hermes/config.yaml loader with env overrides
├── oauth.py                  # PKCE authorize/exchange flow
├── storage.py                # HERMES_HOME-aware auth/account storage
├── token.py                  # refresh token parsing and refresh
├── endpoints.py              # Antigravity endpoint fallback metadata
├── accounts/manager.py       # account selection, cooldowns, persistence
└── transform/                # OpenAI/Gemini envelope, schema, SSE helpers

plugins/
├── antigravity_tools/        # file-system Hermes CLI plugin wrapper
└── model-providers/
    └── antigravity/          # provider aliases for Hermes model discovery
```

The Python package is the source of truth. The plugin directories are thin Hermes integration wrappers.

---

## Authentication Flow

1. `hermes antigravity login` starts the PKCE OAuth flow in `antigravity_auth/oauth.py`.
2. The callback handler in `antigravity_auth/cli.py` receives the authorization code.
3. `exchange_antigravity()` exchanges the code for access and refresh credentials.
4. The account is written to `~/.hermes/antigravity-accounts.json`.
5. Token state is synced to both:
   - `~/.hermes/auth.json` under the `antigravity` provider key
   - `~/.hermes/auth/google_oauth.json` for Hermes' native Cloud Code runtime

`HERMES_HOME` overrides the base directory for all Hermes-owned files.

---

## Provider Registration

The model provider plugin at `plugins/model-providers/antigravity` registers a `ProviderProfile` named `google-gemini-cli` with Antigravity aliases:

- `antigravity`
- `antigravity-google`
- `ag`

This is intentional. Hermes v0.14 has native runtime support for `google-gemini-cli` and `cloudcode-pa://google`; generic `oauth_external` providers are not enough to execute requests. Antigravity aliases therefore resolve to the supported Hermes Cloud Code provider while exposing Antigravity model names and account tooling.

Typical usage:

```bash
hermes -z "Hello" --provider antigravity --model claude-opus-4-6-thinking
hermes -z "Hello" --provider ag --model gemini-3.1-pro
```

---

## Configuration

Configuration lives under `plugins.entries.antigravity` in `~/.hermes/config.yaml`:

```yaml
plugins:
  enabled:
    - antigravity-cli
  entries:
    antigravity:
      keep_thinking: false
      session_recovery: true
      cli_first: false
      debug: false
      quiet_mode: false
```

For compatibility with early Python migration snapshots, root-level Antigravity keys are still accepted, but nested plugin config wins on conflicts.

Environment overrides:

| Variable | Purpose |
|----------|---------|
| `HERMES_HOME` | Override `~/.hermes` |
| `HERMES_ANTIGRAVITY_DEBUG` | Enable file debug logging |
| `HERMES_ANTIGRAVITY_DEBUG_TUI` | Enable debug output in Hermes UI integrations |
| `HERMES_ANTIGRAVITY_QUIET` | Suppress status output |
| `HERMES_ANTIGRAVITY_CLI_FIRST` | Prefer Gemini CLI quota for Gemini models |
| `HERMES_ANTIGRAVITY_ACCOUNT_SELECTION_STRATEGY` | Account rotation strategy |
| `HERMES_ANTIGRAVITY_SCHEDULING_MODE` | Rate-limit scheduling mode |

---

## Account Storage

Location: `~/.hermes/antigravity-accounts.json`

The account manager stores OAuth refresh tokens, project IDs, active indices, per-family active accounts, cooldowns, quota cache state, and fingerprint metadata. Writes are atomic and honor `HERMES_HOME`.

Sensitive files:

- `~/.hermes/antigravity-accounts.json`
- `~/.hermes/auth.json`
- `~/.hermes/auth/google_oauth.json`

---

## Request And Transform Helpers

The transform package contains the Python equivalents for request/response adaptation:

| Module | Purpose |
|--------|---------|
| `transform/messages.py` | OpenAI-style messages to Gemini `contents[].parts[]` |
| `transform/thinking.py` | Claude thinking block stripping |
| `transform/schema.py` | JSON Schema allowlist sanitization |
| `transform/envelope.py` | Antigravity request envelope construction |
| `transform/response.py` | SSE and candidate response conversion |

These helpers are covered by unit tests and remain available for Hermes integration paths that need direct Antigravity request wrapping.

---

## Multi-Account Behavior

Account selection is handled by `antigravity_auth/accounts/manager.py`.

Key behavior:

- Sticky account selection until a rate limit or cooldown requires rotation
- Separate active account tracking for Claude and Gemini families
- Header-style aware rate-limit tracking for Gemini quota pools
- Health-score based selection and recovery
- Optional PID offset for parallel Hermes sessions
- Cached quota state with soft quota thresholds

---

## Debugging

Enable debug logging:

```bash
export HERMES_ANTIGRAVITY_DEBUG=1
export HERMES_ANTIGRAVITY_DEBUG_TUI=1
```

Then run:

```bash
hermes antigravity check
hermes -z "Hello" --provider antigravity --model gemini-3.1-pro
```

Check the Hermes home directory for account and auth state:

```bash
ls ~/.hermes/
ls ~/.hermes/auth/
```

---

## Troubleshooting

| Error | Likely Cause | Fix |
|-------|--------------|-----|
| Provider not found | Model provider plugin not installed | Copy `plugins/model-providers/antigravity` to `~/.hermes/plugins/model-providers/` |
| `oauth_external` unsupported | Provider did not resolve to `google-gemini-cli` | Reinstall the current Antigravity provider plugin |
| Missing credentials | Login did not complete or auth store is stale | Run `hermes antigravity login` |
| 429 rate limit | Current account is rate-limited | Add accounts or wait for cooldown |
| Schema field rejected | Tool schema contains unsupported JSON Schema fields | `transform/schema.py` strips or converts unsupported fields |

---

## See Also

- [ANTIGRAVITY_API_SPEC.md](./ANTIGRAVITY_API_SPEC.md)
- [README.md](../README.md)
