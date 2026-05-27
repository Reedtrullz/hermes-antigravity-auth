import unittest
from unittest.mock import patch, Mock
import json
import base64

try:
    from .oauth import (
        generate_pkce,
        encode_state,
        decode_state,
        authorize_antigravity,
        fetch_project_id,
        exchange_antigravity,
        get_pkce_verifier,
        _pkce_verifier_store,
        _decompress,
        make_post_request,
        make_get_request,
    )
except ImportError:
    from oauth import (
        generate_pkce,
        encode_state,
        decode_state,
        authorize_antigravity,
        fetch_project_id,
        exchange_antigravity,
        get_pkce_verifier,
        _pkce_verifier_store,
        _decompress,
        make_post_request,
        make_get_request,
    )


class TestOAuth(unittest.TestCase):

    def setUp(self):
        # Clear the PKCE verifier store between tests to prevent cross-test pollution
        _pkce_verifier_store.clear()

    def test_generate_pkce(self):
        pkce = generate_pkce()
        self.assertIn('challenge', pkce)
        self.assertIn('verifier', pkce)
        self.assertIsInstance(pkce['challenge'], str)
        self.assertIsInstance(pkce['verifier'], str)
        # Challenge should be a base64url encoded SHA256 of the verifier
        import hashlib
        expected_challenge = base64.urlsafe_b64encode(
            hashlib.sha256(pkce['verifier'].encode('utf-8')).digest()
        ).decode('utf-8').rstrip('=')
        self.assertEqual(pkce['challenge'], expected_challenge)

    def test_encode_state(self):
        payload = {'verifier': 'test_verifier', 'projectId': 'test_project'}
        state = encode_state(payload)
        self.assertIsInstance(state, str)
        # Decode to check
        decoded = base64.urlsafe_b64decode(state + '=' * (4 - len(state) % 4))
        self.assertEqual(json.loads(decoded), payload)

    def test_decode_state(self):
        state = encode_state({'verifier': 'test_verifier', 'projectId': 'test_project'})
        decoded = decode_state(state)
        self.assertEqual(decoded['verifier'], 'test_verifier')
        self.assertEqual(decoded['projectId'], 'test_project')

    def test_decode_state_missing_verifier(self):
        state = encode_state({'projectId': 'test_project'})
        decoded = decode_state(state)
        self.assertEqual(decoded['projectId'], 'test_project')
        self.assertNotIn('verifier', decoded)

    def test_authorize_antigravity(self):
        # Patch the credential loading at the constants level since
        # authorize_antigravity() now uses require_credentials()
        with patch('antigravity_auth.constants._credentials_valid', True), \
             patch('antigravity_auth.constants.ANTIGRAVITY_CLIENT_ID', 'test_client_id'), \
             patch('antigravity_auth.constants.ANTIGRAVITY_CLIENT_SECRET', 'test_client_secret'), \
             patch('antigravity_auth.oauth.ANTIGRAVITY_REDIRECT_URI', 'http://localhost:51121/oauth-callback'), \
             patch('antigravity_auth.oauth.ANTIGRAVITY_SCOPES', ['scope1', 'scope2']):
            result = authorize_antigravity(project_id='test_project')
            self.assertIn('url', result)
            self.assertIn('verifier', result)
            self.assertIn('projectId', result)
            self.assertEqual(result['projectId'], 'test_project')
            self.assertEqual(result['project_id'], 'test_project')
            # Check that the URL contains the expected parameters
            self.assertIn('client_id=test_client_id', result['url'])
            self.assertIn('response_type=code', result['url'])
            self.assertIn('redirect_uri=http%3A%2F%2Flocalhost%3A51121%2Foauth-callback', result['url'])
            self.assertIn('scope=scope1+scope2', result['url'])
            self.assertIn('code_challenge=', result['url'])
            self.assertIn('code_challenge_method=S256', result['url'])
            # state should be encoded
            self.assertIn('state=', result['url'])

    def test_authorize_returns_state_for_manual_code_only_flow(self):
        with patch('antigravity_auth.constants._credentials_valid', True), \
             patch('antigravity_auth.constants.ANTIGRAVITY_CLIENT_ID', 'test_client_id'), \
             patch('antigravity_auth.constants.ANTIGRAVITY_CLIENT_SECRET', 'test_client_secret'), \
             patch('antigravity_auth.oauth.ANTIGRAVITY_REDIRECT_URI', 'http://localhost:51121/oauth-callback'), \
             patch('antigravity_auth.oauth.ANTIGRAVITY_SCOPES', ['scope1', 'scope2']):
            data = authorize_antigravity(project_id="proj")
        self.assertIn("state", data)
        self.assertTrue(data["state"])
        decoded = decode_state(data["state"])
        self.assertIn("id", decoded)

    def test_get_pkce_verifier_expires_old_entries(self):
        _pkce_verifier_store["old"] = {"verifier": "v", "projectId": "p", "createdAt": "0"}
        with patch("antigravity_auth.oauth.time.time", return_value=999999):
            self.assertIsNone(get_pkce_verifier("old"))
        self.assertNotIn("old", _pkce_verifier_store)

    def test_authorize_stores_created_at_with_current_time(self):
        with patch('antigravity_auth.constants._credentials_valid', True), \
             patch('antigravity_auth.constants.ANTIGRAVITY_CLIENT_ID', 'test_client_id'), \
             patch('antigravity_auth.constants.ANTIGRAVITY_CLIENT_SECRET', 'test_client_secret'), \
             patch('antigravity_auth.oauth.ANTIGRAVITY_REDIRECT_URI', 'http://localhost:51121/oauth-callback'), \
             patch('antigravity_auth.oauth.ANTIGRAVITY_SCOPES', ['scope1', 'scope2']), \
             patch('antigravity_auth.oauth.time.time', return_value=123.0):
            data = authorize_antigravity(project_id="proj")
        decoded = decode_state(data["state"])
        self.assertEqual(_pkce_verifier_store[decoded["id"]]["createdAt"], "123.0")

    @patch('antigravity_auth.oauth.make_post_request')
    def test_fetch_project_id_success(self, mock_make_post):
        # Mock a successful response
        mock_make_post.return_value = (200, b'{"cloudaicompanionProject": {"id": "test_project_id"}}')
        result = fetch_project_id('fake_token')
        self.assertEqual(result, 'test_project_id')
        # Ensure make_post_request was called
        self.assertTrue(mock_make_post.called)

    @patch('antigravity_auth.oauth.make_post_request')
    def test_fetch_project_id_fallback(self, mock_make_post):
        # First endpoint fails, second succeeds
        mock_make_post.side_effect = [
            (400, b'error'),
            (200, b'{"cloudaicompanionProject": "direct_project_id"}')
        ]
        result = fetch_project_id('fake_token')
        self.assertEqual(result, 'direct_project_id')
        self.assertEqual(mock_make_post.call_count, 2)

    @patch('antigravity_auth.oauth.make_post_request')
    def test_fetch_project_id_all_fail(self, mock_make_post):
        mock_make_post.return_value = (400, b'error')
        result = fetch_project_id('fake_token')
        self.assertEqual(result, '')

    @patch('antigravity_auth.oauth.make_post_request')
    @patch('antigravity_auth.oauth.make_get_request')
    def test_exchange_antigravity_success(self, mock_make_get, mock_make_post):
        # Mock token exchange
        mock_make_post.return_value = (200, b'{"access_token": "access_token", "refresh_token": "refresh_token", "expires_in": 3600}')
        # Mock user info
        mock_make_get.return_value = (200, b'{"email": "test@example.com"}')
        # Mock fetch_project_id to return a project ID
        with patch('antigravity_auth.oauth.fetch_project_id', return_value='test_project'):
            # Populate the PKCE verifier store with a known state_id
            state_id = "test_state_id"
            _pkce_verifier_store[state_id] = {"verifier": "test_verifier", "projectId": "test_project"}
            state = encode_state({"id": state_id})
            result = exchange_antigravity('fake_code', state)
            self.assertEqual(result['type'], 'success')
            self.assertEqual(result['access'], 'access_token')
            self.assertEqual(result['refresh'], 'refresh_token|test_project')  # stored format
            self.assertEqual(result['email'], 'test@example.com')
            self.assertEqual(result['projectId'], 'test_project')
            self.assertEqual(result['project_id'], 'test_project')
            self.assertIn('expires', result)
            # Verify the verifier was consumed
            self.assertIsNone(get_pkce_verifier(state_id))

    @patch('antigravity_auth.oauth.make_post_request')
    @patch('antigravity_auth.oauth.make_get_request')
    def test_exchange_antigravity_token_headers_do_not_request_brotli(self, mock_make_get, mock_make_post):
        mock_make_post.return_value = (200, b'{"access_token": "access_token", "refresh_token": "refresh_token", "expires_in": 3600}')
        mock_make_get.return_value = (200, b'{"email": "test@example.com"}')
        state_id = "test_state_id_headers"
        _pkce_verifier_store[state_id] = {"verifier": "test_verifier", "projectId": "test_project"}
        state = encode_state({"id": state_id})
        result = exchange_antigravity('fake_code', state)
        self.assertEqual(result['type'], 'success')
        token_headers = mock_make_post.call_args_list[0][0][1]
        self.assertEqual(token_headers["Accept-Encoding"], "gzip, deflate")
        self.assertNotIn("br", token_headers["Accept-Encoding"])

    @patch('antigravity_auth.oauth.make_post_request')
    def test_exchange_antigravity_token_failure(self, mock_make_post):
        mock_make_post.return_value = (400, b'{"error": "invalid_grant"}')
        result = exchange_antigravity('fake_code', 'fake_state')
        self.assertEqual(result['type'], 'failed')
        self.assertIn('error', result)

    @patch('antigravity_auth.oauth.make_post_request')
    def test_exchange_antigravity_missing_access_token(self, mock_make_post):
        # Populate the PKCE verifier store
        state_id = "test_state_id_missing_access"
        _pkce_verifier_store[state_id] = {"verifier": "test_verifier", "projectId": "test_project"}
        state = encode_state({"id": state_id})
        mock_make_post.return_value = (200, b'{"refresh_token": "refresh_token"}')
        result = exchange_antigravity('fake_code', state)
        self.assertEqual(result['type'], 'failed')
        self.assertEqual(result['error'], 'Missing access token in response')

    @patch('antigravity_auth.oauth.make_post_request')
    def test_exchange_antigravity_missing_refresh_token(self, mock_make_post):
        # Populate the PKCE verifier store
        state_id = "test_state_id_missing_refresh"
        _pkce_verifier_store[state_id] = {"verifier": "test_verifier", "projectId": "test_project"}
        state = encode_state({"id": state_id})
        mock_make_post.return_value = (200, b'{"access_token": "access_token"}')
        result = exchange_antigravity('fake_code', state)
        self.assertEqual(result['type'], 'failed')
        self.assertEqual(result['error'], 'Missing refresh token in response')

    def test_decompress(self):
        # Test that _decompress returns the same data if not gzipped
        data = b'test data'
        class MockResponse:
            def __init__(self):
                self.headers = {}
        resp = MockResponse()
        self.assertEqual(_decompress(data, resp), data)
        # Test with gzip encoding
        import gzip
        gzipped_data = gzip.compress(data)
        resp.headers = {'Content-Encoding': 'gzip'}
        self.assertEqual(_decompress(gzipped_data, resp), data)

    def test_make_post_request_success(self):
        with patch('antigravity_auth.oauth.urllib.request.urlopen') as mock_urlopen:
            mock_response = Mock()
            mock_response.status = 200
            mock_response.headers = {'Content-Encoding': ''}
            mock_response.read.return_value = b'response'
            mock_urlopen.return_value.__enter__.return_value = mock_response
            status, data = make_post_request('http://example.com', {}, b'data')
            self.assertEqual(status, 200)
            self.assertEqual(data, b'response')

    def test_make_post_request_http_error(self):
        with patch('antigravity_auth.oauth.urllib.request.urlopen') as mock_urlopen:
            mock_urlopen.side_effect = Exception('HTTP Error 400: Bad Request')
            # The function catches Exception and returns 500, str(e).encode()
            status, data = make_post_request('http://example.com', {}, b'data')
            self.assertEqual(status, 500)
            self.assertIn(b'HTTP Error 400', data)

    def test_make_get_request_success(self):
        with patch('antigravity_auth.oauth.urllib.request.urlopen') as mock_urlopen:
            mock_response = Mock()
            mock_response.status = 200
            mock_response.headers = {'Content-Encoding': ''}
            mock_response.read.return_value = b'response'
            mock_urlopen.return_value.__enter__.return_value = mock_response
            status, data = make_get_request('http://example.com', {})
            self.assertEqual(status, 200)
            self.assertEqual(data, b'response')


if __name__ == '__main__':
    unittest.main()