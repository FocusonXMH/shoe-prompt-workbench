import asyncio
import unittest
from identity import CpoIdentityProvider


class FakeResponse:
    def __init__(self, status, body):
        self.status_code, self.body = status, body
    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError('identity service failed')
    def json(self):
        return self.body


class FakeClient:
    def __init__(self, response):
        self.response = response
        self.calls = []
    async def get(self, url, headers):
        self.calls.append((url, headers))
        return self.response
    async def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.response


class IdentityTests(unittest.TestCase):
    def test_login_uses_identity_service(self):
        client = FakeClient(FakeResponse(200, {'accessToken': 'token', 'user': {'userId': 42}}))
        result = asyncio.run(CpoIdentityProvider(client).login('test', 'test-password'))
        self.assertEqual(result['accessToken'], 'token')
        self.assertEqual(client.calls[0][0], '/api/auth/login')

    def test_login_rejects_invalid_credentials(self):
        client = FakeClient(FakeResponse(401, {}))
        self.assertIsNone(asyncio.run(CpoIdentityProvider(client).login('test', 'bad')))

    def test_logout_revokes_service_session(self):
        client = FakeClient(FakeResponse(200, {'ok': True}))
        asyncio.run(CpoIdentityProvider(client).logout('token'))
        self.assertEqual(client.calls[0][0], '/api/auth/logout')
    def test_resolves_verified_user(self):
        client = FakeClient(FakeResponse(200, {'user': {'userId': 42, 'username': 'test', 'roleCode': 'operator'}}))
        principal = asyncio.run(CpoIdentityProvider(client).resolve('sample-token'))
        self.assertEqual(principal.user_id, '42')
        self.assertEqual(principal.role, 'operator')
        self.assertEqual(client.calls[0][0], '/api/auth/me')

    def test_rejects_unauthorized_and_empty_token(self):
        client = FakeClient(FakeResponse(401, {}))
        provider = CpoIdentityProvider(client)
        self.assertIsNone(asyncio.run(provider.resolve('invalid')))
        self.assertIsNone(asyncio.run(provider.resolve('')))
        self.assertEqual(len(client.calls), 1)

    def test_rejects_missing_stable_id(self):
        client = FakeClient(FakeResponse(200, {'user': {'username': 'test'}}))
        with self.assertRaises(ValueError):
            asyncio.run(CpoIdentityProvider(client).resolve('sample-token'))

if __name__ == '__main__':
    unittest.main()
