"""Server-side authorization for the IT operations console.

Same trust anchor as the rest of the stack: the pinned RS256 *public* JWK from
`PGRST_JWT_SECRET`, which PostgREST and both Django backends already verify
against. What differs is how much of the token's context is checked, because
this console reports on the infrastructure itself:

  1. RS256 signature against the pinned realm key — `algorithms` is fixed, so
     an `alg: none` or HMAC-confusion token cannot be substituted.
  2. `exp`/`iat`/`iss`/`sub` must all be present.
  3. `iss` must be one of the origins the deploy registered for this realm.
  4. `typ` must be `Bearer` — an ID or refresh token is not an access token.
  5. The token must have been minted for the `it-operations` client. A valid
     Seller Central or storefront token is rejected even if it carries the role.
  6. Only then: the `it-ops` *realm* role. A client role of the same name does
     not count.

Steps 1-5 fail as 401 (the caller is not authenticated to this application);
step 6 fails as 403 (authenticated, but not IT operations).
"""
import json
from functools import lru_cache

import jwt
from django.conf import settings
from jwt import PyJWK


class AuthError(Exception):
    """The caller is not authenticated for this application."""

    status = 401


class ForbiddenError(AuthError):
    """The caller is authenticated but lacks the it-ops realm role."""

    status = 403


@lru_cache(maxsize=4)
def _verification_key(jwk_json):
    try:
        return PyJWK.from_dict(json.loads(jwk_json)).key
    except Exception as exc:  # malformed PGRST_JWT_SECRET — fail closed
        raise AuthError(f'token verification key is unusable: {exc}')


def _bearer_token(request):
    header = request.headers.get('Authorization') or ''
    scheme, _, token = header.partition(' ')
    if scheme.lower() != 'bearer' or not token.strip():
        raise AuthError('missing bearer token')
    return token.strip()


def _verified_claims(token):
    if not settings.OE_JWT_JWK:
        raise AuthError('token verification key is not configured')
    try:
        return jwt.decode(
            token, _verification_key(settings.OE_JWT_JWK),
            algorithms=['RS256'],
            options={
                'verify_aud': False,   # audience is checked as client context below
                'require': ['exp', 'iat', 'iss', 'sub'],
            },
        )
    except jwt.PyJWTError as exc:
        raise AuthError(f'invalid token: {exc}')


def _audiences(claims):
    aud = claims.get('aud')
    if isinstance(aud, str):
        return [aud]
    return [a for a in aud if isinstance(a, str)] if isinstance(aud, list) else []


def _check_context(claims):
    if claims.get('iss') not in settings.OE_ALLOWED_ISSUERS:
        raise AuthError('token issuer is not trusted by this service')
    if claims.get('typ') != 'Bearer':
        raise AuthError('not an access token')
    client_id = settings.OE_OIDC_CLIENT_ID
    if claims.get('azp') != client_id:
        raise AuthError(f'token was not issued to the {client_id} client')


def _check_role(claims):
    realm_access = claims.get('realm_access')
    roles = realm_access.get('roles') if isinstance(realm_access, dict) else None
    roles = roles if isinstance(roles, list) else []
    if settings.OE_REQUIRED_ROLE not in roles:
        raise ForbiddenError(f'{settings.OE_REQUIRED_ROLE} realm role required')


def require_it_ops(request):
    """Return the verified claims, or raise AuthError / ForbiddenError."""
    claims = _verified_claims(_bearer_token(request))
    _check_context(claims)
    _check_role(claims)
    return claims
