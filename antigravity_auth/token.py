"""Access token refresh, expiry detection, and OAuth error parsing."""
import gzip
import json
import time
import urllib.request
import urllib.error
from urllib.parse import urlencode

try:
    from .constants import ANTIGRAVITY_CLIENT_ID, ANTIGRAVITY_CLIENT_SECRET
    from .storage import (
        get_active_token_from_auth_json,
        load_accounts,
        resolve_active_account_index,
        save_accounts,
        sync_token_to_auth_json,
    )
except ImportError:
    from constants import ANTIGRAVITY_CLIENT_ID, ANTIGRAVITY_CLIENT_SECRET
    from storage import (
        get_active_token_from_auth_json,
        load_accounts,
        resolve_active_account_index,
        save_accounts,
        sync_token_to_auth_json,
    )


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


def _coerce_storage_index(value, default: int = 0) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        return default
    return value


def _clamp_storage_index(value: int, account_count: int) -> int:
    if account_count <= 0:
        return 0
    return max(0, min(value, account_count - 1))


def _adjust_storage_indexes_after_account_removal(accounts_data: dict, removed_index: int) -> None:
    accounts = accounts_data.get("accounts", [])
    if not isinstance(accounts, list):
        accounts = []
        accounts_data["accounts"] = accounts

    account_count = len(accounts)

    active_idx = _coerce_storage_index(accounts_data.get("activeIndex"), 0)
    if account_count == 0:
        active_idx = 0
    else:
        if active_idx > removed_index:
            active_idx -= 1
        elif active_idx == removed_index:
            active_idx = min(removed_index, account_count - 1)
        active_idx = _clamp_storage_index(active_idx, account_count)
    accounts_data["activeIndex"] = active_idx

    family_map = accounts_data.get("activeIndexByFamily")
    if not isinstance(family_map, dict):
        family_map = {}
    if account_count == 0:
        accounts_data["activeIndexByFamily"] = {"claude": 0, "gemini": 0}
    else:
        adjusted_family_map = {}
        for family in ("claude", "gemini"):
            family_idx = _coerce_storage_index(family_map.get(family), active_idx)
            if family_idx > removed_index:
                family_idx -= 1
            elif family_idx == removed_index:
                family_idx = active_idx
            adjusted_family_map[family] = _clamp_storage_index(family_idx, account_count)
        accounts_data["activeIndexByFamily"] = adjusted_family_map

    cursor = _coerce_storage_index(accounts_data.get("cursor"), active_idx)
    if account_count == 0:
        cursor = 0
    else:
        if cursor > removed_index:
            cursor -= 1
        cursor = cursor % account_count
    accounts_data["cursor"] = cursor


def _sync_token_to_all_auth_stores_best_effort(
    access_token: str,
    refresh_token: str,
    project_id: str = "",
    email: str | None = None,
    expires_ms: int | None = None,
    set_active: bool = True,
) -> None:
    try:
        try:
            from .auth_sync import sync_token_to_all_auth_stores
        except ImportError:
            from auth_sync import sync_token_to_all_auth_stores

        sync_result = sync_token_to_all_auth_stores(
            access_token=access_token,
            refresh_token=refresh_token,
            project_id=project_id,
            email=email,
            expires_ms=expires_ms,
            set_active=set_active,
        )
        if not getattr(sync_result, "auth_json", bool(sync_result)):
            try:
                sync_token_to_auth_json(
                    access_token=access_token,
                    refresh_token=refresh_token,
                    project_id=project_id,
                    email=email,
                    set_active=set_active,
                )
            except Exception:
                pass
    except Exception:
        try:
            sync_token_to_auth_json(
                access_token=access_token,
                refresh_token=refresh_token,
                project_id=project_id,
                email=email,
                set_active=set_active,
            )
        except Exception:
            pass


def _packed_refresh_for_account(account: dict) -> str:
    return format_refresh_parts({
        "refreshToken": account.get("refreshToken", ""),
        "projectId": account.get("projectId") or "",
        "managedProjectId": account.get("managedProjectId") or "",
    })


def _auth_json_points_at_raw_refresh_token(raw_refresh_token: str) -> bool:
    try:
        active = get_active_token_from_auth_json()
        active_parts = parse_refresh_parts(active.get("refresh_token", ""))
        return active_parts.get("refreshToken") == raw_refresh_token
    except Exception:
        return False


def _sync_runtime_auth_to_active_account_or_clear(accounts_data: dict) -> None:
    accounts = accounts_data.get("accounts", [])
    if not isinstance(accounts, list) or not accounts:
        _sync_token_to_all_auth_stores_best_effort(
            "",
            "",
            project_id="",
            email=None,
            set_active=False,
        )
        return

    active_idx = resolve_active_account_index(accounts_data)
    active_account = accounts[active_idx]
    if not isinstance(active_account, dict):
        _sync_token_to_all_auth_stores_best_effort(
            "",
            "",
            project_id="",
            email=None,
            set_active=False,
        )
        return

    packed_refresh = _packed_refresh_for_account(active_account)
    _sync_token_to_all_auth_stores_best_effort(
        access_token="",
        refresh_token=packed_refresh,
        project_id=active_account.get("projectId") or "",
        email=active_account.get("email"),
        expires_ms=None,
        set_active=True,
    )


def _remove_invalid_grant_account_and_sync_auth(raw_refresh_token: str) -> None:
    accounts_data = load_accounts()
    accounts = accounts_data.get("accounts", [])
    if not isinstance(accounts, list):
        accounts = []
        accounts_data["accounts"] = accounts

    removed_any = False
    idx = 0
    while idx < len(accounts):
        account = accounts[idx]
        if isinstance(account, dict) and account.get("refreshToken") == raw_refresh_token:
            accounts.pop(idx)
            accounts_data["accounts"] = accounts
            _adjust_storage_indexes_after_account_removal(accounts_data, idx)
            removed_any = True
            continue
        idx += 1

    if removed_any:
        save_accounts(accounts_data)
        _sync_runtime_auth_to_active_account_or_clear(accounts_data)
        return

    if _auth_json_points_at_raw_refresh_token(raw_refresh_token):
        _sync_runtime_auth_to_active_account_or_clear(accounts_data)


def _account_identity_matches_refresh_parts(account: dict, parts: dict[str, str | None]) -> bool:
    if account.get("refreshToken") != parts.get("refreshToken"):
        return False

    identity_pairs = (
        (account.get("projectId"), parts.get("projectId")),
        (account.get("managedProjectId"), parts.get("managedProjectId")),
    )
    for account_value, caller_value in identity_pairs:
        if account_value and caller_value and account_value != caller_value:
            return False
    return True


def _apply_refresh_rotation_to_account(account: dict, parts: dict[str, str | None], new_raw_refresh: str) -> None:
    account["refreshToken"] = new_raw_refresh
    if parts.get("projectId") and not account.get("projectId"):
        account["projectId"] = parts["projectId"]
    if parts.get("managedProjectId") and not account.get("managedProjectId"):
        account["managedProjectId"] = parts["managedProjectId"]


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
        
        if code == "invalid_grant" and persist:
            try:
                _remove_invalid_grant_account_and_sync_auth(parts.get("refreshToken") or "")
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
    
    new_raw_refresh = str(payload.get("refresh_token") or parts["refreshToken"])
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
            if isinstance(acc, dict) and _account_identity_matches_refresh_parts(acc, parts):
                _apply_refresh_rotation_to_account(acc, parts, new_raw_refresh)
                updated_any = True
        if updated_any:
            save_accounts(accounts_data)
    except Exception:
        pass
        
    return updated_auth
