"""Keycloak token verification.

Uses the same pinned RS256 public JWK the PostgREST services verify against
(PGRST_JWT_SECRET in .env), so the trust model is identical across the stack:
a token is valid iff it was signed by the shopmock realm key and carries the
`customer` role claim. The caller's identity is always taken from the verified
`sub` claim — never from the request body.
"""
import json
import os

import jwt
from jwt import PyJWK

_signing_key = PyJWK.from_dict(json.loads(os.environ['PGRST_JWT_SECRET'])).key


class AuthError(Exception):
    pass


def require_customer(request):
    header = request.headers.get('Authorization', '')
    if not header.startswith('Bearer '):
        raise AuthError('missing bearer token')
    try:
        claims = jwt.decode(
            header[7:], _signing_key,
            algorithms=['RS256'],
            options={'verify_aud': False},
        )
    except jwt.PyJWTError as exc:
        raise AuthError(f'invalid token: {exc}')
    if claims.get('role') != 'customer':
        raise AuthError('customer role required')
    if not claims.get('sub'):
        raise AuthError('no subject (sub) claim in token')
    return claims
