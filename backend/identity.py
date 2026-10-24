"""Identity boundary for the workbench; CPO remains the identity authority.

This module intentionally does not store passwords or issue its own bearer token.
It can be replaced by another provider without coupling template data to DingTalk.
"""
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class Principal:
    user_id: str
    username: str
    display_name: str
    role: str


class IdentityProvider(Protocol):
    async def resolve(self, token: str) -> Principal | None: ...


class CpoIdentityProvider:
    def __init__(self, client):
        self.client = client

    async def login(self, username: str, password: str) -> dict | None:
        # The caller must send credentials over TLS and never log this payload.
        response = await self.client.post('/api/auth/login', json={'username': username, 'password': password})
        if response.status_code in (401, 403):
            return None
        response.raise_for_status()
        result = response.json()
        if not result.get('accessToken') or not result.get('user', {}).get('userId'):
            raise ValueError('Identity service returned an invalid login response')
        return result

    async def logout(self, token: str) -> None:
        if not token:
            return
        response = await self.client.post('/api/auth/logout', headers={'Authorization': f'Bearer {token}'})
        if response.status_code not in (401, 403):
            response.raise_for_status()

    async def resolve(self, token: str) -> Principal | None:
        if not token:
            return None
        response = await self.client.get('/api/auth/me', headers={'Authorization': f'Bearer {token}'})
        if response.status_code in (401, 403):
            return None
        response.raise_for_status()
        user = response.json()['user']
        user_id = user.get('userId')
        if not user_id:
            raise ValueError('Identity service returned no stable user ID')
        return Principal(str(user_id), str(user.get('username') or ''),
                         str(user.get('displayName') or ''), str(user.get('roleCode') or ''))
