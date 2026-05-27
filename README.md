# Hermes Antigravity Auth

**Google Antigravity OAuth for Hermes Agent** — access Claude Opus 4.6, Sonnet 4.6, Gemini 3.1 Pro, and more via Google OAuth.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](pyproject.toml)

---

<details open>
<summary><b>⚠️ Terms of Service Warning — Read Before Installing</b></summary>

> [!CAUTION]
> Using this plugin (and any proxy for Antigravity) violates Google's Terms of Service. A number of users have reported their Google accounts being **banned** or **shadow-banned** (restricted access without explicit notification).
>
> **By using this plugin, you acknowledge:**
> - This is an unofficial tool not endorsed by Google
> - Your account may be suspended or permanently banned
> - You assume all risks associated with using this plugin

</details>

---

## Quick Install

<details open>
<summary><b>For Humans</b></summary>

**Option A: Let an LLM do it**

Paste this into any LLM agent (Claude Code, OpenCode, Cursor, etc.):

```
Install the hermes-antigravity-auth plugin for Hermes Agent by following the instructions at: https://raw.githubusercontent.com/Reedtrullz/hermes-antigravity-auth/main/README.md
```

**Option B: Manual setup**

### 1. Install from source or Git

The current supported install path is this GitHub repository or a local source
checkout. A PyPI package may not be published yet; if one exists in the
future, use the same credential, plugin-install, and authentication steps below.

```bash
pip install git+https://github.com/Reedtrullz/hermes-antigravity-auth.git
```

Or clone and install locally:

```bash
git clone https://github.com/Reedtrullz/hermes-antigravity-auth.git
cd hermes-antigravity-auth
pip install -e .
```

### 2. Provide OAuth client credentials

Source/git installs do not include private OAuth client credentials. Before
running `hermes antigravity login`, provide your own Google OAuth desktop-client
values via environment variables:

```bash
export ANTIGRAVITY_CLIENT_ID="your-client-id.apps.googleusercontent.com"
export ANTIGRAVITY_CLIENT_SECRET="your-client-secret"
```

For an editable checkout, you may instead create a local gitignored credentials
file:

```bash
cp antigravity_auth/_credentials.py.example antigravity_auth/_credentials.py
$EDITOR antigravity_auth/_credentials.py
```

Do not commit credentials or paste real secrets into issue reports.

### 3. Install the Hermes plugins

Install the CLI and model-provider wrappers into your Hermes plugins directory:

```bash
hermes-antigravity-install
```

If the script is not on your shell `PATH`, run the module directly with the Python environment where the package is installed:

```bash
python -m antigravity_auth.install_plugins
```

From a source checkout, copying the plugin directories also works:

```bash
# From the repo root:
cp -r plugins/model-providers/antigravity ~/.hermes/plugins/model-providers/
cp -r plugins/antigravity_tools ~/.hermes/plugins/antigravity-cli/
```

### 4. Enable the CLI plugin

Add to `~/.hermes/config.yaml`:

```yaml
plugins:
  enabled:
    - antigravity-cli
```

### 5. Authenticate

```bash
hermes antigravity login
```

This opens a browser window for Google OAuth. The provider is automatically registered after authentication.

### 6. Use it

```bash
hermes -z "Hello" --provider antigravity --model claude-opus-4-6-thinking
```

Or use the alias:

```bash
hermes -z "Hello" --provider ag --model claude-opus-4-6-thinking
```

In `hermes model` and the in-agent `/model` picker, Antigravity appears as
**Google Antigravity**. Internally this still uses Hermes' supported
`google-gemini-cli` Cloud Code runtime.

</details>

<details>
<summary><b>For LLM Agents</b></summary>

### Step-by-Step Instructions

1. Install from GitHub or a local checkout:
   ```bash
   pip install git+https://github.com/Reedtrullz/hermes-antigravity-auth.git
   ```

2. Provide OAuth client credentials before login. Prefer environment variables
   for non-editable installs:
   ```bash
   export ANTIGRAVITY_CLIENT_ID="your-client-id.apps.googleusercontent.com"
   export ANTIGRAVITY_CLIENT_SECRET="your-client-secret"
   ```
   For an editable checkout, `antigravity_auth/_credentials.py` is also
   supported. Copy `antigravity_auth/_credentials.py.example` and fill it in
   locally; never commit it.

3. Install the Hermes plugin wrappers:
   ```bash
   hermes-antigravity-install
   ```

   If that script is not on `PATH`, use:
   ```bash
   python -m antigravity_auth.install_plugins
   ```

   Or copy the source plugin directories to the Hermes plugins directory:
   ```bash
   cp -r plugins/model-providers/antigravity ~/.hermes/plugins/model-providers/
   cp -r plugins/antigravity_tools ~/.hermes/plugins/antigravity-cli/
   ```

4. Enable the CLI plugin in `~/.hermes/config.yaml`:
   ```yaml
   plugins:
     enabled:
       - antigravity-cli
   ```

5. Authenticate:
   ```bash
   hermes antigravity login
   ```

### Verification

```bash
hermes -z "Hello" --provider antigravity --model claude-opus-4-6-thinking
```

</details>

---

## What You Get

| Feature | Description |
|---------|-------------|
| **Claude Opus 4.6** | Extended thinking via Antigravity |
| **Claude Sonnet 4.6** | Fast, capable model |
| **Gemini 3.5 Flash / 3.1 Pro / 3.0 legacy** | Current Antigravity/Gemini model IDs |
| **Gemini CLI header style** | Optional/deprecated Gemini quota style for Gemini models when `cli_first: true` |
| **Multi-account** | Add multiple Google accounts, rotate on 403/429 rate-limit or auth failures |
| **Session recovery signals** | Best-effort detection/toasts for known tool-result and thinking-block errors |
| **Google Search** | Web search grounding for Gemini models |

### How It Works

Provider aliases (`antigravity`, `antigravity-google`, `ag`, `gemini-cli`,
`gemini-oauth`) all resolve to Hermes' canonical `google-gemini-cli` Cloud Code
runtime. This plugin does not route through OpenRouter.

On plugin load, `antigravity_auth/interceptor.py` patches two Hermes Cloud Code
paths:

1. **Claude request preparation**: The patched `wrap_code_assist_request` path
   applies Claude-specific body transforms before Hermes wraps the Code Assist
   envelope: tool-call IDs, thinking-block stripping unless `keep_thinking` is
   enabled, `VALIDATED` tool mode, snake_case thinking config, and placeholder
   required fields for empty tool schemas.
2. **httpx request hook**: For `cloudcode-pa` requests, the hook reads the model,
   chooses `antigravity` or deprecated `gemini-cli` header style, selects and
   refreshes the active account, syncs Hermes auth stores, and injects
   Antigravity headers plus device fingerprint metadata. The request hook itself
   does not rewrite the request body.
3. **httpx response hook**: 401 responses can trigger token refresh. 403 and 429
   responses mark the current account unavailable and rotate with model-family
   and header-style awareness. 5xx responses mark the endpoint failed for the
   internal endpoint helper.
4. **Endpoint routing**: Current runtime requests use production
   `cloudcode-pa.googleapis.com`. An endpoint fallback helper exists in code, but
   `select_endpoint()` currently returns PROD and Hermes' Cloud Code runtime is
   not wired to retry alternate sandbox endpoints automatically.
5. **Quota monitoring**: `hermes antigravity check` fetches live quota data from
   Google's API and prints remaining percentage per bucket. Soft quota selection
   only uses cached quota data that is already present in account state.

The runtime request body remains Hermes/Code Assist format except for the
Claude-specific wrapper transforms above. Antigravity behavior is primarily
selected by model ID, account credentials, and header style.

### Available Models

All aliases below route through canonical provider `google-gemini-cli`:
`antigravity`, `antigravity-google`, `ag`, `gemini-cli`, and `gemini-oauth`.

| Family | Model IDs | Notes |
|--------|-----------|-------|
| Claude | `claude-sonnet-4-6`, `claude-sonnet-4-6-thinking`, `claude-opus-4-6-thinking` | Antigravity header style |
| Gemini 3.5 | `gemini-3.5-flash-medium`, `gemini-3.5-flash-high` | Antigravity 2.0 names |
| Gemini 3.1 | `gemini-3.1-pro-low`, `gemini-3.1-pro-high` | Antigravity 2.0 names; no `-preview` suffix |
| Gemini 3.0 legacy | `gemini-3-pro-preview`, `gemini-3-flash-preview` | Legacy names keep `-preview` |
| Gemini 2.5 legacy | `gemini-2.5-flash`, `gemini-2.5-pro` | Registered fallback models |
| GPT OSS | `gpt-oss-120b-medium` | Registered model ID |

Use the exact model ID shown above. The plugin does not define extra model-name
suffix rules beyond the registered IDs; generation options are handled by
Hermes and the underlying Cloud Code runtime.

---

## Configuration

### Plugin Settings

Add settings under `plugins.entries.antigravity` in `~/.hermes/config.yaml`:

```yaml
plugins:
  enabled:
    - antigravity-cli
  entries:
    antigravity:
      # --- Basic ---
      keep_thinking: false
      session_recovery: true
      cli_first: false
      debug: false
      quiet_mode: false

      # --- Rate Limit Scheduling ---
      scheduling_mode: cache_first       # cache_first | balance | performance_first
      max_cache_first_wait_seconds: 60   # seconds to wait for cache-first
      failure_ttl_seconds: 3600          # how long to remember failures

      # --- Quota Protection ---
      soft_quota_threshold_percent: 90   # rotate when cached quota shows this % used
      quota_refresh_interval_minutes: 15
      quota_fallback: false              # reserved; automatic quota fallback is not wired

      # --- Account Selection ---
      account_selection_strategy: hybrid # sticky | hybrid | round-robin
      pid_offset_enabled: false          # vary starting account per process
```

### Basic Options

| Option | Default | What it does |
|--------|---------|--------------|
| `keep_thinking` | `false` | Preserve Claude's thinking across turns |
| `session_recovery` | `true` | Best-effort detection/metadata for known recoverable tool/thinking errors |
| `cli_first` | `false` | Route Gemini models to Gemini CLI quota first |
| `debug` | `false` | Enable debug file logging to `~/.hermes/logs/antigravity/` |
| `quiet_mode` | `false` | Suppress notifications |

### Scheduling

Controls how the plugin waits when accounts are rate-limited.

| Option | Default | What it does |
|--------|---------|--------------|
| `scheduling_mode` | `cache_first` | `cache_first`: wait for the preferred account. `balance`: rotate immediately. `performance_first`: prefer fastest account |
| `max_cache_first_wait_seconds` | `60` | Max seconds to wait in `cache_first` mode before rotating |
| `failure_ttl_seconds` | `3600` | How long a failed account is avoided before retrying |

### Quota Protection

When fresh cached quota data is present in account state, selection can rotate
away from accounts that have crossed the configured soft threshold. The live
quota CLI display does not currently populate that cache automatically.

| Option | Default | What it does |
|--------|---------|--------------|
| `soft_quota_threshold_percent` | `90` | Rotate to next account when cached quota shows this % used |
| `quota_refresh_interval_minutes` | `15` | Interval used for soft quota cache TTL calculation |
| `quota_fallback` | `false` | Reserved/internal; current runtime does not automatically switch header styles on quota exhaustion |

### Account Selection

Controls which account is picked from the multi-account pool.

| Option | Default | What it does |
|--------|---------|--------------|
| `account_selection_strategy` | `hybrid` | `sticky`: keep using the same account. `round-robin`: rotate evenly. `hybrid`: sticky until rate-limited, then rotate |
| `pid_offset_enabled` | `false` | When `true`, parallel Hermes processes start at different accounts — prevents all processes hammering the same account |

### Environment Variables

| Variable | What it does |
|----------|--------------|
| `ANTIGRAVITY_CLIENT_ID` | OAuth client ID required for source/git installs |
| `ANTIGRAVITY_CLIENT_SECRET` | OAuth client secret required for source/git installs |
| `HERMES_ANTIGRAVITY_DEBUG=1` | Enable debug file logging |
| `HERMES_ANTIGRAVITY_DEBUG_TUI=1` | Enable debug output in Hermes UI integrations |

> OAuth credentials are loaded from `ANTIGRAVITY_CLIENT_ID` /
> `ANTIGRAVITY_CLIENT_SECRET` first, or from a local gitignored
> `antigravity_auth/_credentials.py` in an editable checkout. Source/git installs
> should assume credentials are not bundled.

---

## Multi-Account Setup

Add multiple Google accounts for higher combined quota. The plugin auto-rotates when one is rate-limited:

```bash
hermes antigravity login  # Run again to add more accounts
```

**Account management:**
```bash
hermes antigravity accounts    # List accounts and quotas
hermes antigravity check       # Check quota status
```

---

## Documentation

| Doc | Covers |
|-----|--------|
| [Architecture Guide](docs/ARCHITECTURE.md) | Plugin structure, provider aliases, request flow, endpoint helper status, account rotation logic |
| [Antigravity API Spec](docs/ANTIGRAVITY_API_SPEC.md) | Reverse-engineered Antigravity API reference — request envelope, headers, SSE format |

---

## Troubleshooting

> **Quick Reset**: Delete `~/.hermes/antigravity-accounts.json` and re-authenticate.

### File Locations

| File | Path |
|------|------|
| Accounts | `~/.hermes/antigravity-accounts.json` |
| Auth tokens | `~/.hermes/auth.json` and `~/.hermes/auth/google_oauth.json` |
| Hermes config | `~/.hermes/config.yaml` |
| Debug logs | `~/.hermes/logs/antigravity/` |

### Model Not Found

**"Model not found" or HTTP 404**: Use one of the exact model IDs in
[Available Models](#available-models). Antigravity 2.0 Gemini 3.1 and 3.5 model
IDs do not use a preview suffix; only the Gemini 3.0 legacy IDs shown above keep
that suffix.

```bash
# ✅ Current Antigravity 2.0 IDs
hermes -z "Hello" --provider ag --model gemini-3.1-pro-high
hermes -z "Hello" --provider ag --model gemini-3.5-flash-medium

# ✅ Legacy Gemini 3.0 ID
hermes -z "Hello" --provider ag --model gemini-3-flash-preview
```

If Hermes doesn't see the antigravity provider at all, verify the plugin is installed:

```bash
ls ~/.hermes/plugins/model-providers/antigravity/
# Should show: __init__.py  plugin.yaml
```

### Auth Issues

1. Delete the accounts file:
   ```bash
   rm ~/.hermes/antigravity-accounts.json
   ```
2. Re-authenticate:
   ```bash
   hermes antigravity login
   ```

### 403 Permission Denied

403 can mean the current Google account is blocked/shadow-banned for this
surface, or that project access is missing for the selected header style. For
the deprecated Gemini CLI header style, you may need:
1. A Google Cloud project with the **Gemini for Google Cloud API** enabled
2. Set `projectId` in `~/.hermes/antigravity-accounts.json`

### Session Recovery

Session recovery is best-effort. The plugin detects known Antigravity/Claude
tool-result and thinking-block error signatures and returns recovery metadata
and toast content through Hermes' `pre_api_request` hook. It does not guarantee
automatic recovery for every interrupted session.

If Hermes leaves the conversation open after a recoverable error, retrying with
a short continuation may help:
```bash
continue
```

### Known Limitations

**Model IDs**: Use the exact registered IDs. Gemini 3.1/3.5 Antigravity 2.0
models do not use a preview suffix; Gemini 3.0 legacy models still do.

**Endpoint fallback**: The code includes an endpoint helper and records 5xx
failures, but current runtime selection still uses the production Cloud Code
endpoint.

**Soft quota cache**: Account selection can honor cached quota state when it is
already present, but the live quota check command currently displays quota
without populating that cache automatically.

---

## Migrating from OpenCode

Previously an OpenCode npm plugin (`opencode-antigravity-auth`)? See the [Migration Guide](MIGRATION.md) for a smooth transition.

Key differences:

| Area | OpenCode | Hermes |
|------|----------|--------|
| Config dir | `~/.config/opencode/` | `~/.hermes/` |
| Login | `opencode auth login` | `hermes antigravity login` |
| Package | npm | Python package from source/git; PyPI if published |
| Accounts file | `antigravity-accounts.json` | Same format, compatible |

Accounts file format is identical — just copy it over:

```bash
cp ~/.config/opencode/antigravity-accounts.json ~/.hermes/antigravity-accounts.json
```

---

## Project Structure

```
hermes-antigravity-auth/
├── antigravity_auth/        # Python package (Hermes plugin)
│   ├── config.py            # YAML config with env overrides + TTL cache
│   ├── oauth.py             # PKCE OAuth flow
│   ├── token.py             # Token refresh & validation
│   ├── token_watchdog.py    # Background proactive token refresh
│   ├── storage.py           # Persistent account storage
│   ├── cli.py               # CLI login, account management, quota check
│   ├── interceptor.py       # HTTP interceptor: monkey-patches GeminiCloudCodeClient
│   ├── tools.py             # Hermes tool registration
│   ├── search.py            # Google Search via Antigravity API
│   ├── recovery.py          # Session recovery
│   ├── accounts/            # Multi-account management
│   │   ├── manager.py       # Account rotation & selection
│   │   ├── quota.py         # Dual quota pool tracking + live quota API
│   │   ├── ratelimit.py     # Rate limit handling & backoff
│   │   └── rotation.py      # Health-score rotation
│   └── transform/           # Request/response transformation
│       ├── messages.py      # OpenAI → Gemini content format
│       ├── thinking.py      # Claude thinking block stripping
│       ├── schema.py        # JSON schema sanitization
│       ├── envelope.py      # Antigravity request wrapping + headers
│       └── response.py      # SSE streaming response parsing
├── docs/                    # Documentation
│   ├── ARCHITECTURE.md      # Architecture guide
│   └── ANTIGRAVITY_API_SPEC.md  # API reference
├── plugins/
│   ├── model-providers/     # Hermes model provider plugin
│   └── antigravity_tools/   # Hermes CLI plugin
├── pyproject.toml           # Python package config (v1.6.0)
├── MIGRATION.md             # OpenCode → Hermes migration guide
└── README.md                # This file
```

---

## Development

```bash
# Install locally
pip install -e ".[dev]"

# Run tests
python3 -m pytest antigravity_auth/ -v
```

---

## License

MIT License. See [LICENSE](LICENSE) for details.

<details>
<summary><b>Legal</b></summary>

### Intended Use
- Personal / internal development only
- Respect internal quotas and data handling policies
- Not for bypassing intended limits or production services

### Disclaimer
- Not affiliated with Google. This is an independent open-source project.
- "Antigravity", "Gemini", "Google Cloud", and "Google" are trademarks of Google LLC.
- This plugin is based on [opencode-antigravity-auth](https://github.com/NoeFabris/opencode-antigravity-auth) by NoeFabris.

</details>
