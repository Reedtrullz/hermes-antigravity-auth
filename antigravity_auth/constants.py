import os
import sys

# OAuth client credentials — load from environment with fallback to defaults
# These are public OAuth 2.0 client credentials for a desktop application type.
# They are embedded here for convenience but can be overridden via environment
# variables for custom deployments.
# Environment variables take priority over _credentials.py
if "ANTIGRAVITY_CLIENT_ID" in os.environ:
    ANTIGRAVITY_CLIENT_ID = os.environ.get("ANTIGRAVITY_CLIENT_ID", "")
    ANTIGRAVITY_CLIENT_SECRET = os.environ.get("ANTIGRAVITY_CLIENT_SECRET", "")
else:
    try:
        from ._credentials import (  # type: ignore  # noqa: F811
            ANTIGRAVITY_CLIENT_ID,
            ANTIGRAVITY_CLIENT_SECRET,
        )
    except ImportError:
        ANTIGRAVITY_CLIENT_ID = ""
        ANTIGRAVITY_CLIENT_SECRET = ""

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

ANTIGRAVITY_VERSION_FALLBACK = "1.18.3"

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
