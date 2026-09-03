"""Catalog tests: every picker model must resolve to a real backend model.

Models are registered only after a live call through the Antigravity
backend succeeded — never by name-guessing. Bare ``gemini-3.8-flash``
returned HTTP 404 and therefore stays excluded.
"""

import unittest

from antigravity_auth.hermes_provider_plugin import ANTIGRAVITY_MODELS
from antigravity_auth.transform.envelope import (
  GEMINI_37_FLASH_TIERED,
  resolve_model_for_header_style,
)


class TestFlashCatalog(unittest.TestCase):
  """Verified Gemini Flash IDs and their backend resolution."""

  def test_37_variants_registered(self) -> None:
    for model in (
      "gemini-3.7-flash",
      "gemini-3.7-flash-high",
      "gemini-3.7-flash-medium",
      "gemini-3.7-flash-low",
    ):
      self.assertIn(model, ANTIGRAVITY_MODELS)

  def test_38_tiered_variants_registered(self) -> None:
    for model in (
      "gemini-3.8-flash-high",
      "gemini-3.8-flash-medium",
      "gemini-3.8-flash-low",
    ):
      self.assertIn(model, ANTIGRAVITY_MODELS)

  def test_bare_38_excluded_after_live_404(self) -> None:
    self.assertNotIn("gemini-3.8-flash", ANTIGRAVITY_MODELS)

  def test_37_resolves_to_tiered_backend(self) -> None:
    for model in (
      "gemini-3.7-flash",
      "gemini-3.7-flash-high",
      "gemini-3.7-flash-medium",
      "gemini-3.7-flash-low",
    ):
      self.assertEqual(
        resolve_model_for_header_style(model, "antigravity"),
        GEMINI_37_FLASH_TIERED,
      )

  def test_38_resolves_to_backend_id(self) -> None:
    """3.8 tiered IDs pass through to identical backend IDs."""
    for model in (
      "gemini-3.8-flash-high",
      "gemini-3.8-flash-medium",
      "gemini-3.8-flash-low",
    ):
      self.assertEqual(resolve_model_for_header_style(model, "antigravity"), model)

  def test_no_duplicate_ids(self) -> None:
    self.assertEqual(len(ANTIGRAVITY_MODELS), len(set(ANTIGRAVITY_MODELS)))
