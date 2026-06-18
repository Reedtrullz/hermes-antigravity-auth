# Migration Guide: OpenCode → Hermes Antigravity Auth

## Overview

This guide helps users of `opencode-antigravity-auth` (the OpenCode npm plugin) migrate to `hermes-antigravity-auth` (the Hermes Python package). The core authentication logic and account file format are compatible, making migration straightforward.

---

## Key Differences

| Area | OpenCode (npm) | Hermes (Python) |
|------|---------------|-----------------|
| **Config directory** | `~/.config/opencode/` | `~/.hermes/` |
| **Config format** | `opencode.json` / `antigravity.json` | `config.yaml` (under `plugins.entries.antigravity`) |
| **CLI login** | `opencode auth login` | `hermes antigravity login` |
| **Updates** | Auto-update via plugin system | Run the canonical Git install plus `hermes-antigravity-install` |
| **Notifications** | Toast notifications | Hermes notification system |
| **Package type** | npm plugin | Python package (source/Git install) |

---

## Migration Steps

### 1. Install the package and Hermes wrappers

The canonical install and update path is the Git package plus
`hermes-antigravity-install`. The installer resolves the Python environment used
by the `hermes` launcher, installs this package into that environment, and writes
the Hermes file-plugin wrappers.

```bash
python3 -m pip install --upgrade "hermes-antigravity-auth[yaml] @ git+https://github.com/Reedtrullz/hermes-antigravity-auth.git"
hermes-antigravity-install
```

Do not copy plugin directories by hand; copied wrappers can drift from the
package installed in Hermes' Python.

### 2. Provide OAuth client credentials

Before running `hermes antigravity login`, create a Google OAuth desktop client
with authorized redirect URI `http://localhost:51121/oauth-callback`, then store
its values in Hermes home:

```bash
hermes antigravity set-credentials --client-id "your-client-id.apps.googleusercontent.com"
```

The command prompts for `ANTIGRAVITY_CLIENT_SECRET` with hidden input.
For automation, environment variables are also supported:

```bash
export ANTIGRAVITY_CLIENT_ID="your-client-id.apps.googleusercontent.com"
export ANTIGRAVITY_CLIENT_SECRET="your-client-secret"
```

Alternatively, store the credentials in an external Hermes file outside the
Python package tree at `~/.hermes/antigravity-credentials.json`:

```bash
mkdir -p ~/.hermes
cat > ~/.hermes/antigravity-credentials.json <<'JSON'
{
  "client_id": "your-client-id.apps.googleusercontent.com",
  "client_secret": "your-client-secret"
}
JSON
chmod 600 ~/.hermes/antigravity-credentials.json
```

Do not place real credentials in `antigravity_auth/_credentials.py`.

### 3. Migrate accounts

The `antigravity-accounts.json` file format is compatible between OpenCode and Hermes. Simply copy it:

```bash
cp ~/.config/opencode/antigravity-accounts.json ~/.hermes/antigravity-accounts.json
```

### 4. Set up Hermes config

Copy your plugin settings from `~/.config/opencode/antigravity.json` to your Hermes `config.yaml` at `~/.hermes/config.yaml`:

```yaml
plugins:
  enabled:
    - antigravity-cli
  entries:
    antigravity:
      # Model behavior
      keep_thinking: false
      session_recovery: true
      cli_first: false

      # Account rotation
      account_selection_strategy: hybrid
      pid_offset_enabled: false

      # Quota protection
      soft_quota_threshold_percent: 90
      quota_refresh_interval_minutes: 15
      soft_quota_cache_ttl_minutes: auto

      # Rate limit scheduling
      scheduling_mode: cache_first
      max_cache_first_wait_seconds: 60
      failure_ttl_seconds: 3600

      # App behavior
      quiet_mode: false
      debug: false
      debug_tui: false
```

### 5. Verify

```bash
hermes antigravity login    # Authenticate with Google
hermes antigravity quota    # Check remaining quota
hermes antigravity selftest # Offline transform/package round-trip smoke
```

---

## Configuration Reference

All configuration options for `config.yaml` under `plugins.entries.antigravity`:

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `keep_thinking` | `bool` | `false` | Preserve Claude's thinking across turns |
| `session_recovery` | `bool` | `true` | Auto-recover from tool errors |
| `cli_first` | `bool` | `false` | Route Gemini models to Gemini CLI quota first |
| `account_selection_strategy` | `string` | `"hybrid"` | Account rotation: `"sticky"`, `"hybrid"`, `"round-robin"` |
| `pid_offset_enabled` | `bool` | `false` | Distribute parallel agent sessions across accounts |
| `soft_quota_threshold_percent` | `int` | `90` | Skip account when quota usage exceeds this % |
| `quota_refresh_interval_minutes` | `int` | `15` | Interval used for soft quota cache TTL calculation |
| `soft_quota_cache_ttl_minutes` | `string`/`int` | `"auto"` | Quota cache freshness TTL |
| `scheduling_mode` | `string` | `"cache_first"` | Rate limit behavior: `"cache_first"`, `"balance"`, `"performance_first"` |
| `max_cache_first_wait_seconds` | `int` | `60` | Max wait before switching accounts |
| `failure_ttl_seconds` | `int` | `3600` | Reset failure count after this many seconds |
| `quiet_mode` | `bool` | `false` | Suppress notifications |
| `debug` | `bool` | `false` | Enable debug file logging |
| `debug_tui` | `bool` | `false` | Show debug logs in TUI |

### Environment variable overrides

| Variable | Overrides |
|----------|-----------|
| `HERMES_HOME` | Custom Hermes home directory |
| `HERMES_ANTIGRAVITY_DEBUG` | Enable debug file logging |
| `HERMES_ANTIGRAVITY_DEBUG_TUI` | Enable debug output in Hermes UI integrations |
| `HERMES_ANTIGRAVITY_QUIET` | Suppress Antigravity status output |

---

## Account Management

### Check quotas

```bash
hermes antigravity quota
```

### Add another account

```bash
hermes antigravity login    # Run again to add more accounts
```

### Multi-account tips

| Setup | Recommended Strategy |
|-------|---------------------|
| 1 account | `sticky` |
| 2-5 accounts | `hybrid` (default) |
| 5+ accounts | `round-robin` |
| Parallel agents | Enable `pid_offset_enabled` |

The accounts file at `~/.hermes/antigravity-accounts.json` stores all authenticated sessions.

---

## Troubleshooting

### Quick reset

```bash
rm ~/.hermes/antigravity-accounts.json
hermes antigravity login
```

### 403 Permission denied

If Google returns `FREE_TIER_USER_NOT_ELIGIBLE`, the account is not eligible for
Antigravity free-tier onboarding. Set a GCP project ID for the account so the
plugin can use Google's reported `standard-tier` path:

```bash
hermes antigravity set-project <email_or_index> <project_id>
```

This writes the same `projectId` field in `antigravity-accounts.json`:

```json
{
  "accounts": [
    {
      "email": "your@email.com",
      "refreshToken": "...",
      "projectId": "your-project-id"
    }
  ]
}
```

### All accounts rate-limited

1. Wait for rate limits to expire, or
2. Add more Google accounts via `hermes antigravity login`

### OAuth callback fails

- **Safari**: Disable HTTPS-Only Mode temporarily in Safari > Settings > Privacy
- **Port conflict**: `lsof -i :51121` and kill the stale process
- **Docker/SSH**: Use SSH port forwarding: `ssh -L 51121:localhost:51121 user@remote`

### Config not found

Ensure your `config.yaml` is at `~/.hermes/config.yaml` with the correct structure:

```yaml
plugins:
  entries:
    antigravity:
      # settings here
```

---

## Support

If you encounter issues during migration, please open a GitHub issue at [https://github.com/Reedtrullz/hermes-antigravity-auth/issues](https://github.com/Reedtrullz/hermes-antigravity-auth/issues).
