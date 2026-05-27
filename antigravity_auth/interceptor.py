"""HTTP interceptor — injects Antigravity headers via httpx event hooks."""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from .config import get_config
from .transform.envelope import (
    build_antigravity_headers,
    resolve_model_for_header_style,
)

logger = logging.getLogger(__name__)

_PATCHED = False
_ORIGINAL_INIT = None
_ORIGINAL_WRAP_CODE_ASSIST = None


def _model_family_for_model(model: str) -> str:
  lower = (model or "").lower()
  if "claude" in lower:
    return "claude"
  return "gemini"


def _select_header_style_for_model(model: str, cli_first: bool) -> str:
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


def _packed_refresh_for_account(account: Any) -> str:
  from .token import format_refresh_parts
  parts = account.refresh_parts
  return format_refresh_parts({
    "refreshToken": parts.refresh_token,
    "projectId": parts.project_id or "",
    "managedProjectId": parts.managed_project_id or "",
  })


def _select_request_account(model: str, header_style: str, config: Any) -> dict[str, Any] | None:
  try:
    from .accounts.shared import get_or_create_global_manager
    from .accounts.quota import compute_soft_quota_cache_ttl_ms
    from .token import parse_refresh_parts, refresh_access_token
    from .auth_sync import sync_token_to_all_auth_stores

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

    packed_refresh = _packed_refresh_for_account(account)
    refreshed = refresh_access_token({"refresh": packed_refresh, "email": account.email})
    if not refreshed or not refreshed.get("access"):
      return None

    rotated_refresh = refreshed.get("refresh")
    sync_refresh = rotated_refresh or packed_refresh
    parsed_refresh = parse_refresh_parts(rotated_refresh) if rotated_refresh else None
    sync_project_id = (
      (parsed_refresh.get("projectId") if parsed_refresh else None)
      or account.refresh_parts.project_id
      or ""
    )
    sync_ok = sync_token_to_all_auth_stores(
      access_token=refreshed["access"],
      refresh_token=sync_refresh,
      project_id=sync_project_id,
      email=account.email,
      expires_ms=refreshed.get("expires"),
      set_active=True,
    )
    if not sync_ok:
      return None

    if parsed_refresh:
      account.refresh_parts.refresh_token = (
        parsed_refresh.get("refreshToken") or account.refresh_parts.refresh_token
      )
      account.refresh_parts.project_id = (
        parsed_refresh.get("projectId") or account.refresh_parts.project_id
      )
      account.refresh_parts.managed_project_id = (
        parsed_refresh.get("managedProjectId") or account.refresh_parts.managed_project_id
      )
    mgr.mark_account_used(account.index)
    mgr.save_to_disk()
    return {"access": refreshed["access"], "account": account, "family": family}
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
  pending: dict = {}  # functionName -> [id, id, ...] (FIFO queue)

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
      if isinstance(fc, dict) and not fc.get("id"):
        counter += 1
        fc["id"] = f"tool-call-{counter}"
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
      if isinstance(fr, dict) and not fr.get("id"):
        name = str(fr.get("name") or "")
        queue = pending.get(name, [])
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

  # 1. Set VALIDATED mode for tool calling
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
          if isinstance(props, dict):
            # Add a _placeholder boolean property to satisfy VALIDATED mode
            props["_placeholder"] = {
              "type": "boolean",
              "description": "Placeholder. Always pass true.",
            }
            params["required"] = ["_placeholder"]


def _antigravity_request_hook(request: httpx.Request) -> None:
    if "cloudcode-pa" not in str(request.url):
        return

    config = get_config()
    
    try:
        body = json.loads(request.read())
    except Exception:
        return
    
    if not isinstance(body, dict) or "request" not in body:
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
    
    new_headers = build_antigravity_headers(header_style=header_style)
    for key in list(request.headers.keys()):
        if key.lower() not in ("host", "authorization", "content-type", "accept", "accept-encoding", "content-length"):
            del request.headers[key]
    for key, val in new_headers.items():
        request.headers[key] = val

    try:
        from .fingerprint import generate_fingerprint
        fp = generate_fingerprint()
        if fp:
            cm = fp.get("clientMetadata")
            if cm:
                request.headers["Client-Metadata"] = json.dumps(cm)
    except Exception:
        pass

    if selected and selected.get("access"):
        request.headers["Authorization"] = f"Bearer {selected['access']}"

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
            from .storage import load_accounts
            from .auth_sync import sync_token_to_google_oauth
            d = load_accounts()
            accs = d.get("accounts", [])
            idx = d.get("activeIndex", 0)
            if 0 <= idx < len(accs):
                a = accs[idx]
                raw_refresh = a.get("refreshToken", "")
                if not raw_refresh:
                    return
                packed_refresh = format_refresh_parts({
                    "refreshToken": raw_refresh,
                    "projectId": a.get("projectId") or "",
                    "managedProjectId": a.get("managedProjectId") or "",
                })
                r = refresh_access_token({"refresh": packed_refresh, "email": a.get("email")})
                if r.get("access"):
                    synced_refresh = r.get("refresh") or packed_refresh
                    sync_token_to_google_oauth(
                        access_token=r["access"], refresh_token=synced_refresh,
                        project_id=a.get("projectId") or "", email=a.get("email"),
                        expires_ms=r.get("expires"),
                    )
        except Exception as e:
            logger.warning("Token refresh failed: %s", e)

    if response.status_code == 403:
        try:
            from .accounts.manager import get_or_create_global_manager
            from .accounts.quota import compute_soft_quota_cache_ttl_ms
            mgr = get_or_create_global_manager()
            active = mgr.get_current_account_for_family(family)
            if active:
                import time
                active.cooling_down_until = (time.time() + 86400) * 1000
                active.cooldown_reason = "auth-failure"
                mgr.save_to_disk()
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
                    from .auth_sync import sync_token_to_google_oauth
                    packed_refresh = format_refresh_parts({
                        "refreshToken": next_acc.refresh_parts.refresh_token,
                        "projectId": next_acc.refresh_parts.project_id or "",
                        "managedProjectId": next_acc.refresh_parts.managed_project_id or "",
                    })
                    r = refresh_access_token({"refresh": packed_refresh, "email": next_acc.email})
                    if r.get("access"):
                        synced_refresh = r.get("refresh") or packed_refresh
                        sync_token_to_google_oauth(
                            access_token=r["access"], refresh_token=synced_refresh,
                            project_id=next_acc.refresh_parts.project_id or "", email=next_acc.email,
                            expires_ms=r.get("expires"),
                        )
                        logger.info("Rotated to %s after 403 for %s", next_acc.email, family)
        except Exception as e:
            logger.warning("403 handler error: %s", e)

    if response.status_code == 429 and config.switch_on_first_rate_limit:
        try:
            from .accounts.manager import get_or_create_global_manager
            from .accounts.quota import compute_soft_quota_cache_ttl_ms
            from .accounts.ratelimit import mark_rate_limited
            mgr = get_or_create_global_manager()
            active = mgr.get_current_account_for_family(family)
            if active:
                retry = config.default_retry_after_seconds
                rh = response.headers.get("Retry-After") or response.headers.get("retry-after")
                if rh:
                    try: retry = int(rh)
                    except ValueError: pass
                mark_rate_limited(active, float(retry * 1000), family, header_style, model)
                mgr.save_to_disk()
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
                    from .auth_sync import sync_token_to_google_oauth
                    packed_refresh = format_refresh_parts({
                        "refreshToken": next_acc.refresh_parts.refresh_token,
                        "projectId": next_acc.refresh_parts.project_id or "",
                        "managedProjectId": next_acc.refresh_parts.managed_project_id or "",
                    })
                    r = refresh_access_token({"refresh": packed_refresh, "email": next_acc.email})
                    if r.get("access"):
                        synced_refresh = r.get("refresh") or packed_refresh
                        sync_token_to_google_oauth(
                            access_token=r["access"], refresh_token=synced_refresh,
                            project_id=next_acc.refresh_parts.project_id or "", email=next_acc.email,
                            expires_ms=r.get("expires"),
                        )
                        logger.info("Rotated to %s after rate limit for %s", next_acc.email, family)
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


def _wrap_http_client(http_client: httpx.Client) -> httpx.Client:
    if not http_client.event_hooks.get("request"):
        http_client.event_hooks["request"] = []
    if not http_client.event_hooks.get("response"):
        http_client.event_hooks["response"] = []
    http_client.event_hooks["request"].append(_antigravity_request_hook)
    http_client.event_hooks["response"].append(_antigravity_response_hook)
    return http_client


def install() -> bool:
  global _PATCHED, _ORIGINAL_INIT, _ORIGINAL_WRAP_CODE_ASSIST
  if _PATCHED:
    return False
  try:
    from agent.gemini_cloudcode_adapter import GeminiCloudCodeClient, wrap_code_assist_request
  except ImportError:
    return False
  _ORIGINAL_INIT = GeminiCloudCodeClient.__init__
  # Guard: if already patched by another plugin, don't chain
  if getattr(_ORIGINAL_INIT, '__name__', '') == '_patched_init':
    logger.warning("Interceptor already patched — skipping install")
    return False
  _ORIGINAL_WRAP_CODE_ASSIST = wrap_code_assist_request

  def _patched_init(self, *args: Any, **kwargs: Any) -> None:
    _ORIGINAL_INIT(self, *args, **kwargs)
    _wrap_http_client(self._http)

  def _patched_wrap_code_assist(*, project_id, model, inner_request, user_prompt_id=None):
    if isinstance(inner_request, dict) and isinstance(model, str) and "claude" in model.lower():
      _inject_tool_call_ids(inner_request)
      _apply_claude_transforms(inner_request)
    return _ORIGINAL_WRAP_CODE_ASSIST(
      project_id=project_id, model=model,
      inner_request=inner_request, user_prompt_id=user_prompt_id,
    )

  GeminiCloudCodeClient.__init__ = _patched_init
  import agent.gemini_cloudcode_adapter as gca
  gca.wrap_code_assist_request = _patched_wrap_code_assist
  _PATCHED = True
  logger.info("Antigravity interceptor installed (headers + tool_call id injection + response hooks)")
  return True


def is_installed() -> bool:
    return _PATCHED


def uninstall() -> bool:
  """Restore original GeminiCloudCodeClient.__init__ and wrap_code_assist_request.

  Returns True if successfully uninstalled, False if not installed."""
  global _PATCHED, _ORIGINAL_INIT, _ORIGINAL_WRAP_CODE_ASSIST
  if not _PATCHED:
    return False
  try:
    from agent.gemini_cloudcode_adapter import GeminiCloudCodeClient
    import agent.gemini_cloudcode_adapter as gca
    if _ORIGINAL_INIT is not None:
      GeminiCloudCodeClient.__init__ = _ORIGINAL_INIT
    if _ORIGINAL_WRAP_CODE_ASSIST is not None:
      gca.wrap_code_assist_request = _ORIGINAL_WRAP_CODE_ASSIST
    _PATCHED = False
    _ORIGINAL_INIT = None
    _ORIGINAL_WRAP_CODE_ASSIST = None
    logger.info("Antigravity interceptor uninstalled")
    return True
  except Exception as e:
    logger.warning("Failed to uninstall interceptor: %s", e)
    return False
