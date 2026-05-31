"""Hermes entry point for the Antigravity CLI plugin."""

from __future__ import annotations

import logging

from .cli import handle_cli, setup_cli
from .config import get_config
from .debug import initialize_debug


def register(ctx):
  """Register Hermes CLI commands when loaded via entry points."""
  ctx.register_cli_command(
    name="antigravity",
    help="Google Antigravity utilities",
    setup_fn=setup_cli,
    handler_fn=handle_cli,
  )

  logger = logging.getLogger(__name__)
  try:
    config = get_config()
    initialize_debug(config.debug, config.debug_tui, config.log_dir)
  except Exception as e:
    logger.warning("Antigravity debug logging initialization failed: %s", e)

  # Activate the HTTP interceptor so all google-gemini-cli requests
  # route through Antigravity's transform pipeline.
  try:
    from .interceptor import install as install_interceptor
    from .interceptor import is_installed as interceptor_is_installed
    if interceptor_is_installed():
      logger.debug("Antigravity interceptor already installed (by provider plugin)")
    else:
      installed = install_interceptor()
      if installed:
        logger.info("Antigravity interceptor installed (by CLI plugin)")
      else:
        logger.warning(
          "Antigravity interceptor could not be installed; "
          "plugin loaded without HTTP interception"
        )
  except Exception as e:
    logger.warning("Antigravity interceptor install failed: %s", e)

  # Initialize shared AccountManager so interceptor hooks share state
  try:
    from .accounts.shared import get_or_create_global_manager
    get_or_create_global_manager()
  except Exception as e:
    logger.warning("Antigravity account manager initialization failed: %s", e)

  # Register pre_api_request hook for session recovery
  try:
    from .recovery import is_recoverable_error, detect_error_type
    from .recovery import get_recovery_toast_content

    def _on_pre_api_request(**kwargs):
      error = kwargs.get("error")
      if error and is_recoverable_error(error):
        error_type = detect_error_type(error)
        toast = get_recovery_toast_content(error_type)
        logger.info("Recovery needed: %s", error_type)
        return {
          "recovery_needed": True,
          "error_type": error_type,
          "toast": toast,
        }
      return None

    ctx.register_hook("pre_api_request", _on_pre_api_request)
  except Exception as e:
    logger.warning("Antigravity recovery hook registration failed: %s", e)

  # Register Antigravity tools (search, etc.)
  try:
    from .tools import register_tools
    register_tools()
  except Exception as e:
    logger.warning("Antigravity tools registration failed: %s", e)

  # Start background token refresh watchdog
  try:
    from .token_watchdog import start_watchdog
    start_watchdog()
  except Exception as e:
    logger.warning("Antigravity token watchdog startup failed: %s", e)

  # Start background version check (non-blocking, cached to once per day)
  try:
    from .version import start_version_check
    start_version_check()
  except Exception as e:
    logger.debug("Antigravity version check startup failed: %s", e)
