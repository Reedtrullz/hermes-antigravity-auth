"""PKCE OAuth 2.0 authorization and token exchange for Google Antigravity."""
import secrets
import hashlib
import base64
import json
import time
import sys
import traceback
import gzip
import urllib.request
import urllib.error
from urllib.parse import urlencode

try:
    from .constants import (
        ANTIGRAVITY_CLIENT_ID,
        ANTIGRAVITY_CLIENT_SECRET,
        ANTIGRAVITY_REDIRECT_URI,
        ANTIGRAVITY_SCOPES,
        ANTIGRAVITY_LOAD_ENDPOINTS,
        ANTIGRAVITY_ENDPOINT_FALLBACKS,
        GEMINI_CLI_HEADERS,
        get_antigravity_headers,
    )
    from .debug import createLogger, format_error_for_log
except ImportError:
    from constants import (
        ANTIGRAVITY_CLIENT_ID,
        ANTIGRAVITY_CLIENT_SECRET,
        ANTIGRAVITY_REDIRECT_URI,
        ANTIGRAVITY_SCOPES,
        ANTIGRAVITY_LOAD_ENDPOINTS,
        ANTIGRAVITY_ENDPOINT_FALLBACKS,
        GEMINI_CLI_HEADERS,
        get_antigravity_headers,
    )
    from debug import createLogger, format_error_for_log

_log = createLogger(__name__)

# In-memory PKCE verifier store:
# state_id -> {"verifier": str, "projectId": str, "createdAt": str}
# Keys are random, never exposed to browser. Entries consumed on exchange.
_pkce_verifier_store: dict[str, dict[str, str]] = {}
_PKCE_VERIFIER_TTL_SECONDS = 600

def generate_pkce() -> dict:
    verifier = secrets.token_urlsafe(64)
    sha256 = hashlib.sha256(verifier.encode("utf-8")).digest()
    challenge = base64.urlsafe_b64encode(sha256).decode("utf-8").rstrip("=")
    return {
        "challenge": challenge,
        "verifier": verifier
    }

def encode_state(payload: dict) -> str:
    json_bytes = json.dumps(payload, separators=(',', ':')).encode("utf-8")
    return base64.urlsafe_b64encode(json_bytes).decode("utf-8").rstrip("=")

def decode_state(state: str) -> dict:
    normalized = state.replace("-", "+").replace("_", "/")
    padded = normalized + "=" * ((4 - len(normalized) % 4) % 4)
    json_bytes = base64.b64decode(padded)
    parsed = json.loads(json_bytes.decode("utf-8", errors="ignore"))
    if not isinstance(parsed, dict):
        raise ValueError("Invalid state format")
    return parsed

def _pkce_entry_expired(entry: dict[str, str], now: float) -> bool:
    try:
        created_at = float(entry.get("createdAt", ""))
    except (TypeError, ValueError):
        return False
    return now - created_at >= _PKCE_VERIFIER_TTL_SECONDS

def _cleanup_expired_pkce_verifiers(now: float | None = None) -> None:
    """Remove abandoned PKCE verifier entries without failing login flow."""
    try:
        current_time = time.time() if now is None else now
        expired_state_ids = [
            state_id
            for state_id, entry in list(_pkce_verifier_store.items())
            if isinstance(entry, dict) and _pkce_entry_expired(entry, current_time)
        ]
        for state_id in expired_state_ids:
            _pkce_verifier_store.pop(state_id, None)
    except Exception:
        pass

def get_pkce_verifier(state_id: str) -> dict[str, str] | None:
    """Retrieve and consume the PKCE verifier for a state ID."""
    _cleanup_expired_pkce_verifiers()
    return _pkce_verifier_store.pop(state_id, None)

def _ensure_credentials():
    if not ANTIGRAVITY_CLIENT_ID or not ANTIGRAVITY_CLIENT_SECRET:
        raise RuntimeError(
            "OAuth credentials not configured. "
            "Set ANTIGRAVITY_CLIENT_ID and ANTIGRAVITY_CLIENT_SECRET environment variables, "
            "or create antigravity_auth/_credentials.py with the values."
        )

def authorize_antigravity(project_id: str = "") -> dict:
    now = time.time()
    _cleanup_expired_pkce_verifiers(now)
    from .constants import require_credentials
    cid, csec = require_credentials()
    pkce = generate_pkce()
    
    state_id = secrets.token_urlsafe(32)
    encoded_state = encode_state({"id": state_id})
    _pkce_verifier_store[state_id] = {
        "verifier": pkce["verifier"],
        "projectId": project_id or "",
        "createdAt": str(now),
    }
    
    params = {
        "client_id": cid,
        "response_type": "code",
        "redirect_uri": ANTIGRAVITY_REDIRECT_URI,
        "scope": " ".join(ANTIGRAVITY_SCOPES),
        "code_challenge": pkce["challenge"],
        "code_challenge_method": "S256",
        "state": encoded_state,
        "access_type": "offline",
        "prompt": "consent",
    }
    
    url = f"https://accounts.google.com/o/oauth2/v2/auth?{urlencode(params)}"
    return {
        "url": url,
        "verifier": pkce["verifier"],
        "state": encoded_state,
        "projectId": project_id or "",
        "project_id": project_id or "",
    }

def _decompress(body: bytes, response) -> bytes:
    encoding = response.headers.get("Content-Encoding", "")
    if "gzip" in encoding:
        return gzip.decompress(body)
    return body


def make_post_request(url: str, headers: dict, data: bytes, timeout: int = 10) -> tuple[int, bytes]:
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return response.status, _decompress(response.read(), response)
    except urllib.error.HTTPError as e:
        return e.code, _decompress(e.read(), e)
    except Exception as e:
        return 500, str(e).encode("utf-8")

def make_get_request(url: str, headers: dict, timeout: int = 10) -> tuple[int, bytes]:
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return response.status, _decompress(response.read(), response)
    except urllib.error.HTTPError as e:
        return e.code, _decompress(e.read(), e)
    except Exception as e:
        return 500, str(e).encode("utf-8")

def fetch_project_id(access_token: str) -> str:
    antigravity_headers = get_antigravity_headers()
    load_headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "User-Agent": GEMINI_CLI_HEADERS["User-Agent"],
        "Client-Metadata": antigravity_headers["Client-Metadata"],
    }
    
    seen = set()
    load_endpoints = []
    for ep in ANTIGRAVITY_LOAD_ENDPOINTS + ANTIGRAVITY_ENDPOINT_FALLBACKS:
        if ep not in seen:
            seen.add(ep)
            load_endpoints.append(ep)
            
    for base_endpoint in load_endpoints:
        try:
            url = f"{base_endpoint}/v1internal:loadCodeAssist"
            payload = {
                "metadata": {
                    "ideType": "ANTIGRAVITY",
                    "platform": "WINDOWS" if sys.platform == "win32" else "MACOS",
                    "pluginType": "GEMINI",
                }
            }
            data = json.dumps(payload).encode("utf-8")
            status, resp_bytes = make_post_request(url, load_headers, data, timeout=10)
            
            if status != 200:
                continue
                
            resp_data = json.loads(resp_bytes.decode("utf-8", errors="ignore"))
            cloudaicompanion_project = resp_data.get("cloudaicompanionProject")
            if not cloudaicompanion_project:
                continue
                
            if isinstance(cloudaicompanion_project, str) and cloudaicompanion_project:
                return cloudaicompanion_project
                
            if isinstance(cloudaicompanion_project, dict):
                pid = cloudaicompanion_project.get("id")
                if isinstance(pid, str) and pid:
                    return pid
        except Exception as e:
            _log.debug(f"fetch_project_id failed for endpoint {base_endpoint}: {format_error_for_log(e)}")
            continue
            
    return ""

def calculate_token_expiry(request_time_ms: int, expires_in_seconds) -> int:
    try:
        seconds = int(expires_in_seconds)
    except (ValueError, TypeError):
        seconds = 3600
        
    if seconds <= 0:
        return request_time_ms
        
    return request_time_ms + seconds * 1000

def exchange_antigravity(code: str, state: str) -> dict:
    try:
        state_data = decode_state(state)
        state_id = state_data.get("id", "")
        pkce_data = get_pkce_verifier(state_id) if state_id else None
        verifier = pkce_data.get("verifier", "") if pkce_data else ""
        project_id = pkce_data.get("projectId", "") if pkce_data else ""
        if not verifier:
            return {
                "type": "failed",
                "error": "Missing or expired PKCE verifier for OAuth state"
            }
        
        start_time = int(time.time() * 1000)
        
        token_params = {
            "client_id": ANTIGRAVITY_CLIENT_ID,
            "client_secret": ANTIGRAVITY_CLIENT_SECRET,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": ANTIGRAVITY_REDIRECT_URI,
            "code_verifier": verifier,
        }
        
        token_headers = {
            "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
            "Accept": "*/*",
            "Accept-Encoding": "gzip, deflate",
            "User-Agent": GEMINI_CLI_HEADERS["User-Agent"],
        }
        
        token_data = urlencode(token_params).encode("utf-8")
        
        token_status, token_bytes = make_post_request(
            "https://oauth2.googleapis.com/token",
            token_headers,
            token_data,
            timeout=10
        )
        
        if token_status != 200:
            error_text = token_bytes.decode("utf-8", errors="ignore")
            return {"type": "failed", "error": error_text}
            
        token_payload = json.loads(token_bytes.decode("utf-8", errors="ignore"))
        access_token = token_payload.get("access_token")
        refresh_token = token_payload.get("refresh_token")
        expires_in = token_payload.get("expires_in")
        
        if not access_token:
            return {"type": "failed", "error": "Missing access token in response"}
            
        if not refresh_token:
            return {"type": "failed", "error": "Missing refresh token in response"}
            
        user_info_headers = {
            "Authorization": f"Bearer {access_token}",
            "User-Agent": GEMINI_CLI_HEADERS["User-Agent"],
        }
        
        user_status, user_bytes = make_get_request(
            "https://www.googleapis.com/oauth2/v1/userinfo?alt=json",
            user_info_headers,
            timeout=10
        )
        
        email = None
        if user_status == 200:
            try:
                user_info = json.loads(user_bytes.decode("utf-8", errors="ignore"))
                email = user_info.get("email")
            except Exception:
                pass
                
        effective_project_id = project_id
        if not effective_project_id:
            effective_project_id = fetch_project_id(access_token)
            
        stored_refresh = f"{refresh_token}|{effective_project_id or ''}"
        
        return {
            "type": "success",
            "refresh": stored_refresh,
            "access": access_token,
            "expires": calculate_token_expiry(start_time, expires_in),
            "email": email,
            "projectId": effective_project_id or "",
            "project_id": effective_project_id or "",
        }
    except Exception as error:
        _log.error(f"exchange_antigravity failed: {format_error_for_log(error)}")
        _log.debug("traceback", extra={"traceback": traceback.format_exc()})
        return {
            "type": "failed",
            "error": str(error)
        }
