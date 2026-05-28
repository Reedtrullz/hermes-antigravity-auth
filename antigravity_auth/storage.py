"""Persistent account and credential storage for Hermes Antigravity plugin."""
import contextlib
import logging
import os
import json
import secrets
import time
import threading
from pathlib import Path
from typing import Any, Callable

# Try to dynamically import the lock from hermes_cli.auth, fallback to a local Thread Lock
try:
    from hermes_cli.auth import _auth_store_lock as _hermes_lock
    if hasattr(_hermes_lock, "__enter__"):
        _auth_store_lock = _hermes_lock
    else:
        _auth_store_lock = threading.Lock()
except ImportError:
    _auth_store_lock = threading.Lock()

_accounts_store_lock = threading.RLock()
_process_lock_warning_emitted = False
_process_lock_warning_guard = threading.Lock()
logger = logging.getLogger(__name__)


def _secret_file_opener(path: str, flags: int) -> int:
    """Open secret-bearing temp files with private permissions immediately."""
    return os.open(path, flags | os.O_CREAT | os.O_EXCL, 0o600)


def _lock_file_opener(path: str, flags: int) -> int:
    return os.open(path, flags | os.O_CREAT, 0o600)


def _process_lock_backend_name() -> tuple[str | None, str]:
    """Return the inter-process file locking backend available on this host."""
    try:
        import fcntl  # noqa: F401
        return "fcntl", "POSIX fcntl.flock"
    except Exception as fcntl_exc:
        try:
            import msvcrt  # noqa: F401
            return "msvcrt", "Windows msvcrt.locking"
        except Exception as msvcrt_exc:
            return None, f"fcntl unavailable ({fcntl_exc}); msvcrt unavailable ({msvcrt_exc})"


def _warn_process_lock_unavailable_once(lock_path: Path, reason: str) -> None:
    global _process_lock_warning_emitted
    with _process_lock_warning_guard:
        if _process_lock_warning_emitted:
            return
        _process_lock_warning_emitted = True
    logger.warning(
        "Inter-process file locking is unavailable for %s; "
        "account-store transactions are only thread-safe inside this process. "
        "Run one Hermes Antigravity process at a time or install on a platform "
        "with fcntl.flock/msvcrt.locking support. Reason: %s",
        lock_path,
        reason,
    )


def _acquire_process_file_lock(lock_file: Any, lock_path: Path) -> Callable[[], None]:
    """Acquire a cross-process lock and return an idempotent unlock callback.

    POSIX uses fcntl.flock. Native Windows uses msvcrt.locking over byte 0 of
    the lock file. If neither backend is importable, we keep the existing
    in-process RLock protection but emit a one-time warning instead of silently
    pretending the transaction is process-safe.
    """
    try:
        import fcntl
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        released = False

        def unlock_fcntl() -> None:
            nonlocal released
            if released:
                return
            released = True
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            except Exception:
                pass

        return unlock_fcntl
    except Exception as fcntl_exc:
        fcntl_reason = fcntl_exc

    try:
        import msvcrt
    except Exception as msvcrt_exc:
        _warn_process_lock_unavailable_once(
            lock_path,
            f"fcntl unavailable ({fcntl_reason}); msvcrt unavailable ({msvcrt_exc})",
        )
        return lambda: None

    try:
        lock_call = getattr(msvcrt, "locking")
        lock_mode = getattr(msvcrt, "LK_LOCK")
        unlock_mode = getattr(msvcrt, "LK_UNLCK")
        lock_file.seek(0, os.SEEK_END)
        if lock_file.tell() == 0:
            lock_file.write("0")
            lock_file.flush()
        lock_file.seek(0)
        lock_call(lock_file.fileno(), lock_mode, 1)
    except Exception as exc:
        raise RuntimeError(f"failed to acquire Windows process file lock for {lock_path}: {exc}") from exc

    released = False

    def unlock_msvcrt() -> None:
        nonlocal released
        if released:
            return
        released = True
        try:
            lock_file.seek(0)
            lock_call(lock_file.fileno(), unlock_mode, 1)
        except Exception:
            pass

    return unlock_msvcrt


@contextlib.contextmanager
def _process_file_lock(lock_path: Path):
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "a+", encoding="utf-8", opener=_lock_file_opener) as lock_file:
        try:
            os.chmod(lock_path, 0o600)
        except Exception:
            pass
        unlock = _acquire_process_file_lock(lock_file, lock_path)
        try:
            yield
        finally:
            unlock()


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


def is_valid_account_index(value: Any, account_count: int) -> bool:
    """Return True when value is a non-bool int within the account list bounds."""
    return type(value) is int and 0 <= value < account_count


def resolve_active_account_index(
    accounts_data: dict[str, Any],
    family: str | None = None,
    fallback: int = 0,
) -> int:
    """Resolve a safe active account index from storage.

    Family-aware callers prefer activeIndexByFamily[family], then global
    activeIndex, then a safe fallback. Family-agnostic callers preserve the
    historical activeIndex preference, but can recover from stale/invalid
    activeIndex by using the first valid family index before falling back to 0.
    """
    accounts = accounts_data.get("accounts", [])
    if not isinstance(accounts, list) or not accounts:
        return 0

    account_count = len(accounts)
    family_map = accounts_data.get("activeIndexByFamily")
    if family and isinstance(family_map, dict):
        family_idx = family_map.get(family)
        if type(family_idx) is int and 0 <= family_idx < account_count:
            return family_idx

    active_idx = accounts_data.get("activeIndex")
    if type(active_idx) is int and 0 <= active_idx < account_count:
        return active_idx

    if not family and isinstance(family_map, dict):
        for family_name in ("claude", "gemini"):
            family_idx = family_map.get(family_name)
            if type(family_idx) is int and 0 <= family_idx < account_count:
                return family_idx

    if type(fallback) is int and 0 <= fallback < account_count:
        return fallback
    return 0


def normalize_active_indices_after_explicit_switch(
    accounts_data: dict[str, Any],
    account_index: int,
) -> int:
    """Set global/family active indexes and cursor after an explicit switch."""
    accounts = accounts_data.get("accounts", [])
    if not isinstance(accounts, list) or not accounts:
        resolved_index = 0
    elif is_valid_account_index(account_index, len(accounts)):
        resolved_index = account_index
    else:
        resolved_index = resolve_active_account_index(accounts_data, fallback=0)

    accounts_data["activeIndex"] = resolved_index
    accounts_data["activeIndexByFamily"] = {
        "claude": resolved_index,
        "gemini": resolved_index,
    }
    accounts_data["cursor"] = resolved_index
    return resolved_index


def _default_accounts_storage() -> dict[str, Any]:
    return {
        "version": 4,
        "accounts": [],
        "activeIndex": 0,
        "cursor": 0,
        "activeIndexByFamily": {
            "claude": 0,
            "gemini": 0
        }
    }


def _load_accounts_unlocked(path: Path | None = None) -> dict[str, Any]:
    default_storage = _default_accounts_storage()
    path = get_accounts_json_path() if path is None else path
    if not path.exists():
        return default_storage

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
            if "cursor" not in data:
                data["cursor"] = data["activeIndex"]
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


def load_accounts() -> dict[str, Any]:
    """
    Loads the antigravity-accounts.json storage.
    Returns a dictionary conforming to the AccountStorageV4 schema structure.
    If the file is missing, malformed, or doesn't exist, it gracefully returns default structure.
    """
    path = get_accounts_json_path()
    with _accounts_store_lock:
        return _load_accounts_unlocked(path)


def _save_accounts_unlocked(path: Path, storage_dict: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    tmp_path = path.with_suffix(f".json.{os.getpid()}.{secrets.token_hex(4)}.tmp")

    try:
        with open(tmp_path, "w", encoding="utf-8", opener=_secret_file_opener) as f:
            json.dump(storage_dict, f, indent=2)
        os.replace(tmp_path, path)
        os.chmod(path, 0o600)
    except Exception as e:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except Exception:
                pass
        raise e


def save_accounts(storage_dict: dict[str, Any]) -> None:
    """
    Saves the antigravity-accounts.json storage to disk.
    Performs an atomic write using a .tmp file and rename.
    """
    path = get_accounts_json_path()
    with _process_file_lock(path.with_suffix(".lock")):
        with _accounts_store_lock:
            _save_accounts_unlocked(path, storage_dict)


def update_accounts(mutator: Callable[[dict[str, Any]], None | dict[str, Any]]) -> dict[str, Any]:
    """Transactionally update antigravity-accounts.json under one file lock.

    The mutator receives freshly loaded storage while the same inter-process
    lock used by save_accounts() is held. It may mutate the dict in place and
    return None, or return a replacement storage dict. The final data is saved
    atomically before the lock is released and returned to the caller.
    """
    path = get_accounts_json_path()
    with _process_file_lock(path.with_suffix(".lock")):
        with _accounts_store_lock:
            current = _load_accounts_unlocked(path)
            replacement = mutator(current)
            if replacement is not None:
                if not isinstance(replacement, dict):
                    raise TypeError("account storage mutator must return a dict or None")
                current = replacement
            if "version" not in current:
                current["version"] = 4
            if "accounts" not in current or not isinstance(current["accounts"], list):
                current["accounts"] = []
            if "activeIndex" not in current:
                current["activeIndex"] = 0
            if "cursor" not in current:
                current["cursor"] = current.get("activeIndex", 0)
            family_map = current.get("activeIndexByFamily")
            if not isinstance(family_map, dict):
                family_map = {"claude": 0, "gemini": 0}
            else:
                family_map.setdefault("claude", 0)
                family_map.setdefault("gemini", 0)
            current["activeIndexByFamily"] = family_map
            account_count = len(current["accounts"])
            if account_count == 0:
                current["activeIndex"] = 0
                current["cursor"] = 0
                current["activeIndexByFamily"] = {"claude": 0, "gemini": 0}
            else:
                active_idx = current.get("activeIndex", 0)
                if not isinstance(active_idx, int) or isinstance(active_idx, bool):
                    active_idx = 0
                current["activeIndex"] = max(0, min(active_idx, account_count - 1))
                cursor = current.get("cursor", current["activeIndex"])
                if not isinstance(cursor, int) or isinstance(cursor, bool):
                    cursor = current["activeIndex"]
                current["cursor"] = cursor % account_count
                normalized_family_map: dict[str, int] = {}
                for family in ("claude", "gemini"):
                    family_idx = family_map.get(family, current["activeIndex"])
                    if not isinstance(family_idx, int) or isinstance(family_idx, bool):
                        family_idx = current["activeIndex"]
                    normalized_family_map[family] = max(0, min(family_idx, account_count - 1))
                current["activeIndexByFamily"] = normalized_family_map
            _save_accounts_unlocked(path, current)
            return current


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

    with _process_file_lock(path.with_suffix(".lock")):
        with _auth_store_lock:
            current_epoch_ms = int(time.time() * 1000)
            tmp_path = path.with_suffix(f".json.{os.getpid()}.{secrets.token_hex(4)}.tmp")

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
                data["active_provider"] = "google-gemini-cli"
            elif not access_token and not refresh_token and data.get("active_provider") in ("antigravity", "google-gemini-cli"):
                data["active_provider"] = ""

            try:
                with open(tmp_path, "w", encoding="utf-8", opener=_secret_file_opener) as f:
                    json.dump(data, f, indent=2)
                os.replace(tmp_path, path)
                os.chmod(path, 0o600)
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
