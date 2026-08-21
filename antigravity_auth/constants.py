"""OAuth client credentials, endpoints, default headers, and platform detection."""
import platform as _platform
import sys

try:
    from .credentials import MissingOAuthCredentialsError, credential_file_path, resolve_oauth_credentials
except ImportError:
    from credentials import MissingOAuthCredentialsError, credential_file_path, resolve_oauth_credentials

# OAuth client credentials — resolve from environment or external Hermes-home file.
# Environment variables take per-field priority over file values.
ANTIGRAVITY_CLIENT_ID, ANTIGRAVITY_CLIENT_SECRET = resolve_oauth_credentials()


def _missing_credentials_error() -> str:
    return (
        "Antigravity OAuth credentials not found.\n\n"
        "Options:\n"
        "  1. Set ANTIGRAVITY_CLIENT_ID and ANTIGRAVITY_CLIENT_SECRET env vars\n"
        "  2. Run hermes antigravity set-credentials --client-id <id> and enter the secret at the hidden prompt\n"
        f"  3. Create {credential_file_path()} with client_id/client_secret\n"
    )

if not ANTIGRAVITY_CLIENT_ID or not ANTIGRAVITY_CLIENT_SECRET:
    _credentials_valid = False
else:
    _credentials_valid = True


def require_credentials() -> tuple[str, str]:
    """Return (client_id, client_secret) or raise RuntimeError with instructions."""
    global ANTIGRAVITY_CLIENT_ID, ANTIGRAVITY_CLIENT_SECRET, _credentials_valid
    ANTIGRAVITY_CLIENT_ID, ANTIGRAVITY_CLIENT_SECRET = resolve_oauth_credentials()
    _credentials_valid = bool(ANTIGRAVITY_CLIENT_ID and ANTIGRAVITY_CLIENT_SECRET)
    if not _credentials_valid:
        raise MissingOAuthCredentialsError(_missing_credentials_error())
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
ANTIGRAVITY_ENDPOINT_PROD = "https://daily-cloudcode-pa.googleapis.com"

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

ANTIGRAVITY_IDE_VERSION = "2.5.5"
_IDE_USER_AGENT_CACHE: str | None = None


def ide_user_agent() -> str:
    """Return the Antigravity IDE User-Agent matching the real IDE client.

    The backend validates the User-Agent against the OAuth credential's client
    identity. Sending a non-IDE UA causes 403 VALIDATION_REQUIRED errors.
    """
    global _IDE_USER_AGENT_CACHE
    if _IDE_USER_AGENT_CACHE is not None:
        return _IDE_USER_AGENT_CACHE
    os_type = _platform.system().lower()
    machine = _platform.machine().lower()
    if machine in ("arm64", "aarch64"):
        arch = "arm64"
    elif machine in ("x86_64", "x86", "i386", "i686"):
        arch = "amd64"
    else:
        arch = machine
    ua = (
        f"antigravity/ide/{ANTIGRAVITY_IDE_VERSION} "
        f"(os_type={os_type}; arch={arch}; aidev_client; auth_method=oauth)"
    )
    _IDE_USER_AGENT_CACHE = ua
    return ua

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
    return {
        "User-Agent": ide_user_agent(),
    }
