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
    header_style = "gemini-cli" if config.cli_first else "antigravity"

    if header_style == "gemini-cli":
        logger.warning(
            "Gemini CLI header style is DEPRECATED — Gemini CLI sunsets 2026-06-18. "
            "Set cli_first: false in config to use the Antigravity header style."
        )

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

    logger.debug("Antigravity headers injected for model=%s", model)


def _antigravity_response_hook(response: httpx.Response) -> None:
    from .config import get_config
    config = get_config()

    if response.status_code == 401 and config.proactive_token_refresh:
        try:
            from .token import refresh_access_token
            from .storage import load_accounts
            from .cli import sync_token_to_google_oauth
            d = load_accounts()
            accs = d.get("accounts", [])
            idx = d.get("activeIndex", 0)
            if 0 <= idx < len(accs):
                a = accs[idx]
                r = refresh_access_token({"refresh": a.get("refreshToken", "")})
                if r.get("access"):
                    sync_token_to_google_oauth(
                        access_token=r["access"], refresh_token=a.get("refreshToken", ""),
                        project_id=a.get("projectId", ""), email=a.get("email"),
                        expires_ms=r.get("expires"),
                    )
        except Exception as e:
            logger.warning("Token refresh failed: %s", e)

    if response.status_code == 403:
        try:
            from .accounts.manager import get_or_create_global_manager
            mgr = get_or_create_global_manager()
            active = mgr.get_current_account_for_family("gemini")
            if active:
                import time
                active.cooling_down_until = (time.time() + 86400) * 1000
                active.cooldown_reason = "auth-failure"
                mgr.save_to_disk()
                next_acc = mgr.get_current_or_next_for_family("gemini", strategy="hybrid")
                if next_acc is None:
                    logger.warning("All gemini accounts exhausted — cannot rotate after 403")
                elif next_acc.index != active.index:
                    from .token import refresh_access_token
                    from .cli import sync_token_to_google_oauth
                    r = refresh_access_token({"refresh": next_acc.refresh_parts.refresh_token})
                    if r.get("access"):
                        sync_token_to_google_oauth(
                            access_token=r["access"], refresh_token=next_acc.refresh_parts.refresh_token,
                            project_id=next_acc.refresh_parts.project_id or "", email=next_acc.email,
                            expires_ms=r.get("expires"),
                        )
                        logger.info("Rotated to %s after 403", next_acc.email)
        except Exception as e:
            logger.warning("403 handler error: %s", e)

    if response.status_code == 429 and config.switch_on_first_rate_limit:
        try:
            from .accounts.manager import get_or_create_global_manager
            from .accounts.ratelimit import mark_rate_limited
            mgr = get_or_create_global_manager()
            active = mgr.get_current_account_for_family("gemini")
            if active:
                retry = config.default_retry_after_seconds
                rh = response.headers.get("Retry-After") or response.headers.get("retry-after")
                if rh:
                    try: retry = int(rh)
                    except ValueError: pass
                mark_rate_limited(active, float(retry * 1000), "gemini", "antigravity")
                mark_rate_limited(active, float(retry * 1000), "gemini", "gemini-cli")
                mgr.save_to_disk()
                next_acc = mgr.get_current_or_next_for_family("gemini", strategy="hybrid")
                if next_acc is None:
                    logger.warning("All gemini accounts exhausted — cannot rotate after rate limit")
                elif next_acc.index != active.index:
                    from .token import refresh_access_token
                    from .cli import sync_token_to_google_oauth
                    r = refresh_access_token({"refresh": next_acc.refresh_parts.refresh_token})
                    if r.get("access"):
                        sync_token_to_google_oauth(
                            access_token=r["access"], refresh_token=next_acc.refresh_parts.refresh_token,
                            project_id=next_acc.refresh_parts.project_id or "", email=next_acc.email,
                            expires_ms=r.get("expires"),
                        )
                        logger.info("Rotated to %s after rate limit", next_acc.email)
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
