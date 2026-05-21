# Architecture Guide

**Last Updated:** May 2026

This document describes the Hermes Agent Python implementation for Google Antigravity OAuth, account management, request transformation, HTTP interception, and CLI integration.

---

## Overview

```
Hermes Agent
  ├─ antigravity-cli plugin: hermes antigravity ...
  ├─ antigravity provider alias: antigravity / ag
  ├─ HTTP interceptor: monkey-patches GeminiCloudCodeClient
  │   └─ Transforms Code Assist → Antigravity envelope + headers
  ├─ Hermes Cloud Code runtime: google-gemini-cli
  └─ Antigravity auth package: OAuth, storage, accounts, transforms, watchdog
```

The plugin authenticates Google accounts, stores Antigravity account state under `HERMES_HOME`, and registers Antigravity provider aliases that route through Hermes' native `google-gemini-cli` Cloud Code transport. On plugin load, an HTTP interceptor monkey-patches `GeminiCloudCodeClient.__init__` to install httpx event hooks that transform every request (Code Assist envelope → Antigravity envelope with randomized headers, schema sanitization, thinking-block stripping) and handle responses (token refresh on 401, account rotation on 429, endpoint fallback on 5xx). A background watchdog thread proactively refreshes tokens before expiry.

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
├── endpoints.py              # EndpointProvider with fallback chain (daily → autopush → prod)
├── accounts/manager.py       # Account selection, cooldowns, persistence, rate-limit rotation
└── transform/                # OpenAI/Gemini envelope, schema, SSE helpers

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
   - `~/.hermes/auth.json` under the `antigravity` provider key
   - `~/.hermes/auth/google_oauth.json` for Hermes' native Cloud Code runtime

`HERMES_HOME` overrides the base directory for all Hermes-owned files.

---

## Provider Registration

The model provider plugin at `plugins/model-providers/antigravity` registers a `ProviderProfile` named `google-gemini-cli` with Antigravity aliases:

- `antigravity`
- `antigravity-google`
- `ag`

This delegates to Hermes' native `google-gemini-cli` Cloud Code transport. The HTTP interceptor (`interceptor.py`) then monkey-patches `GeminiCloudCodeClient.__init__` to install httpx event hooks that transform every request before it reaches the network.

Typical usage:

```bash
hermes -z "Hello" --provider antigravity --model claude-opus-4-6-thinking
hermes -z "Hello" --provider ag --model gemini-3.1-pro-preview
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

## HTTP Interception

The interceptor (`antigravity_auth/interceptor.py`) is a 134-line module that injects Antigravity-specific headers into every Cloud Code API request. On plugin load, `hermes_plugin.py` calls `install()` which monkey-patches `GeminiCloudCodeClient.__init__` to add httpx request/response event hooks to the internal `self._http` client.

The **request hook** transforms headers without touching the request body:
- Reads the Code Assist envelope to determine the model
- Replaces `User-Agent`, `X-Goog-Api-Client`, and `Client-Metadata` with randomized Antigravity-style headers
- Injects a per-request device fingerprint into `Client-Metadata`
- Preserves critical headers: `Authorization`, `Content-Type`, `Host`, `Accept`, `Content-Length`

The **response hook** handles side effects:
- Refreshes OAuth tokens on 401 (when `proactive_token_refresh: true`)
- Rotates accounts and syncs tokens on 429 rate limits (when `switch_on_first_rate_limit: true`)
- Marks endpoints as failed on 5xx server errors for the endpoint fallback chain

**Why headers-only works:** The Code Assist request envelope (`{project, model, user_prompt_id, request}`) is structurally compatible with the Antigravity API. The API routes requests based on headers and `model` field — it doesn't require the `userAgent`/`requestId`/`requestType` envelope fields. The key to unlocking Claude (and other non-Gemini models) is the Antigravity-style `User-Agent` and `Client-Metadata` headers.

---

## Request And Transform Helpers

The transform package contains the Python equivalents for request/response adaptation:

| Module | Purpose |
|--------|---------|
| `transform/messages.py` | OpenAI-style messages to Gemini `contents[].parts[]` |
| `transform/thinking.py` | Claude thinking block stripping + signature handling |
| `transform/schema.py` | JSON Schema allowlist sanitization (const, $ref, $defs removal) |
| `transform/envelope.py` | Antigravity request envelope construction + header randomization |
| `transform/response.py` | SSE streaming response parsing + preview access error rewriting |

These modules are called by the HTTP interceptor at runtime. All are covered by unit tests.

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

## Token Watchdog

`antigravity_auth/token_watchdog.py` runs a background daemon thread that polls token expiry every `proactive_refresh_check_interval_seconds` (default 300s). When a token is within `proactive_refresh_buffer_seconds` (default 1800s) of expiry, the watchdog refreshes it proactively and syncs the new credentials to Hermes' OAuth store. This prevents 401 errors on in-flight requests.

---

## Quota API Integration

`antigravity_auth/accounts/quota.py` provides `fetch_quota_from_api()` which calls `v1internal:retrieveUserQuota` with the Antigravity envelope. The `hermes antigravity check` and `hermes antigravity quota` CLI commands use this to display live quota usage (used/limit per quota group) instead of the previous stub that always printed "OK (Active)".

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
hermes -z "Hello" --provider antigravity --model gemini-3.1-pro-preview
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
