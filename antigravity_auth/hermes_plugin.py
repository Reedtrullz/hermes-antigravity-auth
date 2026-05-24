"""Hermes entry point for the Antigravity CLI plugin."""

from __future__ import annotations

from .cli import handle_cli, setup_cli


def register(ctx):
  """Register Hermes CLI commands when loaded via entry points."""
  ctx.register_cli_command(
    name="antigravity",
    help="Google Antigravity utilities",
    setup_fn=setup_cli,
    handler_fn=handle_cli,
  )

  # Activate the HTTP interceptor so all google-gemini-cli requests
  # route through Antigravity's transform pipeline.
  try:
    from .interceptor import install as install_interceptor
    install_interceptor()
  except Exception:
    pass  # non-fatal — plugin still works for CLI commands

  # Initialize shared AccountManager so interceptor hooks share state
  try:
    from .accounts.shared import get_or_create_global_manager
    get_or_create_global_manager()
  except Exception:
    pass

  # Register pre_api_request hook for session recovery
  try:
    import logging
    _recovery_logger = logging.getLogger(__name__)

    from .recovery import is_recoverable_error, detect_error_type
    from .recovery import get_recovery_toast_content

    def _on_pre_api_request(**kwargs):
      error = kwargs.get("error")
      if error and is_recoverable_error(error):
        error_type = detect_error_type(error)
        toast = get_recovery_toast_content(error_type)
        _recovery_logger.info("Recovery needed: %s", error_type)
        return {
          "recovery_needed": True,
          "error_type": error_type,
          "toast": toast,
        }
      return None

    ctx.register_hook("pre_api_request", _on_pre_api_request)
  except Exception:
    pass

  # Register Antigravity tools (search, etc.)
  try:
    from .tools import register_tools
    register_tools()
  except Exception:
    pass

  # Start background token refresh watchdog
  try:
    from .token_watchdog import start_watchdog
    start_watchdog()
  except Exception:
    pass

  # Start background version check (non-blocking, cached to once per day)
  try:
    from .version import start_version_check
    start_version_check()
  except Exception:
    pass
