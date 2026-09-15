"""Strict storefront access-token validation."""
import json
import os
from functools import lru_cache

import jwt
from jwt import PyJWK

class AuthError(Exception):
    status = 401


class ForbiddenError(AuthError):
    status = 403


@lru_cache(maxsize=4)
def _verification_key(jwk_json):
    try:
        return PyJWK.from_dict(json.loads(jwk_json)).key
    except Exception as exc:
        raise AuthError(f'token verification key is unusable: {exc}')


def require_customer(request):
    header = request.headers.get('Authorization') or ''
    scheme, _, token = header.partition(' ')
    if scheme.lower() != 'bearer' or not token.strip():
        raise AuthError('missing bearer token')
    jwk = os.environ.get('CIAM_JWT_JWK') or os.environ.get('PGRST_JWT_SECRET')
    if not jwk:
        raise AuthError('token verification key is not configured')
    try:
        claims = jwt.decode(
            token.strip(), _verification_key(jwk), algorithms=['RS256'],
            options={'verify_aud': False, 'require': ['exp', 'iat', 'iss', 'sub']},
        )
    except jwt.PyJWTError as exc:
        raise AuthError(f'invalid token: {exc}')
    if claims.get('iss') != os.environ.get('CIAM_ISSUER', 'http://localhost/auth/realms/shopmock-ciam'):
        raise AuthError('token issuer is not trusted by this service')
    if claims.get('typ') != 'Bearer':
        raise AuthError('not an access token')
    if claims.get('azp') != 'storefront':
        raise AuthError('token was not issued to the storefront client')
    access = claims.get('resource_access')
    client = access.get('storefront') if isinstance(access, dict) else None
    roles = client.get('roles') if isinstance(client, dict) else None
    if not isinstance(roles, list) or 'customer' not in roles:
        raise ForbiddenError('storefront customer client role required')
    return claims
