# Hermes Antigravity Auth

**Google Antigravity OAuth for Hermes Agent** — access Claude Opus 4.6, Sonnet 4.6, Gemini 3.1 Pro, and more via Google OAuth.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](pyproject.toml)

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

### 1. Install the Python package

```bash
pip install hermes-antigravity-auth
```

Or from source:

```bash
git clone https://github.com/Reedtrullz/hermes-antigravity-auth.git
cd hermes-antigravity-auth
pip install -e .
```

### 2. Install the Hermes plugins

Install the CLI and model-provider wrappers into your Hermes plugins directory:

```bash
hermes-antigravity-install
```

From a source checkout, copying the plugin directories also works:

```bash
# From the repo root:
cp -r plugins/model-providers/antigravity ~/.hermes/plugins/model-providers/
cp -r plugins/antigravity_tools ~/.hermes/plugins/antigravity-cli/
```

### 3. Enable the CLI plugin

Add to `~/.hermes/config.yaml`:

```yaml
plugins:
  enabled:
    - antigravity-cli
```

### 4. Authenticate

```bash
hermes antigravity login
```

This opens a browser window for Google OAuth. The provider is automatically registered after authentication.

### 5. Use it

```bash
hermes -z "Hello" --provider antigravity --model claude-opus-4-6-thinking
```

Or use the alias:

```bash
hermes -z "Hello" --provider ag --model claude-opus-4-6-thinking
```

</details>

<details>
<summary><b>For LLM Agents</b></summary>

### Step-by-Step Instructions

1. Install the Python package:
   ```bash
   pip install hermes-antigravity-auth
   ```

2. Install the Hermes plugin wrappers:
   ```bash
   hermes-antigravity-install
   ```

   Or copy the source plugin directories to the Hermes plugins directory:
   ```bash
   cp -r plugins/model-providers/antigravity ~/.hermes/plugins/model-providers/
   cp -r plugins/antigravity_tools ~/.hermes/plugins/antigravity-cli/
   ```

3. Enable the CLI plugin in `~/.hermes/config.yaml`:
   ```yaml
   plugins:
     enabled:
       - antigravity-cli
   ```

4. Authenticate:
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
| **Gemini 3.1 Pro / 3 Pro** | Thinking models via Antigravity quota |
| **Gemini CLI quota** | Separate quota pool for Gemini models |
| **Multi-account** | Add multiple Google accounts, auto-rotate on rate limits |
| **Session recovery** | Auto-recover from tool errors |
| **Google Search** | Web search grounding for Gemini models |

### Available Models

**Antigravity quota** (default for Claude and Gemini):

| Model | Alias | Thinking |
|-------|-------|----------|
| `gemini-3-pro` | `--provider ag` | low, high |
| `gemini-3.1-pro` | `--provider ag` | low, high |
| `gemini-3-flash` | `--provider ag` | minimal, low, medium, high |
| `claude-sonnet-4-6` | `--provider ag` | — |
| `claude-opus-4-6-thinking` | `--provider ag` | low, max |

**Gemini CLI quota** (fallback or `cli_first: true`):

| Model | Notes |
|-------|-------|
| `gemini-2.5-flash` | Fallback |
| `gemini-2.5-pro` | Fallback |
| `gemini-3-flash-preview` | Preview |
| `gemini-3-pro-preview` | Preview |

### Using Variants

```bash
hermes -z "Solve this" --provider antigravity --model claude-opus-4-6-thinking --variant=max
```

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
      keep_thinking: false
      session_recovery: true
      cli_first: false
      debug: false
      quiet_mode: false
```

| Option | Default | What it does |
|--------|---------|--------------|
| `keep_thinking` | `false` | Preserve Claude's thinking across turns |
| `session_recovery` | `true` | Auto-recover from tool errors |
| `cli_first` | `false` | Route Gemini models to Gemini CLI quota first |
| `debug` | `false` | Enable debug file logging |
| `quiet_mode` | `false` | Suppress notifications |

### Environment Variables

| Variable | What it does |
|----------|--------------|
| `ANTIGRAVITY_CLIENT_ID` | OAuth client ID (defaults to built-in credentials) |
| `ANTIGRAVITY_CLIENT_SECRET` | OAuth client secret (defaults to built-in credentials) |
| `HERMES_ANTIGRAVITY_DEBUG=1` | Enable debug file logging |
| `HERMES_ANTIGRAVITY_DEBUG_TUI=1` | Enable debug output in Hermes UI integrations |

> OAuth credentials are loaded from `antigravity_auth/_credentials.py` (local, gitignored) or environment variables. When installed via pip, the credentials are bundled with the package. For local development from the repo, set the `ANTIGRAVITY_CLIENT_ID` and `ANTIGRAVITY_CLIENT_SECRET` env vars.

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

If Hermes doesn't find the model provider, verify the plugin is installed:

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

Antigravity works OOTB. For Gemini CLI models, you need:
1. A Google Cloud project with the **Gemini for Google Cloud API** enabled
2. Set `projectId` in `~/.hermes/antigravity-accounts.json`

### Session Recovery

If a session errors out:
```bash
continue  # triggers auto-recovery
```

---

## Migrating from OpenCode

Previously an OpenCode npm plugin (`opencode-antigravity-auth`)? See the [Migration Guide](MIGRATION.md) for a smooth transition.

Key differences:

| Area | OpenCode | Hermes |
|------|----------|--------|
| Config dir | `~/.config/opencode/` | `~/.hermes/` |
| Login | `opencode auth login` | `hermes antigravity login` |
| Package | npm | pip |
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
│   ├── config.py            # YAML config with env overrides
│   ├── oauth.py             # PKCE OAuth flow
│   ├── token.py             # Token refresh & validation
│   ├── storage.py           # Persistent account storage
│   ├── cli.py               # CLI login, account management
│   ├── search.py            # Google Search via Antigravity API
│   ├── recovery.py          # Session recovery
│   ├── accounts/            # Multi-account management
│   │   ├── manager.py       # Account rotation & selection
│   │   ├── quota.py         # Dual quota pool tracking
│   │   ├── ratelimit.py     # Rate limit handling & backoff
│   │   └── rotation.py      # Health-score rotation
│   └── transform/           # Request/response transformation
│       ├── messages.py      # OpenAI → Gemini content format
│       ├── thinking.py      # Claude thinking block stripping
│       ├── schema.py        # JSON schema sanitization
│       ├── envelope.py      # Antigravity request wrapping
│       └── response.py      # SSE streaming response parsing
├── docs/                    # Documentation
│   ├── ARCHITECTURE.md      # Architecture guide
│   └── ANTIGRAVITY_API_SPEC.md  # API reference
├── plugins/
│   ├── model-providers/     # Hermes model provider plugin
│   └── antigravity_tools/   # Hermes CLI plugin
├── pyproject.toml           # Python package config
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
