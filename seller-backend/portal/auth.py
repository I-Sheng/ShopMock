"""Keycloak token verification for sellers.

Uses the same pinned RS256 public JWK the PostgREST services and the
internal-service-backend verify against (PGRST_JWT_SECRET in .env), so the
trust model is identical across the stack: a token is valid iff it was signed
by the shopmock realm key and carries the `seller` role claim (stamped by the
`seller-dashboard` client's hardcoded-claim mapper). The caller's identity is
always taken from the verified `sub` claim — never from the request body.
"""
import json
import os

import jwt
from jwt import PyJWK

_signing_key = PyJWK.from_dict(json.loads(os.environ['PGRST_JWT_SECRET'])).key


class AuthError(Exception):
    pass


def require_seller(request):
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
    if claims.get('role') != 'seller':
        raise AuthError('seller role required')
    if not claims.get('sub'):
        raise AuthError('no subject (sub) claim in token')
    return claims
