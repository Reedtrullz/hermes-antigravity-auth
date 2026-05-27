"""Access token refresh, expiry detection, and OAuth error parsing."""
import gzip
import json
import time
import urllib.request
import urllib.error
from urllib.parse import urlencode

try:
    from .constants import ANTIGRAVITY_CLIENT_ID, ANTIGRAVITY_CLIENT_SECRET
    from .storage import load_accounts, save_accounts, sync_token_to_auth_json
except ImportError:
    from constants import ANTIGRAVITY_CLIENT_ID, ANTIGRAVITY_CLIENT_SECRET
    from storage import load_accounts, save_accounts, sync_token_to_auth_json


def _decompress(body: bytes, response) -> bytes:
    encoding = response.headers.get("Content-Encoding", "")
    if "gzip" in encoding:
        return gzip.decompress(body)
    return body


class AntigravityTokenRefreshError(Exception):
    def __init__(
        self,
        message: str,
        code: str | None = None,
        description: str | None = None,
        status: int = 500,
        status_text: str = "",
    ):
        super().__init__(message)
        self.code = code
        self.description = description
        self.status = status
        self.status_text = status_text


def parse_refresh_parts(refresh: str) -> dict[str, str | None]:
    if not refresh:
        return {"refreshToken": "", "projectId": None, "managedProjectId": None}
    parts = refresh.split("|")
    refresh_token = parts[0] if len(parts) > 0 else ""
    project_id = parts[1] if len(parts) > 1 and parts[1] else None
    managed_project_id = parts[2] if len(parts) > 2 and parts[2] else None
    return {
        "refreshToken": refresh_token,
        "projectId": project_id,
        "managedProjectId": managed_project_id,
    }


def format_refresh_parts(parts: dict) -> str:
    refresh_token = parts.get("refreshToken") or ""
    project_id = parts.get("projectId") or ""
    managed_project_id = parts.get("managedProjectId") or ""
    base = f"{refresh_token}|{project_id}"
    if managed_project_id:
        return f"{base}|{managed_project_id}"
    return base


def is_access_token_expired(auth: dict) -> bool:
    if not auth or "access" not in auth or not auth.get("access"):
        return True
    expires = auth.get("expires")
    if not isinstance(expires, (int, float)):
        return True
    current_ms = int(time.time() * 1000)
    return expires <= current_ms + 60000


def parse_oauth_error_payload(text: str | None) -> dict[str, str | None]:
    if not text:
        return {}
    try:
        payload = json.loads(text)
        if not isinstance(payload, dict):
            return {"description": text}
        
        code = None
        error_val = payload.get("error")
        if isinstance(error_val, str):
            code = error_val
        elif isinstance(error_val, dict):
            code = error_val.get("status") or error_val.get("code")
            if not payload.get("error_description") and error_val.get("message"):
                return {"code": str(code) if code else None, "description": error_val.get("message")}
                
        description = payload.get("error_description")
        if description:
            return {"code": str(code) if code else None, "description": description}
            
        if isinstance(error_val, dict) and error_val.get("message"):
            return {"code": str(code) if code else None, "description": error_val.get("message")}
            
        return {"code": str(code) if code else None}
    except Exception:
        return {"description": text}


def refresh_access_token(auth: dict, *, persist: bool = False, set_active: bool = False) -> dict:
    old_refresh = auth.get("refresh", "")
    parts = parse_refresh_parts(old_refresh)
    if not parts.get("refreshToken"):
        raise AntigravityTokenRefreshError("Missing refresh token", status=400)

    start_time_ms = int(time.time() * 1000)
    
    token_params = {
        "grant_type": "refresh_token",
        "refresh_token": parts["refreshToken"],
        "client_id": ANTIGRAVITY_CLIENT_ID,
        "client_secret": ANTIGRAVITY_CLIENT_SECRET,
    }
    
    token_headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "*/*",
        "User-Agent": "google-api-nodejs-client/9.15.1",
    }
    
    token_data = urlencode(token_params).encode("utf-8")
    req = urllib.request.Request(
        "https://oauth2.googleapis.com/token",
        data=token_data,
        headers=token_headers,
        method="POST"
    )
    
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            status = response.status
            status_text = response.reason if hasattr(response, "reason") else "OK"
            resp_bytes = _decompress(response.read(), response)
    except urllib.error.HTTPError as e:
        status = e.code
        status_text = e.reason if hasattr(e, "reason") else "HTTP Error"
        try:
            resp_bytes = _decompress(e.read(), e)
        except Exception:
            resp_bytes = b""
    except Exception as e:
        status = 500
        status_text = "Internal Server Error"
        resp_bytes = str(e).encode("utf-8", errors="ignore")

    if status != 200:
        error_text = resp_bytes.decode("utf-8", errors="ignore")
        error_info = parse_oauth_error_payload(error_text)
        code = error_info.get("code")
        description = error_info.get("description") or error_text
        
        details = [x for x in [code, description] if x]
        details_str = ": ".join(details)
        base_message = f"Antigravity token refresh failed ({status} {status_text})"
        message = f"{base_message} - {details_str}" if details_str else base_message
        
        if code == "invalid_grant":
            if persist:
                try:
                    sync_token_to_auth_json("", "", project_id="", set_active=False)
                except Exception:
                    pass

            try:
                accounts_data = load_accounts()
                original_len = len(accounts_data.get("accounts", []))
                accounts_data["accounts"] = [
                    acc for acc in accounts_data.get("accounts", [])
                    if acc.get("refreshToken") != parts["refreshToken"]
                ]
                if len(accounts_data["accounts"]) != original_len:
                    save_accounts(accounts_data)
            except Exception:
                pass
                
        raise AntigravityTokenRefreshError(
            message=message,
            code=code,
            description=description,
            status=status,
            status_text=status_text
        )

    try:
        payload = json.loads(resp_bytes.decode("utf-8", errors="ignore"))
    except Exception as e:
        raise AntigravityTokenRefreshError(
            f"Failed to parse token response JSON: {e}",
            status=500,
            status_text="JSON Parse Error"
        )
        
    access_token = payload.get("access_token")
    if not access_token:
        raise AntigravityTokenRefreshError(
            "Missing access token in token response",
            status=500,
            status_text="Invalid Response"
        )
        
    expires_in = payload.get("expires_in") or 3600
    expires_ms = start_time_ms + int(expires_in) * 1000
    
    new_raw_refresh = payload.get("refresh_token") or parts["refreshToken"]
    refreshed_parts = {
        "refreshToken": new_raw_refresh,
        "projectId": parts.get("projectId"),
        "managedProjectId": parts.get("managedProjectId"),
    }
    
    new_refresh_packed = format_refresh_parts(refreshed_parts)
    
    updated_auth = dict(auth)
    updated_auth["access"] = access_token
    updated_auth["expires"] = expires_ms
    updated_auth["refresh"] = new_refresh_packed
    
    project_id = parts.get("projectId") or ""
    email = auth.get("email")
    
    if persist:
        try:
            sync_token_to_auth_json(
                access_token=access_token,
                refresh_token=new_refresh_packed,
                project_id=project_id,
                email=email,
                set_active=set_active,
            )
        except Exception:
            pass
        
    try:
        accounts_data = load_accounts()
        updated_any = False
        for acc in accounts_data.get("accounts", []):
            if acc.get("refreshToken") == parts["refreshToken"]:
                acc["refreshToken"] = new_raw_refresh
                if parts.get("projectId"):
                    acc["projectId"] = parts["projectId"]
                if parts.get("managedProjectId"):
                    acc["managedProjectId"] = parts["managedProjectId"]
                updated_any = True
        if updated_any:
            save_accounts(accounts_data)
    except Exception:
        pass
        
    return updated_auth
