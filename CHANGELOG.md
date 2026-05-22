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
