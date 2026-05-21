import secrets
import hashlib
import base64
import json
import time
import sys
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
    parsed = json.loads(json_bytes.decode("utf-8"))
    if not isinstance(parsed, dict) or "verifier" not in parsed:
        raise ValueError("Missing PKCE verifier in state")
    return {
        "verifier": parsed["verifier"],
        "projectId": parsed.get("projectId") or parsed.get("project_id") or ""
    }

def _ensure_credentials():
    if not ANTIGRAVITY_CLIENT_ID or not ANTIGRAVITY_CLIENT_SECRET:
        raise RuntimeError(
            "OAuth credentials not configured. "
            "Set ANTIGRAVITY_CLIENT_ID and ANTIGRAVITY_CLIENT_SECRET environment variables, "
            "or create antigravity_auth/_credentials.py with the values."
        )

def authorize_antigravity(project_id: str = "") -> dict:
    _ensure_credentials()
    pkce = generate_pkce()
    
    params = {
        "client_id": ANTIGRAVITY_CLIENT_ID,
        "response_type": "code",
        "redirect_uri": ANTIGRAVITY_REDIRECT_URI,
        "scope": " ".join(ANTIGRAVITY_SCOPES),
        "code_challenge": pkce["challenge"],
        "code_challenge_method": "S256",
        "state": encode_state({"verifier": pkce["verifier"], "projectId": project_id or ""}),
        "access_type": "offline",
        "prompt": "consent",
    }
    
    url = f"https://accounts.google.com/o/oauth2/v2/auth?{urlencode(params)}"
    return {
        "url": url,
        "verifier": pkce["verifier"],
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
                
            resp_data = json.loads(resp_bytes.decode("utf-8"))
            cloudaicompanion_project = resp_data.get("cloudaicompanionProject")
            if not cloudaicompanion_project:
                continue
                
            if isinstance(cloudaicompanion_project, str) and cloudaicompanion_project:
                return cloudaicompanion_project
                
            if isinstance(cloudaicompanion_project, dict):
                pid = cloudaicompanion_project.get("id")
                if isinstance(pid, str) and pid:
                    return pid
        except Exception:
            pass
            
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
        verifier = state_data["verifier"]
        project_id = state_data["projectId"]
        
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
            "Accept-Encoding": "gzip, deflate, br",
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
            
        token_payload = json.loads(token_bytes.decode("utf-8"))
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
                user_info = json.loads(user_bytes.decode("utf-8"))
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
        return {
            "type": "failed",
            "error": str(error)
        }
