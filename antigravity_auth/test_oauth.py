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
        _decompress,
        make_post_request,
        make_get_request,
    )


class TestOAuth(unittest.TestCase):

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
        with self.assertRaises(ValueError) as cm:
            decode_state(state)
        self.assertIn('Missing PKCE verifier in state', str(cm.exception))

    def test_authorize_antigravity(self):
        # We need to set the credentials for the test
        # We'll mock the constants to avoid needing _credentials.py
        with patch('antigravity_auth.oauth.ANTIGRAVITY_CLIENT_ID', 'test_client_id'), \
             patch('antigravity_auth.oauth.ANTIGRAVITY_CLIENT_SECRET', 'test_client_secret'), \
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
            # Create a valid state to pass decode_state
            state = encode_state({"verifier": "test_verifier", "projectId": "test_project"})
            result = exchange_antigravity('fake_code', state)
            self.assertEqual(result['type'], 'success')
            self.assertEqual(result['access'], 'access_token')
            self.assertEqual(result['refresh'], 'refresh_token|test_project')  # stored format
            self.assertEqual(result['email'], 'test@example.com')
            self.assertEqual(result['projectId'], 'test_project')
            self.assertEqual(result['project_id'], 'test_project')
            self.assertIn('expires', result)

    @patch('antigravity_auth.oauth.make_post_request')
    def test_exchange_antigravity_token_failure(self, mock_make_post):
        mock_make_post.return_value = (400, b'{"error": "invalid_grant"}')
        result = exchange_antigravity('fake_code', 'fake_state')
        self.assertEqual(result['type'], 'failed')
        self.assertIn('error', result)

    @patch('antigravity_auth.oauth.make_post_request')
    def test_exchange_antigravity_missing_access_token(self, mock_make_post):
        # Create a valid state to pass decode_state
        state = encode_state({"verifier": "test_verifier", "projectId": "test_project"})
        mock_make_post.return_value = (200, b'{"refresh_token": "refresh_token"}')
        result = exchange_antigravity('fake_code', state)
        self.assertEqual(result['type'], 'failed')
        self.assertEqual(result['error'], 'Missing access token in response')

    @patch('antigravity_auth.oauth.make_post_request')
    def test_exchange_antigravity_missing_refresh_token(self, mock_make_post):
        # Create a valid state to pass decode_state
        state = encode_state({"verifier": "test_verifier", "projectId": "test_project"})
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