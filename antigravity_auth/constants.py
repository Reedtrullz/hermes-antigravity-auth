"""OAuth client credentials, endpoints, default headers, and platform detection."""
import sys

try:
    from .credentials import resolve_oauth_credentials
except ImportError:
    from credentials import resolve_oauth_credentials

# OAuth client credentials — resolve from environment or external Hermes-home file.
# Environment variables take per-field priority over file values.
ANTIGRAVITY_CLIENT_ID, ANTIGRAVITY_CLIENT_SECRET = resolve_oauth_credentials()

_MISSING_CREDENTIALS_ERROR = (
    "Antigravity OAuth credentials not found.\n\n"
    "Options:\n"
    "  1. Set ANTIGRAVITY_CLIENT_ID and ANTIGRAVITY_CLIENT_SECRET env vars\n"
    "  2. Create ~/.hermes/antigravity-credentials.json with client_id/client_secret\n"
    "  3. Reinstall the package: pip install --force-reinstall git+https://github.com/Reedtrullz/hermes-antigravity-auth.git"
)

if not ANTIGRAVITY_CLIENT_ID or not ANTIGRAVITY_CLIENT_SECRET:
    _credentials_valid = False
else:
    _credentials_valid = True


def require_credentials() -> tuple[str, str]:
    """Return (client_id, client_secret) or raise RuntimeError with instructions."""
    if not _credentials_valid:
        raise RuntimeError(_MISSING_CREDENTIALS_ERROR)
    return ANTIGRAVITY_CLIENT_ID, ANTIGRAVITY_CLIENT_SECRET

ANTIGRAVITY_REDIRECT_URI = "http://localhost:51121/oauth-callback"

ANTIGRAVITY_SCOPES = [
    "https://www.googleapis.com/auth/cloud-platform",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "https://www.googleapis.com/auth/cclog",
    "https://www.googleapis.com/auth/experimentsandconfigs",
]

ANTIGRAVITY_ENDPOINT_DAILY = "https://daily-cloudcode-pa.sandbox.googleapis.com"
ANTIGRAVITY_ENDPOINT_AUTOPUSH = "https://autopush-cloudcode-pa.sandbox.googleapis.com"
ANTIGRAVITY_ENDPOINT_PROD = "https://cloudcode-pa.googleapis.com"

ANTIGRAVITY_ENDPOINT_FALLBACKS = [
    ANTIGRAVITY_ENDPOINT_DAILY,
    ANTIGRAVITY_ENDPOINT_AUTOPUSH,
    ANTIGRAVITY_ENDPOINT_PROD,
]

ANTIGRAVITY_LOAD_ENDPOINTS = [
    ANTIGRAVITY_ENDPOINT_PROD,
    ANTIGRAVITY_ENDPOINT_DAILY,
    ANTIGRAVITY_ENDPOINT_AUTOPUSH,
]

ANTIGRAVITY_VERSION_FALLBACK = "2.0.0"

# Gemini CLI headers — DEPRECATED as of May 2026.
# Google is sunsetting the Gemini CLI in favour of Antigravity CLI (agy).
# Gemini CLI access ends 2026-06-18.  These headers and the dual-quota
# pool they represent will stop working after that date.
# Prefer the 'antigravity' header style (Electron UA + fingerprint) going forward.
GEMINI_CLI_HEADERS = {
    "User-Agent": "google-api-nodejs-client/9.15.1",
    "X-Goog-Api-Client": "gl-node/22.17.0",
    "Client-Metadata": "ideType=IDE_UNSPECIFIED,platform=PLATFORM_UNSPECIFIED,pluginType=GEMINI",
}

ANTIGRAVITY_DEFAULT_PROJECT_ID = "rising-fact-p41fc"

ANTIGRAVITY_ACCOUNTS_FILE = "~/.hermes/antigravity-accounts.json"


def get_platform() -> str:
    return "WINDOWS" if sys.platform == "win32" else "MACOS"

def get_antigravity_headers(version: str = ANTIGRAVITY_VERSION_FALLBACK) -> dict:
    platform = get_platform()
    return {
        "User-Agent": f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Antigravity/{version} Chrome/138.0.7204.235 Electron/37.3.1 Safari/537.36",
        "X-Goog-Api-Client": "google-cloud-sdk vscode_cloudshelleditor/0.1",
        "Client-Metadata": f'{{"ideType":"ANTIGRAVITY","platform":"{platform}","pluginType":"GEMINI"}}',
    }
