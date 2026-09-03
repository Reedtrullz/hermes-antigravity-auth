"""Regression tests: Hermes 0.21 model-picker overlay registration.

Hermes 0.21 enumerates ``/model`` picker providers from
``hermes_cli.providers.HERMES_OVERLAYS``. The plugin must register the
``google-gemini-cli`` row there (feature-detected: older Hermes builds
without that table must not crash).
"""

import sys
import types
import unittest
from unittest.mock import patch

from antigravity_auth.hermes_provider_plugin import _register_picker_overlay


def _install_providers_stub(table=True, overlay_cls=True):
  """Inject a fake hermes_cli.providers module; return saved state."""
  saved = {
    name: sys.modules[name]
    for name in ("hermes_cli", "hermes_cli.providers")
    if name in sys.modules
  }
  pkg = types.ModuleType("hermes_cli")
  pkg.__path__ = []
  mod = types.ModuleType("hermes_cli.providers")
  if table:
    setattr(mod, "HERMES_OVERLAYS", {})
  if overlay_cls:
    from dataclasses import dataclass

    @dataclass
    class HermesOverlay:
      transport: str = "openai_chat"
      is_aggregator: bool = False
      auth_type: str = "api_key"
      extra_env_vars: tuple = ()
      base_url_override: str = ""
      base_url_env_var: str = ""
      keyless: bool = False

    setattr(mod, "HermesOverlay", HermesOverlay)
  sys.modules["hermes_cli"] = pkg
  sys.modules["hermes_cli.providers"] = mod
  return saved, mod


def _restore_providers_stub(saved):
  for name in ("hermes_cli", "hermes_cli.providers"):
    sys.modules.pop(name, None)
  sys.modules.update(saved)


class TestPickerOverlay(unittest.TestCase):
  """HERMES_OVERLAYS registration with feature detection."""

  def tearDown(self) -> None:
    _restore_providers_stub({})

  def test_registers_overlay_when_table_present(self) -> None:
    saved, mod = _install_providers_stub()
    try:
      self.assertTrue(_register_picker_overlay())
      entry = mod.HERMES_OVERLAYS.get("google-gemini-cli")
      self.assertIsNotNone(entry)
      self.assertEqual(entry.auth_type, "oauth_external")
      self.assertEqual(entry.base_url_override, "cloudcode-pa://google")
    finally:
      _restore_providers_stub(saved)

  def test_registration_is_idempotent(self) -> None:
    saved, mod = _install_providers_stub()
    try:
      self.assertTrue(_register_picker_overlay())
      first = mod.HERMES_OVERLAYS["google-gemini-cli"]
      self.assertTrue(_register_picker_overlay())
      self.assertIs(mod.HERMES_OVERLAYS["google-gemini-cli"], first)
    finally:
      _restore_providers_stub(saved)

  def test_old_hermes_without_table_does_not_crash(self) -> None:
    saved, _ = _install_providers_stub(table=False)
    try:
      self.assertFalse(_register_picker_overlay())
    finally:
      _restore_providers_stub(saved)

  def test_old_hermes_without_overlay_class_does_not_crash(self) -> None:
    saved, _ = _install_providers_stub(overlay_cls=False)
    try:
      self.assertFalse(_register_picker_overlay())
    finally:
      _restore_providers_stub(saved)

  def test_no_hermes_at_all_does_not_crash(self) -> None:
    saved = {
      name: sys.modules[name]
      for name in ("hermes_cli", "hermes_cli.providers")
      if name in sys.modules
    }
    for name in ("hermes_cli", "hermes_cli.providers"):
      sys.modules.pop(name, None)
    try:
      with patch.dict(sys.modules, {}):
        sys.modules.pop("hermes_cli", None)
        sys.modules.pop("hermes_cli.providers", None)
        self.assertFalse(_register_picker_overlay())
    finally:
      _restore_providers_stub(saved)
