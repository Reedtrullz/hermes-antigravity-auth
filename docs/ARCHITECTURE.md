# Architecture Guide

**Last Updated:** May 2026

This document describes the Hermes Agent Python implementation for Google Antigravity OAuth, account management, request transformation, HTTP interception, and CLI integration.

---

## Overview

```
Hermes Agent
  ├─ antigravity-cli plugin: hermes antigravity ...
  ├─ provider aliases: antigravity / antigravity-google / ag / gemini-cli / gemini-oauth
  ├─ Hermes Cloud Code runtime: google-gemini-cli
  ├─ wrap_code_assist_request patch: Claude body hardening before envelope wrapping
  ├─ httpx event hooks: headers, account selection, token/account rotation
  └─ Antigravity auth package: OAuth, storage, accounts, transforms, watchdog
```

The plugin authenticates Google accounts, stores Antigravity account state under `HERMES_HOME`, and registers Antigravity provider aliases that route through Hermes' native `google-gemini-cli` Cloud Code transport. It does not route through OpenRouter. On plugin load, `interceptor.py` patches `GeminiCloudCodeClient.__init__` to attach httpx request/response event hooks, and patches Hermes' `wrap_code_assist_request` helper so Claude-specific body transforms run before the native Cloud Code envelope is built. The request hook is primarily for header injection, account selection, and token sync; response handling covers token refresh and model-family/header-style-aware account rotation. A background watchdog thread proactively refreshes tokens before expiry.

---

## Runtime Components

```
antigravity_auth/
├── cli.py                    # hermes antigravity login/accounts/list/delete/check/quota
├── hermes_plugin.py          # Hermes entry point: registers CLI, interceptor, recovery, tools, watchdog
├── hermes_provider_plugin.py # Branded provider aliases for Hermes model discovery
├── interceptor.py            # HTTP interceptor: monkey-patches GeminiCloudCodeClient, httpx event hooks
├── config.py                 # ~/.hermes/config.yaml loader with env overrides + TTL cache
├── oauth.py                  # PKCE authorize/exchange flow
├── storage.py                # HERMES_HOME-aware auth/account storage
├── token.py                  # Refresh token parsing and refresh
├── token_watchdog.py         # Background daemon thread for proactive token refresh
├── tools.py                  # Hermes tool registration (google_antigravity_search)
├── endpoints.py              # Endpoint helper; runtime selection currently returns PROD
├── accounts/manager.py       # Account selection, cooldowns, persistence, rate-limit rotation
└── transform/                # Header/model helpers plus request/response transform utilities

plugins/
├── antigravity_tools/        # File-system Hermes CLI plugin wrapper
└── model-providers/
    └── antigravity/          # Provider aliases for Hermes model discovery
```

The Python package is the source of truth. The plugin directories are thin Hermes integration wrappers.

---

## Authentication Flow

1. `hermes antigravity login` starts the PKCE OAuth flow in `antigravity_auth/oauth.py`.
2. The callback handler in `antigravity_auth/cli.py` receives the authorization code.
3. `exchange_antigravity()` exchanges the code for access and refresh credentials.
4. The account is written to `~/.hermes/antigravity-accounts.json`.
5. Token state is synced to both:
   - `~/.hermes/auth.json` under both `antigravity` and `google-gemini-cli` provider keys, with `active_provider` set to the canonical `google-gemini-cli` runtime provider
   - `~/.hermes/auth/google_oauth.json` for Hermes' native Cloud Code runtime

`HERMES_HOME` overrides the base directory for all Hermes-owned files.

OAuth client credentials are not assumed to be bundled in source/git installs.
They must be supplied by `ANTIGRAVITY_CLIENT_ID` /
`ANTIGRAVITY_CLIENT_SECRET`, or by the external Hermes credentials file
`~/.hermes/antigravity-credentials.json`. The package-tree
`antigravity_auth/_credentials.py` path is legacy reference only and is not the
recommended credential location.

---

## Provider Registration

The model provider plugin at `plugins/model-providers/antigravity` registers a `ProviderProfile` named `google-gemini-cli` with Antigravity aliases:

- `antigravity`
- `antigravity-google`
- `ag`
- `gemini-cli`
- `gemini-oauth`

This delegates to Hermes' native `google-gemini-cli` Cloud Code transport. The canonical runtime provider remains `google-gemini-cli`; the Antigravity names are aliases and picker branding.

Typical usage:

```bash
hermes -z "Hello" --provider antigravity --model claude-opus-4-6-thinking
hermes -z "Hello" --provider ag --model gemini-3.1-pro-high
```

---

## Model Inventory And Naming

`antigravity_auth/hermes_provider_plugin.py` is the source of truth for the
provider model list exposed to Hermes' model picker. Current registered model
IDs include:

| Family | Model IDs | Notes |
|--------|-----------|-------|
| Claude | `claude-sonnet-4-6`, `claude-sonnet-4-6-thinking`, `claude-opus-4-6-thinking` | Passed through to Antigravity/Claude routing |
| Gemini 3.5 | `gemini-3.5-flash-medium`, `gemini-3.5-flash-high` | Antigravity 2.0 names |
| Gemini 3.1 | `gemini-3.1-pro-low`, `gemini-3.1-pro-high` | Antigravity 2.0 names; no `-preview` suffix |
| Gemini 3.0 legacy | `gemini-3-pro-preview`, `gemini-3-flash-preview` | Legacy Gemini 3.0 names keep `-preview` |
| Gemini 2.5 legacy | `gemini-2.5-flash`, `gemini-2.5-pro` | Registered fallback models |
| GPT OSS | `gpt-oss-120b-medium` | Registered model ID |

Do not generalize a suffix rule across all Gemini models. The current runtime
uses bare Antigravity 2.0 IDs for Gemini 3.1/3.5 and legacy `-preview` IDs only
for Gemini 3.0.

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
| `ANTIGRAVITY_CLIENT_ID` | OAuth desktop client ID for source/git installs |
| `ANTIGRAVITY_CLIENT_SECRET` | OAuth desktop client secret for source/git installs |

---

## Account Storage

Location: `~/.hermes/antigravity-accounts.json`

The account manager stores OAuth refresh tokens, project IDs, active indices, per-family active accounts, cooldowns, quota cache state, and fingerprint metadata. Writes are atomic and honor `HERMES_HOME`.

Sensitive files:

- `~/.hermes/antigravity-accounts.json`
- `~/.hermes/auth.json`
- `~/.hermes/auth/google_oauth.json`

---

## HTTP Interception

On plugin load, `hermes_plugin.py` calls `interceptor.install()`. The install path patches two Hermes internals:

- `GeminiCloudCodeClient.__init__`, adding httpx request/response event hooks to the internal `self._http` client.
- `agent.gemini_cloudcode_adapter.wrap_code_assist_request`, applying Claude-specific body transforms before Hermes builds the Code Assist envelope.

The **request hook** is primarily headers and account selection:
- Reads the Code Assist envelope only to determine the model and model family.
- Chooses header style: `antigravity` by default, or deprecated `gemini-cli` for Gemini models when `cli_first: true`.
- Selects the current account, refreshes its access token, and syncs `auth.json` / `auth/google_oauth.json` for the canonical `google-gemini-cli` runtime.
- Replaces `User-Agent`, `X-Goog-Api-Client`, and `Client-Metadata` with Antigravity or Gemini CLI style headers.
- Injects per-request device fingerprint metadata into `Client-Metadata` when available.
- Preserves critical headers such as `Authorization`, `Content-Type`, `Host`, `Accept`, `Accept-Encoding`, and `Content-Length`.
- Does not rewrite the request body.

The **Claude wrapper patch** performs the body work for Claude models:
- Injects IDs into Gemini `functionCall` / `functionResponse` parts so the Antigravity Claude backend can map them to Anthropic `tool_use` / `tool_result` blocks.
- Runs `_apply_claude_transforms()`: strips stale thinking blocks unless `keep_thinking` is enabled, forces `toolConfig.functionCallingConfig.mode = "VALIDATED"`, converts `thinkingConfig` keys to snake_case, and adds placeholder required fields for empty tool schemas.

The **response hook** handles side effects:
- Refreshes OAuth tokens on 401 when `proactive_token_refresh: true`.
- Puts accounts on cooldown and rotates after 403 failures.
- Marks model/header-style-specific rate limits and rotates after 429 when `switch_on_first_rate_limit: true`.
- Marks endpoints as failed on 5xx server errors for the internal endpoint helper. Current endpoint selection still returns PROD, so this is not a full automatic endpoint fallback chain.

**Why a headers-first hook works:** Hermes already sends a Cloud Code request envelope (`{project, model, user_prompt_id, request}`) accepted by the production `cloudcode-pa.googleapis.com` endpoint. The request hook changes headers, credentials, and account state while preserving that native body. Claude-specific body hardening happens earlier in the wrapper patch, not in the httpx request hook.

---

## Request And Transform Helpers

The transform package contains the Python equivalents for request/response adaptation:

| Module | Purpose | Current runtime use |
|--------|---------|---------------------|
| `transform/messages.py` | OpenAI-style messages to Gemini `contents[].parts[]` | Utility/test coverage; Hermes native Cloud Code path already supplies Gemini-style inner requests |
| `transform/thinking.py` | Claude/Gemini thinking block filtering and signature helpers | Used by `_apply_claude_transforms()` for Claude request hardening |
| `transform/schema.py` | JSON Schema allowlist sanitization helpers | Utility/test coverage; the current wrapper patch only adds Claude placeholder required fields |
| `transform/envelope.py` | Model-name mapping, header randomization, direct Antigravity envelope helpers | Header/model helpers are used by the httpx hook; direct envelope helpers are used by tools/tests |
| `transform/response.py` | Usage extraction, error rewriting, and non-stream response unwrap; streaming SSE is passed through unchanged | Utility/test coverage for direct Antigravity response adaptation; current runtime uses native Hermes Cloud Code stream parsing |

These modules are covered by unit tests, but they are not all on the same
runtime path. The current Hermes Cloud Code path uses the wrapper patch for
Claude body transforms and the httpx event hooks for headers/account selection.

---

## Multi-Account Behavior

Account selection is handled by `antigravity_auth/accounts/manager.py`.

Key behavior:

- Sticky account selection until a rate limit or cooldown requires rotation
- Separate active account tracking for Claude and Gemini families
- Header-style aware rate-limit tracking for Gemini quota pools
- Health-score based selection and recovery
- Optional PID offset for parallel Hermes sessions
- Cached quota state can be honored by soft quota thresholds when present; live quota polling does not currently populate that cache automatically

---

## Token Watchdog

`antigravity_auth/token_watchdog.py` runs a background daemon thread that polls token expiry every `proactive_refresh_check_interval_seconds` (default 300s). When a token is within `proactive_refresh_buffer_seconds` (default 1800s) of expiry, the watchdog refreshes it proactively and syncs the new credentials to Hermes' OAuth store. This prevents 401 errors on in-flight requests.

---

## Quota API Integration

`antigravity_auth/accounts/quota.py` provides `fetch_quota_from_api()` which calls `v1internal:retrieveUserQuota` against production Cloud Code. The `hermes antigravity check` and `hermes antigravity quota` CLI commands use this to display live quota usage from Google's API.

Soft quota selection is separate: `AccountManager` can avoid accounts whose stored `cachedQuota` data is fresh and over `soft_quota_threshold_percent`, but the current CLI quota fetch only displays quota and does not automatically write fresh `cachedQuota` back into account state.

---

## Google Search Tool

`antigravity_auth/tools.py` registers `google_antigravity_search` with Hermes' tool registry. The tool refreshes the active account's access token, then calls `antigravity_auth/search.py` which wraps the query in the Antigravity envelope and returns grounded search results with citations.

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
hermes -z "Hello" --provider antigravity --model gemini-3.1-pro-high
```

Check the Hermes home directory for account and auth state:

```bash
ls ~/.hermes/
ls ~/.hermes/auth/
```

---

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
