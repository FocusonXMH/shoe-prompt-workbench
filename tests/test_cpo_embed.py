import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock,patch
from fastapi.testclient import TestClient
from app import app

class EmbedTests(unittest.TestCase):
    def test_only_explicit_verified_bearer_can_start_embed(self):
        client=TestClient(app)
        for header in ('','Bearer shoe-local.fixture'):
            self.assertEqual(client.post('/auth/from-cpo',headers={'Authorization':header}).status_code,401)
    def test_embed_cookie_does_not_replace_standalone_session(self):
        client=TestClient(app);client.cookies.set('shoe_session','standalone-fixture')
        actor=SimpleNamespace(user_id='123')
        with patch('app.current_user',new=AsyncMock(return_value=(actor,None))):
            result=client.post('/auth/from-cpo',headers={'Authorization':'Bearer verified-fixture'})
        self.assertEqual(result.status_code,200)
        self.assertIn('Path=/shoe-embed-api/',result.headers['set-cookie'])
        self.assertIn('HttpOnly',result.headers['set-cookie'])
        self.assertEqual(client.cookies.get('shoe_session'),'standalone-fixture')
        self.assertEqual(client.cookies.get('shoe_embed_session'),'verified-fixture')
    def test_embedded_account_switch_and_foreign_origins_rejected(self):
        client=TestClient(app)
        self.assertEqual(client.post('/embedded/auth/login',json={}).status_code,409)
        self.assertEqual(client.post('/embedded/auth/accounts').status_code,409)
        self.assertEqual(client.post('/auth/from-cpo',headers={'Origin':'https://foreign.invalid','Authorization':'Bearer fixture'}).status_code,403)
