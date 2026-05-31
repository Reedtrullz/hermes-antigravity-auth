"""CLI subcommands for OAuth login, account management, and quota checks."""
import os
import sys

if __package__ in (None, ""):
    _PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))
    _PROJECT_ROOT = os.path.dirname(_PACKAGE_DIR)
    _PACKAGE_DIR_REAL = os.path.normcase(os.path.realpath(_PACKAGE_DIR))
    _PROJECT_ROOT_REAL = os.path.normcase(os.path.realpath(_PROJECT_ROOT))

    if not any(
        os.path.normcase(os.path.realpath(path or os.getcwd())) == _PROJECT_ROOT_REAL
        for path in sys.path
    ):
        sys.path.insert(0, _PROJECT_ROOT)

    sys.path[:] = [
        path
        for path in sys.path
        if os.path.normcase(os.path.realpath(path or os.getcwd())) != _PACKAGE_DIR_REAL
    ]
    __package__ = "antigravity_auth"

import http.server
import html
import socketserver
import threading
import time
import webbrowser
from urllib.parse import parse_qs, urlparse
from typing import cast

from .auth_sync import sync_token_to_all_auth_stores, sync_token_to_google_oauth
from .oauth import authorize_antigravity, exchange_antigravity
from .storage import (
    load_accounts,
    normalize_active_indices_after_explicit_switch,
    resolve_active_account_index,
    update_accounts,
)
from .token import format_refresh_parts, parse_refresh_parts


def _auth_sync_auth_json_ok(sync_result) -> bool:
    return bool(getattr(sync_result, "auth_json", bool(sync_result)))


def _auth_sync_google_oauth_ok(sync_result) -> bool:
    return bool(getattr(sync_result, "google_oauth", bool(sync_result)))


def _print_runtime_auth_sync_warnings(sync_result, context: str) -> None:
    if not _auth_sync_auth_json_ok(sync_result):
        print(f"WARNING: Could not sync {context} to Hermes auth.json; runtime authorization may remain unchanged.")
    elif not _auth_sync_google_oauth_ok(sync_result):
        print("WARNING: Native google_oauth sync failed; auth.json credentials are active.")


def _account_matches_identity(account: dict, identity: dict) -> bool:
    if not isinstance(account, dict):
        return False
    if (account.get("email") or None) != (identity.get("email") or None):
        return False
    account_refresh = account.get("refreshToken") or None
    identity_refresh = identity.get("refreshToken") or None
    if account_refresh and identity_refresh and account_refresh == identity_refresh:
        return True
    for key in ("projectId", "managedProjectId"):
        account_value = account.get(key) or None
        identity_value = identity.get(key) or None
        if account_value is not None and identity_value is not None and account_value != identity_value:
            return False
    return True


def _find_account_by_identity(accounts: list, identity: dict, preferred_index: int | None = None) -> dict | None:
    if preferred_index is not None and 0 <= preferred_index < len(accounts):
        candidate = accounts[preferred_index]
        if isinstance(candidate, dict) and _account_matches_identity(candidate, identity):
            return candidate
    for candidate in accounts:
        if isinstance(candidate, dict) and _account_matches_identity(candidate, identity):
            return candidate
    return None


class ThreadSafeHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    expected_state: str | None = None
    callback_code: str | None = None
    callback_state: str | None = None
    callback_error: str | None = None


def _callback_html(title: str, heading: str, message: str, success: bool) -> bytes:
    badge_text = "Success" if success else "Action Required"
    heading_color = "#10b981" if success else "#dc2626"
    badge_bg = "#d1fae5" if success else "#fee2e2"
    badge_color = "#065f46" if success else "#991b1b"
    return f"""
<!DOCTYPE html>
<html>
<head>
    <title>{html.escape(title)}</title>
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            background-color: #f3f4f6;
            color: #1f2937;
            display: flex;
            justify-content: center;
            align-items: center;
            height: 100vh;
            margin: 0;
        }}
        .card {{
            background: white;
            padding: 2.5rem;
            border-radius: 12px;
            box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1), 0 2px 4px -1px rgba(0, 0, 0, 0.06);
            text-align: center;
            max-width: 400px;
            width: 90%;
        }}
        h1 {{
            color: {heading_color};
            margin-top: 0;
            font-size: 1.75rem;
        }}
        p {{
            color: #4b5563;
            line-height: 1.5;
            margin-bottom: 1.5rem;
        }}
        .badge {{
            display: inline-block;
            background-color: {badge_bg};
            color: {badge_color};
            padding: 0.25rem 0.75rem;
            border-radius: 9999px;
            font-size: 0.875rem;
            font-weight: 500;
        }}
    </style>
</head>
<body>
    <div class="card">
        <h1>{html.escape(heading)}</h1>
        <p>{html.escape(message)}</p>
        <div class="badge">{badge_text}</div>
    </div>
</body>
</html>
    """.encode("utf-8")


class OAuthCallbackHandler(http.server.BaseHTTPRequestHandler):

    def log_message(self, format, *args):
        pass

    def _write_html(self, status: int, title: str, heading: str, message: str, success: bool) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(_callback_html(title, heading, message, success))

    def _shutdown_soon(self) -> None:
        def shutdown_server():
            time.sleep(1)
            self.server.shutdown()

        threading.Thread(target=shutdown_server, daemon=True).start()

    def do_GET(self):
        parsed_url = urlparse(self.path)
        query_params = parse_qs(parsed_url.query)

        code_list = query_params.get("code")
        state_list = query_params.get("state")
        error_list = query_params.get("error")
        description_list = query_params.get("error_description")

        code = code_list[0] if code_list else None
        state = state_list[0] if state_list else None
        error_value = error_list[0] if error_list else None
        server = cast(ThreadSafeHTTPServer, self.server)
        expected_state = server.expected_state

        if expected_state and state != expected_state:
            self._write_html(
                400,
                "Hermes Authentication Failed",
                "State Mismatch",
                "The OAuth callback state did not match the login session. Return to your terminal and keep waiting for the correct callback.",
                False,
            )
            return

        if error_value:
            server.callback_error = error_value
            server.callback_state = state
            description = description_list[0] if description_list else error_value
            self._write_html(
                400,
                "Hermes Authentication Failed",
                "Authentication Failed",
                f"Google returned an OAuth error: {description}",
                False,
            )
            self._shutdown_soon()
            return

        if not code:
            self._write_html(
                400,
                "Hermes Authentication Failed",
                "Missing Authorization Code",
                "The OAuth callback did not include an authorization code.",
                False,
            )
            return

        server.callback_code = code
        server.callback_state = state

        self._write_html(
            200,
            "Hermes Authentication Success",
            "Authentication Success",
            "Google Antigravity has been successfully authorized for Hermes. You can now close this tab and return to your terminal.",
            True,
        )
        self._shutdown_soon()


def run_callback_server(
    port: int = 51121,
    timeout: int = 60,
    expected_state: str | None = None,
) -> tuple[str | None, str | None]:
    server = None
    try:
        server = ThreadSafeHTTPServer(("127.0.0.1", port), OAuthCallbackHandler)
    except Exception as e:
        print(f"Error starting callback server on port {port}: {e}", file=sys.stderr)
        return None, None

    server.callback_code = None
    server.callback_state = None
    server.callback_error = None
    server.expected_state = expected_state

    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    start_time = time.time()
    while time.time() - start_time < timeout:
        if server.callback_code is not None or server.callback_error is not None:
            break
        time.sleep(0.5)

    server.shutdown()
    server.server_close()
    server_thread.join()

    if server.callback_error:
        print(f"OAuth callback failed: {server.callback_error}", file=sys.stderr)

    return server.callback_code, server.callback_state


def run_login_flow(project_id: str = "", no_browser: bool = False) -> bool:
    auth_data = authorize_antigravity(project_id=project_id)
    auth_url = auth_data["url"]
    verifier = auth_data["verifier"]

    print("=" * 60)
    print("Initiating Google Antigravity OAuth flow...")
    print("=" * 60)

    code = None
    state = None

    if not no_browser:
        print("Opening your browser to authorize...")
        try:
            webbrowser.open(auth_url)
            print("Waiting for callback on http://localhost:51121/...")
            code, state = run_callback_server(
                port=51121,
                timeout=60,
                expected_state=auth_data.get("state", ""),
            )
        except KeyboardInterrupt:
            print("\nLogin cancelled by user.")
            return False
        except Exception as e:
            print(f"Failed to open browser or start server: {e}")

    if not code:
        print("\nPlease open the following link manually in your browser to authorize:")
        print(f"\n{auth_url}\n")
        try:
            user_input = input("Paste the redirect URL or the 'code' parameter value: ").strip()
            if not user_input:
                print("Login failed: empty input.")
                return False
            
            if "code=" in user_input:
                parsed = urlparse(user_input)
                query_params = parse_qs(parsed.query)
                code = query_params.get("code", [user_input])[0]
                state_values = query_params.get("state", [])
                state = state_values[0] if state_values else None
                expected_state = auth_data.get("state", "")
                if expected_state and state != expected_state:
                    print("Login failed: OAuth state mismatch.")
                    return False
            else:
                code = user_input
                state = auth_data.get("state", "")
        except KeyboardInterrupt:
            print("\nLogin cancelled by user.")
            return False

    print("\nExchanging code for credentials...")
    result = exchange_antigravity(code, state)

    if result.get("type") != "success":
        print(f"Authentication failed: {result.get('error') or 'Unknown error'}")
        return False

    email = result.get("email") or "unknown@google.com"
    refresh = result.get("refresh", "")
    resolved_project_id = result.get("projectId") or project_id or ""

    refresh_token = parse_refresh_parts(refresh)["refreshToken"]

    new_account_index_holder = {"index": 0}

    def upsert_account(accounts_data: dict) -> None:
        accounts = [
            acc for acc in accounts_data.get("accounts", [])
            if isinstance(acc, dict) and acc.get("email") != email
        ]
        account_record = {
            "email": email,
            "refreshToken": refresh_token,
            "projectId": resolved_project_id,
        }
        if result.get("access"):
            account_record["accessToken"] = result.get("access")
        if result.get("expires") is not None:
            account_record["accessTokenExpiresAt"] = result.get("expires")
        account_record["lastRefreshAt"] = int(time.time() * 1000)
        accounts.append(account_record)
        accounts_data["accounts"] = accounts
        new_account_index_holder["index"] = len(accounts) - 1
        normalize_active_indices_after_explicit_switch(accounts_data, new_account_index_holder["index"])

    update_accounts(upsert_account)

    sync_result = sync_token_to_all_auth_stores(
        access_token=result.get("access", ""),
        refresh_token=refresh,
        project_id=resolved_project_id,
        email=email,
        expires_ms=result.get("expires"),
        set_active=True
    )

    if not _auth_sync_auth_json_ok(sync_result):
        print("-" * 60)
        print("ERROR: Authentication was saved, but Hermes auth.json could not be updated.")
        print("Runtime authorization may not be ready; please retry login or check file permissions.")
        print("-" * 60)
        return False
    if not _auth_sync_google_oauth_ok(sync_result):
        print("WARNING: Native google_oauth sync failed; auth.json credentials are active.")

    print("-" * 60)
    print("SUCCESS: Successfully authenticated!")
    print(f"Logged in as: {email}")
    print(f"Project ID: {resolved_project_id or '<none>'}")
    print("-" * 60)
    return True


def list_accounts():
    accounts_data = load_accounts()
    accounts = accounts_data.get("accounts", [])
    active_idx = accounts_data.get("activeIndex", 0)

    if not accounts:
        print("No Google Antigravity accounts registered yet.")
        return

    print("\nGoogle Antigravity Registered Accounts:")
    print("=" * 60)
    for idx, acc in enumerate(accounts):
        is_active = "*" if idx == active_idx else " "
        email = acc.get("email", "Unknown")
        project_id = acc.get("projectId") or "<none>"
        print(f"{is_active} [{idx}] Email: {email} | Project: {project_id}")
    print("=" * 60)


def delete_account(email_or_index: str) -> bool:
    removed_holder: dict[str, object] = {"removed": None, "found": False}

    def remove_account(accounts_data: dict) -> None:
        accounts = accounts_data.get("accounts", [])
        if not isinstance(accounts, list) or not accounts:
            return

        target_idx = None
        if email_or_index.isdigit():
            idx = int(email_or_index)
            if 0 <= idx < len(accounts):
                target_idx = idx
        else:
            for idx, acc in enumerate(accounts):
                if isinstance(acc, dict) and acc.get("email") == email_or_index:
                    target_idx = idx
                    break

        if target_idx is None:
            return

        removed_holder["found"] = True
        removed = accounts.pop(target_idx)
        removed_holder["removed"] = removed
        accounts_data["accounts"] = accounts

        active_idx = accounts_data.get("activeIndex", 0)
        if not isinstance(active_idx, int) or isinstance(active_idx, bool):
            active_idx = 0
        if not accounts:
            accounts_data["activeIndex"] = 0
        else:
            if active_idx > target_idx:
                active_idx -= 1
            elif active_idx == target_idx:
                active_idx = min(target_idx, len(accounts) - 1)
            accounts_data["activeIndex"] = max(0, min(active_idx, len(accounts) - 1))

        family_map = accounts_data.get("activeIndexByFamily")
        if not isinstance(family_map, dict):
            family_map = {}
        if not accounts:
            accounts_data["activeIndexByFamily"] = {"claude": 0, "gemini": 0}
        else:
            new_active_idx = accounts_data["activeIndex"]
            adjusted_family_map = {}
            for family in ("claude", "gemini"):
                family_idx = family_map.get(family)
                if not isinstance(family_idx, int) or isinstance(family_idx, bool):
                    family_idx = new_active_idx
                elif family_idx > target_idx:
                    family_idx -= 1
                elif family_idx == target_idx:
                    family_idx = new_active_idx
                adjusted_family_map[family] = max(0, min(family_idx, len(accounts) - 1))
            accounts_data["activeIndexByFamily"] = adjusted_family_map

        cursor = accounts_data.get("cursor", accounts_data.get("activeIndex", 0))
        if not isinstance(cursor, int) or isinstance(cursor, bool):
            cursor = accounts_data.get("activeIndex", 0)
        if not accounts:
            accounts_data["cursor"] = 0
        else:
            if cursor > target_idx:
                cursor -= 1
            accounts_data["cursor"] = cursor % len(accounts)

    final_data = update_accounts(remove_account)
    accounts = final_data.get("accounts", [])

    if not removed_holder["found"]:
        if not accounts:
            print("No accounts to delete.")
        else:
            print(f"Account '{email_or_index}' not found.")
        return False

    removed = removed_holder["removed"] if isinstance(removed_holder["removed"], dict) else {}
    print(f"Removed account: {removed.get('email')}")

    if accounts:
        active_idx = final_data.get("activeIndex", 0)
        if not isinstance(active_idx, int) or isinstance(active_idx, bool):
            active_idx = 0
        active_idx = max(0, min(active_idx, len(accounts) - 1))
        active = accounts[active_idx]
        active_identity = {
            "email": active.get("email"),
            "refreshToken": active.get("refreshToken"),
            "projectId": active.get("projectId"),
            "managedProjectId": active.get("managedProjectId"),
        }
        packed_refresh = format_refresh_parts({
            "refreshToken": active.get("refreshToken", ""),
            "projectId": active.get("projectId") or "",
            "managedProjectId": active.get("managedProjectId") or "",
        })
        access_token = ""
        expires_ms = None
        sync_refresh = packed_refresh

        try:
            from .token import refresh_access_token
            refreshed = refresh_access_token({
                "refresh": packed_refresh,
                "email": active.get("email"),
            })
            access_token = refreshed.get("access") or ""
            expires_ms = refreshed.get("expires")
            sync_refresh = refreshed.get("refresh") or packed_refresh
        except Exception:
            pass

        try:
            from .token import parse_refresh_parts
            parsed = parse_refresh_parts(sync_refresh)

            def persist_active_refresh(storage: dict) -> None:
                stored_accounts = storage.get("accounts", [])
                if not isinstance(stored_accounts, list) or not stored_accounts:
                    return
                idx = max(0, min(active_idx, len(stored_accounts) - 1))
                target = _find_account_by_identity(stored_accounts, active_identity, idx)
                if not isinstance(target, dict):
                    return
                if parsed.get("refreshToken"):
                    target["refreshToken"] = parsed.get("refreshToken")
                if parsed.get("projectId"):
                    target["projectId"] = parsed.get("projectId")
                if parsed.get("managedProjectId"):
                    target["managedProjectId"] = parsed.get("managedProjectId")
                if access_token:
                    target["accessToken"] = access_token
                if expires_ms is not None:
                    target["accessTokenExpiresAt"] = expires_ms
                if access_token or expires_ms is not None:
                    target["lastRefreshAt"] = int(time.time() * 1000)

            update_accounts(persist_active_refresh)
        except Exception:
            pass

        try:
            sync_result = sync_token_to_all_auth_stores(
                access_token=access_token,
                refresh_token=sync_refresh,
                project_id=active.get("projectId") or "",
                email=active.get("email"),
                expires_ms=expires_ms,
                set_active=True,
            )
            _print_runtime_auth_sync_warnings(sync_result, "active account")
        except Exception as exc:
            print(f"WARNING: Could not sync active account to Hermes auth.json: {exc}")
    else:
        try:
            # Clear both auth.json and google_oauth.json. The google_oauth helper
            # degrades gracefully if Hermes' native store is unavailable.
            sync_result = sync_token_to_all_auth_stores("", "", project_id="", email=None, set_active=False)
            _print_runtime_auth_sync_warnings(sync_result, "cleared credentials")
        except Exception as exc:
            print(f"WARNING: Could not clear Hermes auth.json credentials: {exc}")

    return True


def _find_account_index(accounts: list, email_or_index: str) -> int | None:
    if email_or_index.isdigit():
        idx = int(email_or_index)
        if 0 <= idx < len(accounts):
            return idx
        return None
    for idx, acc in enumerate(accounts):
        if isinstance(acc, dict) and acc.get("email") == email_or_index:
            return idx
    return None


def _account_is_runtime_active(accounts_data: dict, account_index: int) -> bool:
    if resolve_active_account_index(accounts_data) == account_index:
        return True
    family_map = accounts_data.get("activeIndexByFamily")
    if isinstance(family_map, dict):
        return account_index in (
            family_map.get("claude"),
            family_map.get("gemini"),
        )
    return False


def _reload_global_account_manager() -> None:
    try:
        from .accounts.shared import get_global_manager
        manager = get_global_manager()
        if manager is not None:
            manager.reload_from_disk()
    except Exception:
        pass


def set_account_project(email_or_index: str, project_id: str) -> bool:
    project_id = (project_id or "").strip()
    if not project_id:
        print("Project ID is required.")
        return False

    state: dict[str, object] = {
        "found": False,
        "index": None,
        "account": None,
        "runtime_active": False,
    }

    def set_project(accounts_data: dict) -> None:
        accounts = accounts_data.get("accounts", [])
        if not isinstance(accounts, list) or not accounts:
            return
        idx = _find_account_index(accounts, email_or_index)
        if idx is None:
            return
        account = accounts[idx]
        if not isinstance(account, dict):
            return
        account["projectId"] = project_id
        account["managedProjectId"] = ""
        state["found"] = True
        state["index"] = idx
        state["account"] = dict(account)
        state["runtime_active"] = _account_is_runtime_active(accounts_data, idx)

    updated = update_accounts(set_project)
    if not state["found"]:
        accounts = updated.get("accounts", [])
        if not accounts:
            print("No accounts registered.")
        else:
            print(f"Account '{email_or_index}' not found.")
        return False

    _reload_global_account_manager()

    account = state["account"] if isinstance(state["account"], dict) else {}
    email = account.get("email")
    refresh_token = account.get("refreshToken") or ""
    packed_refresh = format_refresh_parts({
        "refreshToken": refresh_token,
        "projectId": project_id,
        "managedProjectId": "",
    })

    synced = False
    if bool(state["runtime_active"]):
        access_token = account.get("accessToken") or account.get("access") or ""
        expires_ms = account.get("accessTokenExpiresAt") or account.get("expiresMs") or account.get("expires")
        refresh_for_sync = packed_refresh

        try:
            from .token import refresh_access_token
            refreshed = refresh_access_token(
                {"refresh": packed_refresh, "email": email},
                persist=True,
                set_active=True,
            )
            if refreshed.get("access"):
                access_token = refreshed.get("access") or access_token
                expires_ms = refreshed.get("expires", expires_ms)
                refresh_for_sync = refreshed.get("refresh") or packed_refresh
        except Exception as exc:
            print(f"WARNING: Project saved, but active token refresh failed: {exc}")

        if access_token:
            sync_result = sync_token_to_all_auth_stores(
                access_token=access_token,
                refresh_token=refresh_for_sync,
                project_id=project_id,
                email=email if isinstance(email, str) else None,
                expires_ms=expires_ms if isinstance(expires_ms, int) else None,
                set_active=True,
            )
            _print_runtime_auth_sync_warnings(sync_result, "updated project")
            synced = _auth_sync_auth_json_ok(sync_result)
        else:
            print("WARNING: Project saved, but no active access token was available to sync Hermes runtime credentials.")

    print(f"Set project for {email or email_or_index}: {project_id}")
    if not bool(state["runtime_active"]):
        print("Note: account is not currently active; switch to it before using this project.")
    elif synced:
        print("Hermes runtime credentials updated.")
    return True


def check_quotas_and_verify():
    accounts_data = load_accounts()
    accounts = accounts_data.get("accounts", [])
    if not accounts:
        print("No accounts registered.")
        return

    print("\nVerifying Account Status & Quotas:")
    print("=" * 60)
    for idx, acc in enumerate(accounts):
        email = acc.get("email", "Unknown")
        project_id = acc.get("projectId") or ""

        refresh_token = acc.get("refreshToken", "")
        if not refresh_token:
            print(f"[{idx}] {email} (Project: {project_id or '<none>'}) -> FAILED (Missing credentials)")
            continue

        packed_refresh = format_refresh_parts({
            "refreshToken": refresh_token,
            "projectId": project_id,
            "managedProjectId": acc.get("managedProjectId") or "",
        })

        # Refresh access token
        try:
            from .token import refresh_access_token
            refreshed = refresh_access_token({"refresh": packed_refresh, "email": email})
            access_token = refreshed.get("access", "")
        except Exception:
            print(f"[{idx}] {email} (Project: {project_id or '<none>'}) -> FAILED (Token refresh error)")
            continue

        if not access_token:
            print(f"[{idx}] {email} (Project: {project_id or '<none>'}) -> FAILED (No access token)")
            continue

        # Fetch live quota from Antigravity API
        from .accounts.quota import fetch_quota_from_api
        quota = fetch_quota_from_api(access_token, project_id)

        if quota is None:
            print(f"[{idx}] {email} (Project: {project_id or '<none>'}) -> Token valid, quota fetch failed")
            continue

        print(f"[{idx}] {email} (Project: {project_id or '<none>'})")
        if isinstance(quota, list):
            for bucket in quota:
                if not isinstance(bucket, dict):
                    continue
                model_id = bucket.get("modelId", "?")
                remaining = bucket.get("remainingFraction")
                pct = f"{remaining:.0%}" if isinstance(remaining, (int, float)) else "?"
                reset = bucket.get("resetTime", "")
                line = f"    {model_id}: {pct} remaining"
                if reset:
                    line += f" (resets {reset[:10]})"
                print(line)
        else:
            print(f"    Raw response: {quota}")

        # ---- Account health probe (uses same access_token from above) ----
        try:
            from .verification import verify_account_access
            probe = verify_account_access(acc, access_token, project_id=project_id)
            if probe.status == "blocked":
                print(f"    HEALTH: BLOCKED — {probe.message}")
                if probe.verify_url:
                    print(f"    Verification URL: {probe.verify_url}")
            elif probe.status != "ok":
                print(f"    HEALTH: ERROR — {probe.message}")
        except Exception:
            pass  # health probe is informational only — never fail the check command

    print("=" * 60)


def interactive_accounts_menu():
    while True:
        try:
            print("\n--- Google Antigravity Accounts Console ---")
            print("1. List accounts")
            print("2. Add new account (Login)")
            print("3. Set active account")
            print("4. Delete account")
            print("5. Verify accounts & status")
            print("6. Exit")
            print("7. Set account project ID")
            
            choice = input("\nSelect an option [1-7]: ").strip()
            if not choice:
                continue

            if choice == "1":
                list_accounts()
            elif choice == "2":
                proj = input("Enter Google Cloud Project ID (optional): ").strip()
                run_login_flow(project_id=proj)
            elif choice == "3":
                list_accounts()
                accounts_data = load_accounts()
                accounts = accounts_data.get("accounts", [])
                if not accounts:
                    continue
                try:
                    idx_str = input(f"Enter account index [0-{len(accounts)-1}]: ").strip()
                    if idx_str.isdigit():
                        idx = int(idx_str)
                        if 0 <= idx < len(accounts):
                            switched_holder = {"account": None}

                            def switch_active(storage: dict) -> None:
                                stored_accounts = storage.get("accounts", [])
                                if not isinstance(stored_accounts, list) or not (0 <= idx < len(stored_accounts)):
                                    return
                                normalize_active_indices_after_explicit_switch(storage, idx)
                                switched_holder["account"] = stored_accounts[idx]

                            updated_accounts_data = update_accounts(switch_active)
                            accounts = updated_accounts_data.get("accounts", accounts)
                            acc = switched_holder["account"] if isinstance(switched_holder["account"], dict) else accounts[idx]
                            switch_identity = {
                                "email": acc.get("email"),
                                "refreshToken": acc.get("refreshToken"),
                                "projectId": acc.get("projectId"),
                                "managedProjectId": acc.get("managedProjectId"),
                            }
                            packed_refresh = format_refresh_parts({
                                "refreshToken": acc.get("refreshToken", ""),
                                "projectId": acc.get("projectId") or "",
                                "managedProjectId": acc.get("managedProjectId") or "",
                            })
                            # Get a fresh access token for the auth.json
                            expires_ms = None
                            try:
                                from .token import refresh_access_token
                                refreshed = refresh_access_token({"refresh": packed_refresh, "email": acc.get("email")})
                                access_token = refreshed.get("access", "")
                                packed_refresh = refreshed.get("refresh") or packed_refresh
                                expires_ms = refreshed.get("expires")
                                try:
                                    parsed = parse_refresh_parts(packed_refresh)

                                    def persist_switched_token(storage: dict) -> None:
                                        stored_accounts = storage.get("accounts", [])
                                        if not isinstance(stored_accounts, list) or not stored_accounts:
                                            return
                                        target = _find_account_by_identity(stored_accounts, switch_identity, idx)
                                        if not isinstance(target, dict):
                                            return
                                        if parsed.get("refreshToken"):
                                            target["refreshToken"] = parsed.get("refreshToken")
                                        if parsed.get("projectId"):
                                            target["projectId"] = parsed.get("projectId")
                                        if parsed.get("managedProjectId"):
                                            target["managedProjectId"] = parsed.get("managedProjectId")
                                        if access_token:
                                            target["accessToken"] = access_token
                                        if expires_ms is not None:
                                            target["accessTokenExpiresAt"] = expires_ms
                                        target["lastRefreshAt"] = int(time.time() * 1000)

                                    update_accounts(persist_switched_token)
                                except Exception:
                                    pass
                            except Exception:
                                access_token = ""  # fallback to empty if refresh fails
                            sync_result = sync_token_to_all_auth_stores(
                                access_token=access_token,
                                refresh_token=packed_refresh,
                                project_id=acc.get("projectId") or "",
                                email=acc.get("email"),
                                expires_ms=expires_ms,
                                set_active=True
                            )
                            _print_runtime_auth_sync_warnings(sync_result, "selected account")
                            print(f"Set active account to: {acc.get('email')}")
                        else:
                            print("Invalid index.")
                    else:
                        print("Invalid input.")
                except ValueError:
                    print("Invalid input.")
            elif choice == "4":
                list_accounts()
                target = input("Enter email or index to delete: ").strip()
                if target:
                    delete_account(target)
            elif choice == "5":
                check_quotas_and_verify()
            elif choice == "6":
                print("Exiting console.")
                break
            elif choice == "7":
                list_accounts()
                target = input("Enter email or index to update: ").strip()
                project = input("Enter Google Cloud Project ID: ").strip()
                if target and project:
                    set_account_project(target, project)
            else:
                print("Invalid option. Please try again.")
        except KeyboardInterrupt:
            print("\nExiting console.")
            break


def print_interceptor_status():
    """Print the Antigravity interceptor installation status and model availability."""
    print("Antigravity Interceptor Status")
    print("==============================")
    print()

    # Check interceptor installation
    print("--- HTTP Interceptor ---")
    try:
        from . import interceptor
        installed = interceptor.is_installed()
        if installed:
            print("  Status:    INSTALLED (headers + auth + request transformation active)")
        else:
            print("  Status:    NOT INSTALLED")
            print("  Impact:    Claude models will NOT work through Antigravity")
            print("             (requests go through Code Assist which checks eligibility)")
    except Exception as exc:
        print(f"  Status:    ERROR importing interceptor: {exc}")
        installed = False

    # Check if Hermes adapter symbols are importable
    print()
    print("--- Hermes Integration ---")
    try:
        from agent.gemini_cloudcode_adapter import (
            GeminiCloudCodeClient,
            wrap_code_assist_request,
            CODE_ASSIST_ENDPOINT,
        )
        print(f"  Adapter:   GeminiCloudCodeClient available")
        print(f"  Endpoint:  {CODE_ASSIST_ENDPOINT}")
        adapter_ok = True
    except ImportError as exc:
        print(f"  Adapter:   NOT importable ({exc})")
        print("  Impact:    Interceptor cannot install without the Hermes adapter")
        adapter_ok = False
    except Exception as exc:
        print(f"  Adapter:   Error: {exc}")
        adapter_ok = False

    if adapter_ok and not installed:
        print()
        print("  The interceptor module is loadable but not installed in this process.")
        print("  In a live Hermes session, the plugin installs the interceptor at startup.")
        print("  Run this command from within Hermes to check the live state:")
        print()
        print("    /terminal hermes antigravity status")
        print()
        print("  Or check Hermes logs for interceptor messages:")
        print("    grep -i interceptor ~/.hermes/logs/gateway.log")

    # Check accounts
    print()
    print("--- Accounts ---")
    try:
        from .storage import load_accounts
        data = load_accounts()
        accounts = data.get("accounts", [])
        active_idx = data.get("activeIndex", 0)
        print(f"  Accounts:  {len(accounts)} configured")
        for i, a in enumerate(accounts):
            marker = " ← ACTIVE" if i == active_idx else ""
            email = a.get("email", "unknown")
            has_refresh = bool(a.get("refreshToken"))
            has_access = bool(a.get("accessToken"))
            print(f"    [{i}] {email}{marker}  refresh={'✓' if has_refresh else '✗'} access={'✓' if has_access else '✗'}")
    except Exception as exc:
        print(f"  Accounts:  Error loading: {exc}")

    # Model availability
    print()
    print("--- Models ---")
    try:
        from .hermes_provider_plugin import ANTIGRAVITY_MODELS
        claude_models = [m for m in ANTIGRAVITY_MODELS if "claude" in m.lower()]
        gemini_models = [m for m in ANTIGRAVITY_MODELS if "gemini" in m.lower()]
        print(f"  Claude:    {', '.join(claude_models)}")
        print(f"  Gemini:    {', '.join(gemini_models)}")
        if not installed and claude_models:
            print()
            print("  ⚠️  Claude models require the HTTP interceptor to be installed.")
            print("     Without it, requests go through Code Assist which may return 403.")
            print("     Run 'hermes antigravity doctor' for full diagnostics.")
    except Exception as exc:
        print(f"  Models:    Error: {exc}")

    print()

def setup_cli(parser):
    subparsers = parser.add_subparsers(dest="action", help="Antigravity actions")
    
    login_parser = subparsers.add_parser("login", help="Log in with Google Antigravity OAuth")
    login_parser.add_argument("--project-id", default="", help="Google Cloud project ID")
    login_parser.add_argument("--no-browser", action="store_true", help="Disable automatic browser opening")
    
    subparsers.add_parser("accounts", help="Manage multi-account rotation console")
    
    subparsers.add_parser("list", help="List configured accounts")

    subparsers.add_parser("quota", help="Verify accounts and show quota status")
    subparsers.add_parser("check", help="Verify accounts and show quota status")
    subparsers.add_parser("doctor", help="Run Antigravity installation and auth diagnostics")
    subparsers.add_parser("status", help="Show interceptor status and model availability")

    delete_parser = subparsers.add_parser("delete", help="Delete a saved account")
    delete_parser.add_argument("email_or_index", help="Email address or account index to remove")

    project_parser = subparsers.add_parser("set-project", help="Set GCP project ID for a saved account")
    project_parser.add_argument("email_or_index", help="Email address or account index to update")
    project_parser.add_argument("project_id", help="Google Cloud project ID to use for Antigravity standard tier")


def handle_cli(args):
    try:
        if args.action == "login":
            run_login_flow(project_id=args.project_id, no_browser=args.no_browser)
        elif args.action == "accounts":
            interactive_accounts_menu()
        elif args.action == "list":
            list_accounts()
        elif args.action == "delete":
            delete_account(args.email_or_index)
        elif args.action == "set-project":
            set_account_project(args.email_or_index, args.project_id)
        elif args.action in ("quota", "check"):
            check_quotas_and_verify()
        elif args.action == "doctor":
            from .doctor import print_doctor
            print_doctor()
        elif args.action == "status":
            print_interceptor_status()
        else:
            interactive_accounts_menu()
    except KeyboardInterrupt:
        print("\nOperation cancelled by user.")
        sys.exit(0)
