"""HTTP interceptor — injects Antigravity headers via httpx event hooks."""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import httpx

from .config import get_config
from .fingerprint import (
    build_fingerprint_headers,
    generate_fingerprint,
    update_fingerprint_version,
)
from .transform.envelope import (
    HeaderStyle,
    build_antigravity_headers,
    resolve_model_for_header_style,
)

logger = logging.getLogger(__name__)

_PATCHED = False
_ORIGINAL_INIT = None
_ORIGINAL_WRAP_CODE_ASSIST = None
_ORIGINAL_ENSURE_PROJECT_CONTEXT = None

_TRACE_DIR = None


def _trace(event: str, **kwargs: Any) -> None:
    """Write a trace marker to the interceptor debug log.

    Creates one file per Hermes process (keyed by PID) under
    ``~/.hermes/antigravity-traces/`` so you can verify whether the
    httpx event hooks fire and what decisions the hook makes.

    Trace files are created with private permissions (0o600) in a
    private directory (0o700), matching the debug.py security discipline.
    """
    global _TRACE_DIR
    import time as _time

    if _TRACE_DIR is None:
        try:
            from .storage import get_hermes_home
            _TRACE_DIR = get_hermes_home() / "antigravity-traces"
            _TRACE_DIR.mkdir(parents=True, exist_ok=True)
            try:
                os.chmod(_TRACE_DIR, 0o700)
            except Exception:
                pass
            _cleanup_old_traces(_TRACE_DIR, max_files=50)
            # Repair permissions on existing trace files created before
            # the 0o600 opener was added.
            try:
                for existing in _TRACE_DIR.iterdir():
                    if existing.is_file() and existing.name.startswith("trace-"):
                        try:
                            os.chmod(existing, 0o600)
                        except Exception:
                            pass
            except Exception:
                pass
        except Exception:
            return

    try:
        ts = _time.time()
        pid = os.getpid()
        trace_file = _TRACE_DIR / f"trace-{pid}.log"
        extra = " ".join(f"{k}={v}" for k, v in kwargs.items()) if kwargs else ""
        line = f"{ts:.3f} {event} {extra}\n"
        with open(trace_file, "a", opener=_private_trace_opener) as f:
            f.write(line)
    except Exception:
        pass


def _private_trace_opener(path: str, flags: int) -> int:
    """Open trace files with private permissions (0o600)."""
    return os.open(path, flags | os.O_CREAT, 0o600)


def _cleanup_old_traces(traces_dir: Any, max_files: int = 50) -> None:
    """Remove oldest trace files when count exceeds max_files."""
    try:
        from pathlib import Path
        traces_path = Path(traces_dir)
        if not traces_path.is_dir():
            return
        files = [
            f for f in traces_path.iterdir()
            if f.is_file() and f.name.startswith("trace-") and f.name.endswith(".log")
        ]
        if len(files) <= max_files:
            return
        sorted_files = sorted(files, key=lambda f: f.stat().st_mtime, reverse=True)
        for f in sorted_files[max_files:]:
            try:
                f.unlink()
            except Exception:
                pass
    except Exception:
        pass


def _model_family_for_model(model: str) -> str:
  lower = (model or "").lower()
  if "claude" in lower:
    return "claude"
  return "gemini"


def _select_header_style_for_model(model: str, cli_first: bool) -> HeaderStyle:
  lower = (model or "").lower()
  if cli_first and "gemini" in lower and "claude" not in lower:
    return "gemini-cli"
  return "antigravity"


def _request_model_from_response(response: httpx.Response) -> str:
  try:
    body = json.loads(response.request.content)
    if isinstance(body, dict):
      return str(body.get("model") or "")
  except Exception:
    return ""
  return ""


def _account_identity_for_managed_account(account: Any) -> dict[str, str | None]:
  parts = getattr(account, "refresh_parts", None)
  return {
    "email": getattr(account, "email", None),
    "refresh_token": getattr(parts, "refresh_token", None),
    "project_id": getattr(parts, "project_id", None),
    "managed_project_id": getattr(parts, "managed_project_id", None),
  }


def _account_identity_for_account_dict(account: dict[str, Any]) -> dict[str, str | None]:
  return {
    "email": account.get("email"),
    "refresh_token": account.get("refreshToken"),
    "project_id": account.get("projectId"),
    "managed_project_id": account.get("managedProjectId"),
  }


def _account_identity_matches(actual: dict[str, Any], expected: Any) -> bool:
  if not isinstance(expected, dict):
    return True
  for key in ("email", "refresh_token", "project_id", "managed_project_id"):
    if (actual.get(key) or None) != (expected.get(key) or None):
      return False
  return True


def _response_account_for_request(mgr: Any, request_extensions: dict, family: str) -> Any:
  selected_idx = request_extensions.get("antigravity_selected_account_index")
  selected_identity = request_extensions.get("antigravity_selected_account_identity")
  if isinstance(selected_idx, int) and not isinstance(selected_idx, bool):
    try:
      selected = mgr.get_account_by_index(selected_idx)
    except AttributeError:
      selected = None
    if selected is not None and _account_identity_matches(
      _account_identity_for_managed_account(selected),
      selected_identity,
    ):
      return selected
    return None
  return mgr.get_current_account_for_family(family)


def _packed_refresh_for_account(account: Any) -> str:
  from .token import format_refresh_parts
  parts = account.refresh_parts
  return format_refresh_parts({
    "refreshToken": parts.refresh_token,
    "projectId": parts.project_id or "",
    "managedProjectId": parts.managed_project_id or "",
  })


def _sync_refreshed_token_to_all_auth_stores(
    *,
    refreshed: dict[str, Any],
    packed_refresh: str,
    project_id: str = "",
    email: str | None = None,
) -> dict[str, str | None] | None:
  from .auth_sync import sync_token_to_all_auth_stores
  from .token import parse_refresh_parts

  rotated_refresh = refreshed.get("refresh")
  sync_refresh = rotated_refresh or packed_refresh
  parsed_refresh = parse_refresh_parts(rotated_refresh) if rotated_refresh else None
  sync_project_id = (
    (parsed_refresh.get("projectId") if parsed_refresh else None)
    or project_id
    or ""
  )
  sync_result = sync_token_to_all_auth_stores(
    access_token=refreshed["access"],
    refresh_token=sync_refresh,
    project_id=sync_project_id,
    email=email,
    expires_ms=refreshed.get("expires"),
    set_active=True,
  )
  if not getattr(sync_result, "auth_json", bool(sync_result)):
    return None
  if not getattr(sync_result, "google_oauth", bool(sync_result)):
    logger.warning("Native google_oauth sync failed; refreshed auth.json token is still active")
  return parsed_refresh or {}


def _apply_parsed_refresh_to_account_dict(
    account: dict[str, Any],
    parsed_refresh: dict[str, str | None] | None,
) -> None:
  if not parsed_refresh:
    return
  refresh_token = parsed_refresh.get("refreshToken")
  project_id = parsed_refresh.get("projectId")
  managed_project_id = parsed_refresh.get("managedProjectId")
  if refresh_token:
    account["refreshToken"] = refresh_token
  if project_id:
    account["projectId"] = project_id
  if managed_project_id:
    account["managedProjectId"] = managed_project_id


def _apply_parsed_refresh_to_managed_account(
    account: Any,
    parsed_refresh: dict[str, str | None] | None,
) -> None:
  if not parsed_refresh:
    return
  parts = account.refresh_parts
  parts.refresh_token = parsed_refresh.get("refreshToken") or parts.refresh_token
  parts.project_id = parsed_refresh.get("projectId") or parts.project_id
  parts.managed_project_id = parsed_refresh.get("managedProjectId") or parts.managed_project_id


def _now_ms() -> int:
  import time
  return int(time.time() * 1000)


def _coerce_expires_ms(value: Any) -> int | None:
  if isinstance(value, bool) or not isinstance(value, (int, float)):
    return None
  return int(value)


def _access_token_is_fresh(access_token: Any, expires_ms: Any, buffer_seconds: int) -> bool:
  if not isinstance(access_token, str) or not access_token:
    return False
  expires = _coerce_expires_ms(expires_ms)
  if expires is None:
    return False
  return expires > _now_ms() + max(0, int(buffer_seconds)) * 1000


def _account_dict_matches_managed_account(
    account_dict: dict[str, Any],
    account: Any,
    *,
    allow_refresh_mismatch: bool = False,
) -> bool:
  actual = _account_identity_for_account_dict(account_dict)
  expected = _account_identity_for_managed_account(account)
  keys = ("email", "project_id", "managed_project_id") if allow_refresh_mismatch else (
    "email", "refresh_token", "project_id", "managed_project_id"
  )
  for key in keys:
    expected_value = expected.get(key) or None
    actual_value = actual.get(key) or None
    if allow_refresh_mismatch:
      if expected_value is not None and actual_value is not None and expected_value != actual_value:
        return False
    elif expected_value != actual_value:
      return False
  return True


def _sync_managed_account_from_dict(account: Any, account_dict: dict[str, Any]) -> None:
  try:
    parts = account.refresh_parts
    parts.refresh_token = account_dict.get("refreshToken") or parts.refresh_token
    parts.project_id = account_dict.get("projectId") or parts.project_id
    parts.managed_project_id = account_dict.get("managedProjectId") or parts.managed_project_id
  except Exception:
    pass
  try:
    account.access = account_dict.get("accessToken") or account_dict.get("access") or getattr(account, "access", None)
    account.expires = (
      account_dict.get("accessTokenExpiresAt")
      or account_dict.get("expiresMs")
      or account_dict.get("expires")
      or getattr(account, "expires", None)
    )
    account.last_refresh_at = account_dict.get("lastRefreshAt") or getattr(account, "last_refresh_at", None)
  except Exception:
    pass


def _find_account_dict_for_managed_account(
    accounts: list[Any],
    account: Any,
    *,
    allow_refresh_mismatch: bool = False,
) -> tuple[int, dict[str, Any]] | tuple[None, None]:
  account_index = getattr(account, "index", None)
  if type(account_index) is int and 0 <= account_index < len(accounts):
    candidate = accounts[account_index]
    if isinstance(candidate, dict) and _account_dict_matches_managed_account(
      candidate, account, allow_refresh_mismatch=allow_refresh_mismatch
    ):
      return account_index, candidate
  for idx, candidate in enumerate(accounts):
    if isinstance(candidate, dict) and _account_dict_matches_managed_account(
      candidate, account, allow_refresh_mismatch=allow_refresh_mismatch
    ):
      return idx, candidate
  return None, None


def _load_cached_token_for_account(account: Any, buffer_seconds: int) -> dict[str, Any] | None:
  if _access_token_is_fresh(
    getattr(account, "access", None), getattr(account, "expires", None), buffer_seconds
  ):
    return {
      "access": getattr(account, "access"),
      "expires": getattr(account, "expires", None),
      "last_refresh_at": getattr(account, "last_refresh_at", None),
    }

  try:
    from .storage import load_accounts
    stored = load_accounts()
    accounts = stored.get("accounts", [])
    if not isinstance(accounts, list):
      return None
    _, stored_account = _find_account_dict_for_managed_account(
      accounts, account, allow_refresh_mismatch=True
    )
    if not isinstance(stored_account, dict):
      return None
    access_token = stored_account.get("accessToken") or stored_account.get("access")
    expires_ms = (
      stored_account.get("accessTokenExpiresAt")
      or stored_account.get("expiresMs")
      or stored_account.get("expires")
    )
    if not _access_token_is_fresh(access_token, expires_ms, buffer_seconds):
      return None
    _sync_managed_account_from_dict(account, stored_account)
    return {
      "access": access_token,
      "expires": expires_ms,
      "last_refresh_at": stored_account.get("lastRefreshAt"),
    }
  except Exception as e:
    logger.debug("Could not load cached Antigravity access token: %s", e)
    return None


def _persist_managed_account_state(
    account: Any,
    *,
    family: str | None = None,
    set_family_active: bool = False,
) -> bool:
  """Persist mutable fields for one account without rewriting the whole store."""
  try:
    from .storage import update_accounts
  except Exception:
    return False

  persisted = {"ok": False}

  def mutator(storage: dict[str, Any]) -> None:
    accounts = storage.get("accounts", [])
    if not isinstance(accounts, list):
      return
    idx, stored_account = _find_account_dict_for_managed_account(
      accounts, account, allow_refresh_mismatch=True
    )
    if idx is None or not isinstance(stored_account, dict):
      return
    persisted["ok"] = True

    token_fields_safe_to_update = True
    try:
      parts = account.refresh_parts
      stored_refresh = stored_account.get("refreshToken")
      account_refresh = parts.refresh_token
      if stored_refresh and account_refresh and stored_refresh != account_refresh:
        stored_last = _coerce_expires_ms(stored_account.get("lastRefreshAt"))
        account_last = _coerce_expires_ms(getattr(account, "last_refresh_at", None))
        token_fields_safe_to_update = account_last is not None and (
          stored_last is None or account_last >= stored_last
        )
      if token_fields_safe_to_update:
        stored_account["refreshToken"] = account_refresh
        stored_account["projectId"] = parts.project_id
        stored_account["managedProjectId"] = parts.managed_project_id
      else:
        _sync_managed_account_from_dict(account, stored_account)
    except Exception:
      pass

    if token_fields_safe_to_update:
      if getattr(account, "access", None):
        stored_account["accessToken"] = getattr(account, "access")
      if getattr(account, "expires", None) is not None:
        stored_account["accessTokenExpiresAt"] = getattr(account, "expires")
      if getattr(account, "last_refresh_at", None) is not None:
        stored_account["lastRefreshAt"] = getattr(account, "last_refresh_at")
    if getattr(account, "last_used", None) is not None:
      stored_account["lastUsed"] = getattr(account, "last_used")
    if getattr(account, "fingerprint", None):
      stored_account["fingerprint"] = getattr(account, "fingerprint")
    if getattr(account, "fingerprint_history", None):
      stored_account["fingerprintHistory"] = getattr(account, "fingerprint_history")
    try:
      rl_dict = account.rate_limit_reset_times.to_dict()
      if rl_dict:
        stored_account["rateLimitResetTimes"] = rl_dict
      else:
        stored_account.pop("rateLimitResetTimes", None)
    except Exception:
      pass
    cooldown_until = getattr(account, "cooling_down_until", None)
    if cooldown_until is not None:
      stored_account["coolingDownUntil"] = cooldown_until
      stored_account["cooldownReason"] = getattr(account, "cooldown_reason", None)
    else:
      stored_account.pop("coolingDownUntil", None)
      stored_account.pop("cooldownReason", None)

    if set_family_active and family in ("claude", "gemini"):
      family_map = storage.get("activeIndexByFamily")
      if not isinstance(family_map, dict):
        family_map = {"claude": 0, "gemini": 0}
      family_map[family] = idx
      storage["activeIndexByFamily"] = family_map

  try:
    update_accounts(mutator)
    return bool(persisted["ok"])
  except Exception as e:
    logger.debug("Could not persist Antigravity account state transactionally: %s", e)
    return False


def _select_request_account(model: str, header_style: str, config: Any) -> dict[str, Any] | None:
  try:
    from .accounts.shared import get_or_create_global_manager
    from .accounts.quota import compute_soft_quota_cache_ttl_ms
    from .token import parse_refresh_parts, refresh_access_token

    family = _model_family_for_model(model)
    soft_quota_cache_ttl_ms = compute_soft_quota_cache_ttl_ms(
      config.soft_quota_cache_ttl_minutes,
      config.quota_refresh_interval_minutes,
    )
    mgr = get_or_create_global_manager()
    account = mgr.get_current_or_next_for_family(
      family,
      model=model,
      strategy=config.account_selection_strategy,
      header_style=header_style,
      pid_offset_enabled=config.pid_offset_enabled,
      soft_quota_threshold_percent=config.soft_quota_threshold_percent,
      soft_quota_cache_ttl_ms=soft_quota_cache_ttl_ms,
    )
    if not account:
      return None

    buffer_seconds = int(getattr(config, "proactive_refresh_buffer_seconds", 1800) or 0)
    cached = _load_cached_token_for_account(account, buffer_seconds)
    if cached:
      access_token = cached["access"]
      expires_ms = cached.get("expires")
      logger.debug("Using cached Antigravity access token for account index=%s", account.index)
    else:
      packed_refresh = _packed_refresh_for_account(account)
      refreshed = refresh_access_token(
        {"refresh": packed_refresh, "email": account.email},
        persist=True,
        set_active=True,
      )
      if not refreshed or not refreshed.get("access"):
        return None

      access_token = refreshed["access"]
      expires_ms = refreshed.get("expires")
      parsed_refresh = _sync_refreshed_token_to_all_auth_stores(
        refreshed=refreshed,
        packed_refresh=packed_refresh,
        project_id=account.refresh_parts.project_id or "",
        email=account.email,
      )
      if parsed_refresh is None:
        return None
      if parsed_refresh:
        _apply_parsed_refresh_to_managed_account(account, parsed_refresh)
      account.access = access_token
      account.expires = expires_ms
      account.last_refresh_at = _now_ms()

    mgr.mark_account_used(account.index)
    persisted = _persist_managed_account_state(account, family=family, set_family_active=True)
    if not persisted:
      try:
        mgr.save_to_disk()
      except Exception:
        pass
    return {
      "access": access_token,
      "account": account,
      "account_index": account.index,
      "account_identity": _account_identity_for_managed_account(account),
      "family": family,
      "access_expires": expires_ms,
    }
  except Exception as e:
    logger.warning("Request-time account selection failed: %s", e)
    return None


def _inject_tool_call_ids(inner_request: dict) -> None:
  """Inject auto-generated IDs into functionCall/functionResponse for Claude.

  The Antigravity backend converts Gemini functionCall parts to Anthropic
  tool_use blocks when routing to Claude models. Anthropic requires every
  tool_use to have an ``id`` field, and every tool_result to have a matching
  ``tool_use_id``. Without IDs, the backend returns HTTP 400:
  ``messages.N.content.M.tool_use.id: Field required``.

  Follows the TypeScript original's two-pass approach (request.ts:1353-1416):
  1. Assign sequential IDs to functionCall objects (inside the object, NOT at
     the Part level — the Gemini proto rejects unknown Part fields)
  2. Match functionResponse objects to their calls by name (FIFO queue)

  Mutates ``inner_request["contents"]`` in-place.
  """
  contents = inner_request.get("contents")
  if not isinstance(contents, list):
    return

  counter = 0
  used_ids: set[str] = set()
  pending: dict[str, list[Any]] = {}  # functionName -> [id, id, ...] (FIFO queue)

  # Reserve existing functionCall IDs before generating new IDs so generated
  # IDs never collide with IDs already present later in the request.
  for content in contents:
    if not isinstance(content, dict):
      continue
    parts = content.get("parts")
    if not isinstance(parts, list):
      continue
    for part in parts:
      if not isinstance(part, dict):
        continue
      fc = part.get("functionCall")
      if not isinstance(fc, dict):
        continue
      call_id = fc.get("id")
      if isinstance(call_id, str) and call_id:
        used_ids.add(call_id)

  # Pass 1: assign IDs to functionCalls, build FIFO queues per name
  for content in contents:
    if not isinstance(content, dict):
      continue
    parts = content.get("parts")
    if not isinstance(parts, list):
      continue
    for part in parts:
      if not isinstance(part, dict):
        continue
      fc = part.get("functionCall")
      if isinstance(fc, dict):
        if not fc.get("id"):
          while True:
            counter += 1
            generated_id = f"tool-call-{counter}"
            if generated_id not in used_ids:
              break
          fc["id"] = generated_id
          used_ids.add(generated_id)
        else:
          call_id = fc.get("id")
          if isinstance(call_id, str):
            used_ids.add(call_id)
        name = str(fc.get("name") or f"tool-{counter}")
        pending.setdefault(name, []).append(fc["id"])

  # Pass 2: match functionResponses to pending calls (FIFO per name)
  for content in contents:
    if not isinstance(content, dict):
      continue
    parts = content.get("parts")
    if not isinstance(parts, list):
      continue
    for part in parts:
      if not isinstance(part, dict):
        continue
      fr = part.get("functionResponse")
      if isinstance(fr, dict):
        response_id = fr.get("id")
        name = str(fr.get("name") or "")
        queue = pending.get(name, [])
        if response_id:
          try:
            queue.remove(response_id)
          except ValueError:
            pass
          continue
        if queue:
          fr["id"] = queue.pop(0)


def _apply_claude_transforms(inner_request: dict) -> None:
  """Apply Claude-specific request transforms beyond tool_call IDs.

  The TypeScript original (transform/claude.ts) applies several transforms
  that are critical for Claude models to work through Antigravity:

  1. **VALIDATED mode**: Sets ``toolConfig.functionCallingConfig.mode`` to
     ``"VALIDATED"`` (Hermes sends ``"AUTO"``, which Claude's backend
     routing rejects with validation errors).

  2. **Thinking config snake_case**: Converts ``thinkingBudget`` →
     ``thinking_budget`` and ``includeThoughts`` → ``include_thoughts``.
     The Antigravity backend routes these to Anthropic's native API which
     expects snake_case keys (camelCase keys are silently ignored).

  3. **Placeholder for empty required**: Claude's VALIDATED mode requires
     every tool parameter schema to have at least one property in its
     ``required`` array. Hermes' ``sanitize_gemini_tool_parameters`` can
     produce schemas with empty ``required`` (or no ``required`` at all).
     Without the placeholder, Claude returns validation errors.

  Mutates ``inner_request`` in-place.
  """

  try:
    config = get_config()
    keep_thinking = bool(getattr(config, "keep_thinking", False))
  except Exception:
    keep_thinking = False
  if not keep_thinking:
    try:
      from .transform.thinking import deep_filter_thinking_blocks
      deep_filter_thinking_blocks(inner_request)
    except Exception:
      pass

  # 1. Create/normalize VALIDATED mode for tool calling
  tools = inner_request.get("tools")
  has_function_declarations = any(
    isinstance(group, dict) and group.get("functionDeclarations")
    for group in tools
  ) if isinstance(tools, list) else False
  if has_function_declarations and not isinstance(inner_request.get("toolConfig"), dict):
    inner_request["toolConfig"] = {}
  tool_config = inner_request.get("toolConfig")
  if isinstance(tool_config, dict):
    fcc = tool_config.get("functionCallingConfig")
    if isinstance(fcc, dict):
      fcc["mode"] = "VALIDATED"
    else:
      tool_config["functionCallingConfig"] = {"mode": "VALIDATED"}

  # 2. Convert thinking config keys to snake_case
  gen_config = inner_request.get("generationConfig")
  if isinstance(gen_config, dict):
    tc = gen_config.get("thinkingConfig")
    if isinstance(tc, dict):
      if "thinkingBudget" in tc:
        tc["thinking_budget"] = tc.pop("thinkingBudget")
      if "includeThoughts" in tc:
        tc["include_thoughts"] = tc.pop("includeThoughts")

  # 3. Add placeholder required property for tools with empty/missing required
  tools = inner_request.get("tools")
  if isinstance(tools, list):
    for tool_group in tools:
      if not isinstance(tool_group, dict):
        continue
      for fd in tool_group.get("functionDeclarations", []):
        if not isinstance(fd, dict):
          continue
        params = fd.get("parameters")
        if not isinstance(params, dict):
          continue
        required = params.get("required")
        if not isinstance(required, list) or len(required) == 0:
          props = params.get("properties")
          if not isinstance(props, dict):
            if params.get("type") != "object":
              continue
            props = {}
            params["properties"] = props
          # Add a _placeholder boolean property to satisfy VALIDATED mode
          props["_placeholder"] = {
            "type": "boolean",
            "description": "Placeholder. Always pass true.",
          }
          params["required"] = ["_placeholder"]


def _antigravity_request_hook(request: httpx.Request) -> None:
    # ── trace: write to marker file so we know the hook fired ──
    try:
        _trace("hook-fired", url=str(request.url)[:120])
    except Exception:
        pass

    if "cloudcode-pa" not in str(request.url):
        _trace("hook-skip", reason="url-no-cloudcode-pa")
        return

    config = get_config()
    
    try:
        body = json.loads(request.read())
    except Exception:
        _trace("hook-skip", reason="json-decode-failed")
        return
    
    if not isinstance(body, dict) or "request" not in body:
        _trace("hook-skip", reason="no-request-key", body_keys=str(list(body.keys()) if isinstance(body, dict) else type(body).__name__))
        return
    
    model = str(body.get("model", ""))
    header_style = _select_header_style_for_model(model, config.cli_first)
    request.extensions["antigravity_header_style"] = header_style
    request.extensions["antigravity_model_family"] = _model_family_for_model(model)

    if header_style == "gemini-cli":
        logger.warning(
            "Gemini CLI header style is DEPRECATED — Gemini CLI sunsets 2026-06-18. "
            "Set cli_first: false in config to use the Antigravity header style."
        )

    selected = _select_request_account(model, header_style, config)
    model = resolve_model_for_header_style(model, header_style)
    
    for key in list(request.headers.keys()):
        if key.lower() not in ("host", "authorization", "content-type", "accept", "accept-encoding", "content-length"):
            del request.headers[key]

    account = selected.get("account") if selected else None
    if account is not None:
        try:
            fingerprint_changed = False
            fp = getattr(account, "fingerprint", None)
            if not fp:
                fp = generate_fingerprint()
                account.fingerprint = fp
                fingerprint_changed = True
            if isinstance(fp, dict):
                if update_fingerprint_version(fp):
                    fingerprint_changed = True
                for key, val in build_fingerprint_headers(fp).items():
                    request.headers[key] = val
                cm = fp.get("clientMetadata")
                if cm:
                    request.headers["Client-Metadata"] = json.dumps(cm)
                if fingerprint_changed:
                    if not _persist_managed_account_state(account):
                        try:
                            from .accounts.shared import get_global_manager
                            mgr = get_global_manager()
                            if mgr:
                                mgr.save_to_disk()
                        except Exception:
                            pass
        except Exception:
            pass
    else:
        new_headers = build_antigravity_headers(header_style=header_style)
        for key, val in new_headers.items():
            request.headers[key] = val

    if selected and selected.get("access"):
        selected_index = selected.get("account_index")
        if type(selected_index) is int:
            request.extensions["antigravity_selected_account_index"] = selected_index
            request.extensions["antigravity_selected_account_identity"] = selected.get("account_identity")
        request.headers["Authorization"] = f"Bearer {selected['access']}"
    else:
        request.extensions["antigravity_account_selection_failed"] = True
        if "Authorization" in request.headers:
            del request.headers["Authorization"]

    logger.debug("Antigravity headers injected for model=%s", model)


def _antigravity_response_hook(response: httpx.Response) -> None:
    from .config import get_config
    config = get_config()
    model = _request_model_from_response(response)
    try:
        request_extensions = response.request.extensions
    except Exception:
        request_extensions = {}
    family = request_extensions.get("antigravity_model_family") or _model_family_for_model(model)
    header_style = request_extensions.get(
        "antigravity_header_style"
    ) or _select_header_style_for_model(model, config.cli_first)

    if response.status_code == 401 and config.proactive_token_refresh:
        try:
            from .token import format_refresh_parts, refresh_access_token
            from .storage import (
                is_valid_account_index,
                load_accounts,
                resolve_active_account_index,
                update_accounts,
            )
            d = load_accounts()
            accs = d.get("accounts", [])
            if not isinstance(accs, list) or not accs:
                return
            idx = resolve_active_account_index(d, family=family)
            selected_idx = request_extensions.get("antigravity_selected_account_index")
            selected_identity = request_extensions.get("antigravity_selected_account_identity")
            selected_idx_present = type(selected_idx) is int
            if selected_idx_present:
                if not is_valid_account_index(selected_idx, len(accs)):
                    return
                idx = selected_idx
            if 0 <= idx < len(accs):
                a = accs[idx]
                if selected_idx_present and not _account_identity_matches(
                    _account_identity_for_account_dict(a),
                    selected_identity,
                ):
                    return
                raw_refresh = a.get("refreshToken", "")
                if not raw_refresh:
                    return
                packed_refresh = format_refresh_parts({
                    "refreshToken": raw_refresh,
                    "projectId": a.get("projectId") or "",
                    "managedProjectId": a.get("managedProjectId") or "",
                })
                r = refresh_access_token(
                    {"refresh": packed_refresh, "email": a.get("email")},
                    persist=True,
                    set_active=True,
                )
                if r.get("access"):
                    parsed_refresh = _sync_refreshed_token_to_all_auth_stores(
                        refreshed=r,
                        packed_refresh=packed_refresh,
                        project_id=a.get("projectId") or "",
                        email=a.get("email"),
                    )
                    if parsed_refresh is not None:
                        def persist_refreshed_account(storage: dict[str, Any]) -> None:
                            accounts = storage.get("accounts", [])
                            if not isinstance(accounts, list):
                                return
                            target = None
                            if selected_idx_present and is_valid_account_index(idx, len(accounts)):
                                candidate = accounts[idx]
                                if isinstance(candidate, dict) and _account_identity_matches(
                                    _account_identity_for_account_dict(candidate),
                                    selected_identity,
                                ):
                                    target = candidate
                            if target is None:
                                for candidate in accounts:
                                    if (
                                        isinstance(candidate, dict)
                                        and candidate.get("refreshToken") == raw_refresh
                                        and (candidate.get("email") or None) == (a.get("email") or None)
                                    ):
                                        target = candidate
                                        break
                            if not isinstance(target, dict):
                                return
                            _apply_parsed_refresh_to_account_dict(target, parsed_refresh)
                            target["accessToken"] = r.get("access")
                            if r.get("expires") is not None:
                                target["accessTokenExpiresAt"] = r.get("expires")
                            target["lastRefreshAt"] = _now_ms()

                        update_accounts(persist_refreshed_account)
                        response.request.extensions["antigravity_retry_ready"] = True
                        response.request.extensions["antigravity_retry_action"] = "refreshed-selected-account"
        except Exception as e:
            logger.warning("Token refresh failed: %s", e)

    if response.status_code == 403:
        try:
            from .accounts.manager import get_or_create_global_manager
            from .accounts.quota import compute_soft_quota_cache_ttl_ms
            mgr = get_or_create_global_manager()
            active = _response_account_for_request(mgr, request_extensions, family)
            if active:
                import time
                active.cooling_down_until = (time.time() + 86400) * 1000
                active.cooldown_reason = "auth-failure"
                if not _persist_managed_account_state(active, family=family):
                    try:
                        mgr.save_to_disk()
                    except Exception:
                        pass
                soft_quota_cache_ttl_ms = compute_soft_quota_cache_ttl_ms(
                    config.soft_quota_cache_ttl_minutes,
                    config.quota_refresh_interval_minutes,
                )
                next_acc = mgr.get_current_or_next_for_family(
                    family,
                    model=model,
                    strategy=config.account_selection_strategy,
                    header_style=header_style,
                    pid_offset_enabled=config.pid_offset_enabled,
                    soft_quota_threshold_percent=config.soft_quota_threshold_percent,
                    soft_quota_cache_ttl_ms=soft_quota_cache_ttl_ms,
                )
                if next_acc is None:
                    logger.warning("All %s accounts exhausted — cannot rotate after 403", family)
                elif next_acc.index != active.index:
                    from .token import format_refresh_parts, refresh_access_token
                    packed_refresh = format_refresh_parts({
                        "refreshToken": next_acc.refresh_parts.refresh_token,
                        "projectId": next_acc.refresh_parts.project_id or "",
                        "managedProjectId": next_acc.refresh_parts.managed_project_id or "",
                    })
                    r = refresh_access_token(
                        {"refresh": packed_refresh, "email": next_acc.email},
                        persist=True,
                        set_active=True,
                    )
                    if r.get("access"):
                        parsed_refresh = _sync_refreshed_token_to_all_auth_stores(
                            refreshed=r,
                            packed_refresh=packed_refresh,
                            project_id=next_acc.refresh_parts.project_id or "",
                            email=next_acc.email,
                        )
                        _apply_parsed_refresh_to_managed_account(next_acc, parsed_refresh)
                        next_acc.access = r.get("access")
                        next_acc.expires = r.get("expires")
                        next_acc.last_refresh_at = _now_ms()
                        if not _persist_managed_account_state(next_acc, family=family, set_family_active=True):
                            try:
                                mgr.save_to_disk()
                            except Exception:
                                pass
                        logger.info("Rotated to %s after 403 for %s", next_acc.email, family)
                        response.request.extensions["antigravity_retry_ready"] = True
                        response.request.extensions["antigravity_retry_action"] = "rotated-after-403"
        except Exception as e:
            logger.warning("403 handler error: %s", e)

    if response.status_code == 429 and config.switch_on_first_rate_limit:
        try:
            from .accounts.manager import get_or_create_global_manager
            from .accounts.quota import compute_soft_quota_cache_ttl_ms
            from .accounts.ratelimit import mark_rate_limited, parse_rate_limit_reason
            from .transform.response import extract_retry_info
            mgr = get_or_create_global_manager()
            active = _response_account_for_request(mgr, request_extensions, family)
            if active:
                retry_after_ms = float(config.default_retry_after_seconds * 1000)
                rh = response.headers.get("Retry-After") or response.headers.get("retry-after")
                if rh:
                    try:
                        retry_after_ms = float(rh) * 1000
                    except ValueError:
                        pass

                message = None
                raw_reason = None
                try:
                    try:
                        parsed_body = response.json()
                    except httpx.ResponseNotRead:
                        response.read()
                        parsed_body = response.json()
                    if isinstance(parsed_body, dict):
                        retry_info = extract_retry_info(parsed_body)
                        if isinstance(retry_info, dict):
                            retry_delay_ms = retry_info.get("retryDelayMs")
                            if isinstance(retry_delay_ms, (int, float)) and retry_delay_ms > 0:
                                retry_after_ms = float(retry_delay_ms)

                        error = parsed_body.get("error")
                        if isinstance(error, dict):
                            error_message = error.get("message")
                            if isinstance(error_message, str):
                                message = error_message
                            error_status = error.get("status")
                            if isinstance(error_status, str):
                                raw_reason = error_status
                except Exception as e:
                    logger.debug("Unable to parse 429 response body for rate-limit reason: %s", e)

                parsed_reason = parse_rate_limit_reason(raw_reason, message, response.status_code)
                mark_with_reason = getattr(mgr, "mark_rate_limited_with_reason", None)
                if callable(mark_with_reason):
                    mark_with_reason(
                        active,
                        family,
                        header_style,
                        model,
                        parsed_reason,
                        retry_after_ms=retry_after_ms,
                    )
                else:
                    mark_rate_limited(active, retry_after_ms, family, header_style, model)
                if not _persist_managed_account_state(active, family=family):
                    try:
                        mgr.save_to_disk()
                    except Exception:
                        pass
                soft_quota_cache_ttl_ms = compute_soft_quota_cache_ttl_ms(
                    config.soft_quota_cache_ttl_minutes,
                    config.quota_refresh_interval_minutes,
                )
                next_acc = mgr.get_current_or_next_for_family(
                    family,
                    model=model,
                    strategy=config.account_selection_strategy,
                    header_style=header_style,
                    pid_offset_enabled=config.pid_offset_enabled,
                    soft_quota_threshold_percent=config.soft_quota_threshold_percent,
                    soft_quota_cache_ttl_ms=soft_quota_cache_ttl_ms,
                )
                if next_acc is None:
                    logger.warning("All %s accounts exhausted — cannot rotate after rate limit", family)
                elif next_acc.index != active.index:
                    from .token import format_refresh_parts, refresh_access_token
                    packed_refresh = format_refresh_parts({
                        "refreshToken": next_acc.refresh_parts.refresh_token,
                        "projectId": next_acc.refresh_parts.project_id or "",
                        "managedProjectId": next_acc.refresh_parts.managed_project_id or "",
                    })
                    r = refresh_access_token(
                        {"refresh": packed_refresh, "email": next_acc.email},
                        persist=True,
                        set_active=True,
                    )
                    if r.get("access"):
                        parsed_refresh = _sync_refreshed_token_to_all_auth_stores(
                            refreshed=r,
                            packed_refresh=packed_refresh,
                            project_id=next_acc.refresh_parts.project_id or "",
                            email=next_acc.email,
                        )
                        _apply_parsed_refresh_to_managed_account(next_acc, parsed_refresh)
                        next_acc.access = r.get("access")
                        next_acc.expires = r.get("expires")
                        next_acc.last_refresh_at = _now_ms()
                        if not _persist_managed_account_state(next_acc, family=family, set_family_active=True):
                            try:
                                mgr.save_to_disk()
                            except Exception:
                                pass
                        logger.info("Rotated to %s after rate limit for %s", next_acc.email, family)
                        response.request.extensions["antigravity_retry_ready"] = True
                        response.request.extensions["antigravity_retry_action"] = "rotated-after-429"
        except Exception as e:
            logger.warning("Rate limit handler error: %s", e)

    if response.status_code >= 500:
        try:
            from .endpoints import mark_endpoint_failed
            from urllib.parse import urlparse
            p = urlparse(str(response.request.url))
            mark_endpoint_failed(f"https://{p.netloc}")
        except Exception:
            pass


def _is_cloudcode_request(request: httpx.Request) -> bool:
    return "cloudcode-pa" in str(request.url)


def _request_body_is_replayable(request: httpx.Request) -> bool:
    if request.method.upper() not in ("POST", "PUT", "PATCH"):
        return True
    try:
        request.read()
        _ = request.content
        return True
    except Exception:
        return False


_RETRY_REPOPULATED_HEADER_NAMES = {
    "authorization",
    "user-agent",
    "x-goog-api-client",
    "client-metadata",
}


def _headers_for_retry(request: httpx.Request) -> httpx.Headers:
    headers = request.headers.copy()
    for name in list(headers.keys()):
        lower = name.lower()
        if lower in _RETRY_REPOPULATED_HEADER_NAMES:
            del headers[name]
        elif lower.startswith("antigravity-") or lower.startswith("x-antigravity-"):
            del headers[name]
    return headers


def _clone_request_for_retry(request: httpx.Request) -> httpx.Request | None:
    try:
        request.read()
        content = request.content
    except Exception:
        return None
    retry_extensions = dict(request.extensions)
    retry_extensions["antigravity_retry_attempted"] = True
    retry_extensions["antigravity_retry_original_status"] = retry_extensions.get(
        "antigravity_retry_original_status"
    )
    return httpx.Request(
        request.method,
        request.url,
        headers=_headers_for_retry(request),
        content=content,
        extensions=retry_extensions,
    )


def _response_is_retryable(response: httpx.Response) -> bool:
    request = response.request
    if response.status_code not in (401, 403, 429):
        return False
    if not _is_cloudcode_request(request):
        return False
    if request.extensions.get("antigravity_retry_attempted"):
        return False
    if request.extensions.get("antigravity_account_selection_failed"):
        return False
    if not request.extensions.get("antigravity_retry_ready"):
        return False
    return True


def _send_with_antigravity_retry(original_send, request: httpx.Request, *args: Any, **kwargs: Any) -> httpx.Response:
    response = original_send(request, *args, **kwargs)
    if not _response_is_retryable(response):
        return response
    if kwargs.get("stream"):
        response.request.extensions["antigravity_retry_skipped_reason"] = "streaming response"
        logger.warning("Antigravity request got HTTP %s; automatic retry skipped for streaming response", response.status_code)
        return response
    if not _request_body_is_replayable(response.request):
        response.request.extensions["antigravity_retry_skipped_reason"] = "request body is not replayable"
        logger.warning("Antigravity request got HTTP %s; automatic retry skipped because body is not replayable", response.status_code)
        return response
    retry_request = _clone_request_for_retry(response.request)
    if retry_request is None:
        response.request.extensions["antigravity_retry_skipped_reason"] = "request clone failed"
        logger.warning("Antigravity request got HTTP %s; automatic retry skipped because request clone failed", response.status_code)
        return response
    retry_request.extensions["antigravity_retry_original_status"] = response.status_code
    try:
        response.close()
    except Exception:
        pass
    logger.info("Retrying Antigravity request once after HTTP %s", response.status_code)
    return original_send(retry_request, *args, **kwargs)


def _wrap_http_client(http_client: httpx.Client) -> httpx.Client:
    _trace("wrap-http-client", id_hex=hex(id(http_client)))
    if not http_client.event_hooks.get("request"):
        http_client.event_hooks["request"] = []
    if not http_client.event_hooks.get("response"):
        http_client.event_hooks["response"] = []
    if _antigravity_request_hook not in http_client.event_hooks["request"]:
        http_client.event_hooks["request"].append(_antigravity_request_hook)
    if _antigravity_response_hook not in http_client.event_hooks["response"]:
        http_client.event_hooks["response"].append(_antigravity_response_hook)
    if not getattr(http_client, "_antigravity_retry_send_wrapped", False):
        original_send = http_client.send

        def send_with_retry(request: httpx.Request, *args: Any, **kwargs: Any) -> httpx.Response:
            _trace("send-with-retry-called", url=str(request.url)[:100])
            return _send_with_antigravity_retry(original_send, request, *args, **kwargs)

        http_client.send = send_with_retry  # type: ignore[method-assign]
        setattr(http_client, "_antigravity_retry_send_wrapped", True)
    return http_client


_GLOBAL_HTTPX_HOOK_INSTALLED = False


def _install_global_httpx_hook() -> None:
    """Monkey-patch httpx.Client.send, .post, AND .stream to catch every request.

    We've seen evidence that some code paths use httpx differently —
    possibly through subclasses that override send/post.  Patching all
    three entry-points guarantees interception.
    """
    global _GLOBAL_HTTPX_HOOK_INSTALLED
    if _GLOBAL_HTTPX_HOOK_INSTALLED:
        return
    _GLOBAL_HTTPX_HOOK_INSTALLED = True

    # ── Level 1: override send (catches internal Client usage) ──
    _original_client_send = httpx.Client.send

    def _global_send(client_self, request, *args, **kwargs):
        _trace("global-send-called", url=str(request.url)[:120])
        try:
            _antigravity_request_hook(request)
        except Exception:
            pass
        response = _original_client_send(client_self, request, *args, **kwargs)
        try:
            _antigravity_response_hook(response)
        except Exception:
            pass
        return response

    httpx.Client.send = _global_send  # type: ignore[method-assign]

    # ── Level 2: override post — ensures post() routes through our overridden send() ──
    _original_client_post = httpx.Client.post

    def _global_post(client_self, url, *, json=None, content=None, data=None,
                     files=None, headers=None, params=None, **kwargs):
        request = client_self.build_request(
            "POST", url, json=json, content=content, data=data,
            files=files, headers=headers, params=params, **kwargs,
        )
        return client_self.send(request)

    httpx.Client.post = _global_post  # type: ignore[method-assign]

    # ── Level 3: override stream — ensures stream() routes through our overridden send() ──
    _original_client_stream = httpx.Client.stream

    def _global_stream(client_self, method, url, *, json=None, content=None,
                       data=None, files=None, headers=None, params=None, **kwargs):
        request = client_self.build_request(
            method, url, json=json, content=content, data=data,
            files=files, headers=headers, params=params, **kwargs,
        )
        return _original_client_stream(
            client_self, method, url,
            json=json, content=content, data=data,
            files=files, headers=headers, params=params, **kwargs,
        )

    httpx.Client.stream = _global_stream  # type: ignore[method-assign]
    _trace("global-httpx-hook-installed")


def install() -> bool:
  global _PATCHED, _ORIGINAL_INIT, _ORIGINAL_WRAP_CODE_ASSIST, _ORIGINAL_ENSURE_PROJECT_CONTEXT
  if _PATCHED:
    return False

  # ── Global safety net: wrap every httpx.Client so we catch requests
  #     regardless of which subclass or code path creates them. ──
  _install_global_httpx_hook()

  try:
    from agent.gemini_cloudcode_adapter import GeminiCloudCodeClient, wrap_code_assist_request
  except ImportError:
    _trace("install-fail", reason="import-error-gemini-cloudcode-adapter")
    return False
  _ORIGINAL_INIT = GeminiCloudCodeClient.__init__
  _ORIGINAL_ENSURE_PROJECT_CONTEXT = getattr(
    GeminiCloudCodeClient,
    "_ensure_project_context",
    None,
  )
  # Guard: if already patched by another plugin, don't chain
  if getattr(_ORIGINAL_INIT, '__name__', '') == '_patched_init':
    logger.warning("Interceptor already patched — skipping install")
    return False
  _ORIGINAL_WRAP_CODE_ASSIST = wrap_code_assist_request

  def _patched_init(self, *args: Any, **kwargs: Any) -> None:
    _trace("patched-init-called", cls=type(self).__name__)
    _ORIGINAL_INIT(self, *args, **kwargs)
    _wrap_http_client(self._http)

  def _patched_wrap_code_assist(*, project_id, model, inner_request, user_prompt_id=None):
    _trace("patched-wrap-called", model=str(model)[:80])
    resolved_model = model
    if isinstance(model, str):
      try:
        cli_first = bool(getattr(get_config(), "cli_first", False))
      except Exception:
        cli_first = False
      header_style = _select_header_style_for_model(model, cli_first)
      resolved_model = resolve_model_for_header_style(model, header_style)
    transform_model = f"{model} {resolved_model}" if isinstance(model, str) else str(resolved_model)
    if isinstance(inner_request, dict) and "claude" in transform_model.lower():
      _inject_tool_call_ids(inner_request)
      _apply_claude_transforms(inner_request)
    return _ORIGINAL_WRAP_CODE_ASSIST(
      project_id=project_id, model=resolved_model,
      inner_request=inner_request, user_prompt_id=user_prompt_id,
    )

  def _patched_ensure_project_context(self, access_token: str, model: str):
    if getattr(self, "_project_context", None) is not None:
      return self._project_context

    env_project = ""
    stored_project = ""
    managed_project = ""
    try:
      from agent import google_oauth
      env_project = google_oauth.resolve_project_id_from_env()
      creds = google_oauth.load_credentials()
      stored_project = getattr(creds, "project_id", "") if creds else ""
      managed_project = getattr(creds, "managed_project_id", "") if creds else ""
    except Exception as exc:
      google_oauth = None
      logger.debug("Could not read Hermes google_oauth project context: %s", exc)

    from .project_context import resolve_antigravity_project_context

    ctx = resolve_antigravity_project_context(
      access_token,
      configured_project_id=getattr(self, "_configured_project_id", "") or "",
      env_project_id=env_project or "",
      stored_project_id=stored_project or "",
      managed_project_id=managed_project or "",
    )

    try:
      if (getattr(ctx, "project_id", "") or getattr(ctx, "managed_project_id", "")) and google_oauth:
        google_oauth.update_project_ids(
          project_id=getattr(ctx, "project_id", "") or "",
          managed_project_id=getattr(ctx, "managed_project_id", "") or "",
        )
    except Exception as exc:
      logger.debug("Could not persist Antigravity project context to google_oauth store: %s", exc)

    self._project_context = ctx
    return ctx

  GeminiCloudCodeClient.__init__ = _patched_init
  if _ORIGINAL_ENSURE_PROJECT_CONTEXT is not None:
    GeminiCloudCodeClient._ensure_project_context = _patched_ensure_project_context
  import agent.gemini_cloudcode_adapter as gca
  gca.wrap_code_assist_request = _patched_wrap_code_assist
  _PATCHED = True
  _trace("install-ok")
  logger.info("Antigravity interceptor installed (headers + tool_call id injection + response hooks)")
  return True


def is_installed() -> bool:
    return _PATCHED


def uninstall() -> bool:
  """Restore original GeminiCloudCodeClient.__init__ and wrap_code_assist_request.

  Returns True if successfully uninstalled, False if not installed."""
  global _PATCHED, _ORIGINAL_INIT, _ORIGINAL_WRAP_CODE_ASSIST, _ORIGINAL_ENSURE_PROJECT_CONTEXT
  if not _PATCHED:
    return False
  try:
    from agent.gemini_cloudcode_adapter import GeminiCloudCodeClient
    import agent.gemini_cloudcode_adapter as gca
    if _ORIGINAL_INIT is not None:
      GeminiCloudCodeClient.__init__ = _ORIGINAL_INIT
    if _ORIGINAL_ENSURE_PROJECT_CONTEXT is not None:
      GeminiCloudCodeClient._ensure_project_context = _ORIGINAL_ENSURE_PROJECT_CONTEXT
    if _ORIGINAL_WRAP_CODE_ASSIST is not None:
      gca.wrap_code_assist_request = _ORIGINAL_WRAP_CODE_ASSIST
    _PATCHED = False
    _ORIGINAL_INIT = None
    _ORIGINAL_WRAP_CODE_ASSIST = None
    _ORIGINAL_ENSURE_PROJECT_CONTEXT = None
    logger.info("Antigravity interceptor uninstalled")
    return True
  except Exception as e:
    logger.warning("Failed to uninstall interceptor: %s", e)
    return False
