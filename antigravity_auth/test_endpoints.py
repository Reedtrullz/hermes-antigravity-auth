import unittest

from antigravity_auth.endpoints import (
    EndpointProvider,
    select_endpoint,
    mark_endpoint_failed,
    reset_endpoint_failures,
)
from antigravity_auth.constants import (
    ANTIGRAVITY_ENDPOINT_PROD,
    ANTIGRAVITY_ENDPOINT_DAILY,
    ANTIGRAVITY_ENDPOINT_AUTOPUSH,
)


class TestEndpointProvider(unittest.TestCase):
    """Tests for the EndpointProvider fallback chain and failure tracking."""

    def setUp(self):
        self.provider = EndpointProvider()

    # -- get_endpoints -------------------------------------------------------

    def test_get_endpoints_antigravity_style_returns_all_three_in_fallback_order(self):
        endpoints = self.provider.get_endpoints(header_style="antigravity")
        self.assertEqual(len(endpoints), 3)
        self.assertEqual(endpoints[0], ANTIGRAVITY_ENDPOINT_DAILY)
        self.assertEqual(endpoints[1], ANTIGRAVITY_ENDPOINT_AUTOPUSH)
        self.assertEqual(endpoints[2], ANTIGRAVITY_ENDPOINT_PROD)

    def test_get_endpoints_gemini_cli_style_returns_only_prod(self):
        endpoints = self.provider.get_endpoints(header_style="gemini-cli")
        self.assertEqual(endpoints, [ANTIGRAVITY_ENDPOINT_PROD])

    # -- failure tracking ----------------------------------------------------

    def test_mark_failed_then_is_failed_returns_true(self):
        self.assertFalse(self.provider.is_failed(ANTIGRAVITY_ENDPOINT_DAILY))
        self.provider.mark_failed(ANTIGRAVITY_ENDPOINT_DAILY)
        self.assertTrue(self.provider.is_failed(ANTIGRAVITY_ENDPOINT_DAILY))

    def test_reset_clears_all_failures(self):
        self.provider.mark_failed(ANTIGRAVITY_ENDPOINT_DAILY)
        self.provider.mark_failed(ANTIGRAVITY_ENDPOINT_PROD)
        self.provider.reset()
        self.assertFalse(self.provider.is_failed(ANTIGRAVITY_ENDPOINT_DAILY))
        self.assertFalse(self.provider.is_failed(ANTIGRAVITY_ENDPOINT_PROD))

    def test_failed_endpoints_returns_copy(self):
        self.provider.mark_failed(ANTIGRAVITY_ENDPOINT_DAILY)
        copy1 = self.provider.failed_endpoints
        copy2 = self.provider.failed_endpoints
        self.assertIsNot(copy1, copy2)
        # Mutating the copy must not affect the internal set
        copy1.add(ANTIGRAVITY_ENDPOINT_PROD)
        self.assertFalse(self.provider.is_failed(ANTIGRAVITY_ENDPOINT_PROD))


class TestSelectEndpoint(unittest.TestCase):
    """Tests for the module-level select_endpoint function."""

    def test_select_endpoint_returns_prod(self):
        self.assertEqual(select_endpoint(), ANTIGRAVITY_ENDPOINT_PROD)

    def test_select_endpoint_with_config_returns_prod(self):
        # Config is accepted but not yet consulted — current behavior is PROD
        self.assertEqual(select_endpoint(config=None), ANTIGRAVITY_ENDPOINT_PROD)


class TestModuleLevelFunctions(unittest.TestCase):
    """Tests for the module-level mark/reset helpers (smoke tests)."""

    def tearDown(self):
        reset_endpoint_failures()

    def test_mark_endpoint_failed_does_not_crash(self):
        mark_endpoint_failed(ANTIGRAVITY_ENDPOINT_DAILY)

    def test_reset_endpoint_failures_does_not_crash(self):
        reset_endpoint_failures()

    def test_mark_and_reset_sequence_does_not_crash(self):
        mark_endpoint_failed(ANTIGRAVITY_ENDPOINT_AUTOPUSH)
        mark_endpoint_failed(ANTIGRAVITY_ENDPOINT_PROD)
        reset_endpoint_failures()
