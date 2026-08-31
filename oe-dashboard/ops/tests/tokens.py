"""Throwaway RS256 keys + token minting for the authorization tests.

The lab's real pinned JWK never enters the test environment: each run generates
its own keypair, publishes the public half in the JWK shape PGRST_JWT_SECRET
uses, and signs tokens shaped exactly like Keycloak 24's access tokens.
"""
import json
import time
from functools import lru_cache

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

REALM = 'shopmock'
ISSUER = f'http://localhost/auth/realms/{REALM}'
CLIENT_ID = 'it-operations'
SUBJECT = '5f9a7c2e-0000-4a1b-9f3d-it0ps0000001'


@lru_cache(maxsize=4)
def _keypair(tag='primary'):
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def private_key(tag='primary'):
    return _keypair(tag)


def public_jwk(tag='primary'):
    """The public key as the JSON string PGRST_JWT_SECRET holds."""
    data = json.loads(RSAAlgorithm.to_jwk(_keypair(tag).public_key()))
    data.update({'alg': 'RS256', 'use': 'sig', 'kid': f'test-{tag}'})
    return json.dumps(data)


def claims_for(roles=('it-ops',), **overrides):
    now = int(time.time())
    claims = {
        'exp': now + 300,
        'iat': now,
        'jti': 'a2c0f1e4-0000-0000-0000-000000000001',
        'iss': ISSUER,
        'aud': 'account',
        'sub': SUBJECT,
        'typ': 'Bearer',
        'azp': CLIENT_ID,
        'realm_access': {'roles': list(roles)},
        'resource_access': {'account': {'roles': ['view-profile']}},
        'scope': 'openid profile email',
        'preferred_username': 'it.ops',
        'name': 'IT Operations',
    }
    for key, value in overrides.items():
        if value is None:
            claims.pop(key, None)
        else:
            claims[key] = value
    return claims


def make_token(roles=('it-ops',), *, key_tag='primary', headers=None, **overrides):
    return jwt.encode(
        claims_for(roles, **overrides),
        private_key(key_tag),
        algorithm='RS256',
        headers=headers or {'kid': f'test-{key_tag}'},
    )


def bearer(*args, **kwargs):
    return f'Bearer {make_token(*args, **kwargs)}'
