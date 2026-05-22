import os
import json
import time
import threading
from pathlib import Path
from typing import Any

# Try to dynamically import the lock from hermes_cli.auth, fallback to a local Thread Lock
try:
    from hermes_cli.auth import _auth_store_lock as _hermes_lock
    if hasattr(_hermes_lock, "__enter__"):
        _auth_store_lock = _hermes_lock
    else:
        _auth_store_lock = threading.Lock()
except ImportError:
    _auth_store_lock = threading.Lock()

_accounts_store_lock = threading.Lock()


def get_hermes_home() -> Path:
    """
    Returns the absolute Path to the Hermes home directory.
    Checks the HERMES_HOME env var first, falling back to ~/.hermes.
    Recursively creates the directory if it does not exist.
    """
    home_env = os.environ.get("HERMES_HOME")
    if home_env:
        path = Path(home_env).resolve()
    else:
        path = Path("~/.hermes").expanduser().resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_auth_json_path() -> Path:
    """
    Returns the Path to the auth.json file.
    """
    return get_hermes_home() / "auth.json"


def get_accounts_json_path() -> Path:
    """
    Returns the Path to the antigravity-accounts.json file.
    """
    return get_hermes_home() / "antigravity-accounts.json"


def load_accounts() -> dict[str, Any]:
    """
    Loads the antigravity-accounts.json storage.
    Returns a dictionary conforming to the AccountStorageV4 schema structure.
    If the file is missing, malformed, or doesn't exist, it gracefully returns default structure.
    """
    default_storage = {
        "version": 4,
        "accounts": [],
        "activeIndex": 0,
        "activeIndexByFamily": {
            "claude": 0,
            "gemini": 0
        }
    }
    path = get_accounts_json_path()
    if not path.exists():
        return default_storage

    with _accounts_store_lock:
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if not isinstance(data, dict):
                    return default_storage
                
                if "version" not in data:
                    data["version"] = 4
                if "accounts" not in data or not isinstance(data["accounts"], list):
                    data["accounts"] = []
                if "activeIndex" not in data:
                    data["activeIndex"] = 0
                if "activeIndexByFamily" not in data or not isinstance(data["activeIndexByFamily"], dict):
                    data["activeIndexByFamily"] = {
                        "claude": 0,
                        "gemini": 0
                    }
                else:
                    family = data["activeIndexByFamily"]
                    if "claude" not in family:
                        family["claude"] = 0
                    if "gemini" not in family:
                        family["gemini"] = 0
                
                return data
        except Exception:
            return default_storage


def save_accounts(storage_dict: dict[str, Any]) -> None:
    """
    Saves the antigravity-accounts.json storage to disk.
    Performs an atomic write using a .tmp file and rename.
    """
    path = get_accounts_json_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    
    tmp_path = path.with_suffix(f".json.{os.getpid()}.tmp")
    
    with _accounts_store_lock:
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(storage_dict, f, indent=2)
            os.replace(tmp_path, path)
        except Exception as e:
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except Exception:
                    pass
            raise e


def sync_token_to_auth_json(
    access_token: str,
    refresh_token: str,
    project_id: str = "",
    email: str | None = None,
    set_active: bool = True
) -> None:
    """
    Dual-store architecture: Hermes v0.14 routes Cloud Code requests through
    agent.google_oauth which reads auth/google_oauth.json, while the Antigravity
    CLI and plugin manage auth.json. This function writes to auth.json.
    Use sync_token_to_google_oauth() in cli.py for the google_oauth.json store.
    
    Updates or inserts the 'antigravity' key in auth.json provider list.
    Saves auth.json using process/thread-safe write lock.
    """
    path = get_auth_json_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    
    current_epoch_ms = int(time.time() * 1000)
    tmp_path = path.with_suffix(f".json.{os.getpid()}.tmp")
    
    with _auth_store_lock:
        data = {
            "providers": {},
            "active_provider": ""
        }
        
        if path.exists():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    parsed = json.load(f)
                    if isinstance(parsed, dict):
                        data = parsed
            except Exception:
                pass
                
        if "providers" not in data or not isinstance(data["providers"], dict):
            data["providers"] = {}
            
        data["providers"]["antigravity"] = {
            "tokens": {
                "access_token": access_token,
                "refresh_token": refresh_token
            },
            "project_id": project_id,
            "email": email,
            "last_refresh": current_epoch_ms
        }
        # Also register under google-gemini-cli so the /model picker detects it
        data["providers"]["google-gemini-cli"] = data["providers"]["antigravity"]
        
        if set_active:
            data["active_provider"] = "antigravity"
            
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            os.replace(tmp_path, path)
        except Exception as e:
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except Exception:
                    pass
            raise e


def get_active_token_from_auth_json() -> dict[str, str]:
    """
    Reads auth.json, extracts "tokens" from the "antigravity" provider,
    and returns a dict with "access_token", "refresh_token", and "project_id".
    If missing or invalid, returns empty string values.
    """
    default_res = {
        "access_token": "",
        "refresh_token": "",
        "project_id": ""
    }
    path = get_auth_json_path()
    if not path.exists():
        return default_res
        
    with _auth_store_lock:
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if not isinstance(data, dict):
                    return default_res
                
                providers = data.get("providers")
                if not isinstance(providers, dict):
                    return default_res
                    
                antigravity = providers.get("antigravity")
                if not isinstance(antigravity, dict):
                    return default_res
                    
                tokens = antigravity.get("tokens")
                if not isinstance(tokens, dict):
                    return default_res
                    
                return {
                    "access_token": tokens.get("access_token", ""),
                    "refresh_token": tokens.get("refresh_token", ""),
                    "project_id": antigravity.get("project_id", "")
                }
        except Exception:
            return default_res
