"""Server-side authorization for the HR portal.

Same trust anchor and the same ordered checks as the IT console
(oe-dashboard/ops/auth.py) and the Finance portal: the pinned RS256 *public*
JWK from `PGRST_JWT_SECRET`, which PostgREST and the Django backends already
verify against.

  1. RS256 signature against the pinned realm key — `algorithms` is fixed, so
     an `alg: none` or HMAC-confusion token cannot be substituted.
  2. `exp`/`iat`/`iss`/`sub` must all be present.
  3. `iss` must be one of the origins the deploy registered for this realm.
  4. `typ` must be `Bearer` — an ID or refresh token is not an access token.
  5. The token must have been minted for the `hr-portal` client. A valid
     Finance portal, Seller Central, storefront or IT console token is rejected
     even if it carries the hr role.
  6. Only then: the `hr` *realm* role. A client role of the same name does not
     count.

Steps 1-5 fail as 401 (the caller is not authenticated to this application);
step 6 fails as 403 (authenticated, but not HR). A Finance clerk who signs in
through this portal's own client reaches step 6 and is told plainly that they
are not entitled to HR; a token borrowed from the Finance portal never gets
that far.
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
    """The caller is authenticated but lacks the hr realm role."""

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
    if not settings.HR_JWT_JWK:
        raise AuthError('token verification key is not configured')
    try:
        return jwt.decode(
            token, _verification_key(settings.HR_JWT_JWK),
            algorithms=['RS256'],
            options={
                'verify_aud': False,   # audience is checked as client context below
                'require': ['exp', 'iat', 'iss', 'sub'],
            },
        )
    except jwt.PyJWTError as exc:
        raise AuthError(f'invalid token: {exc}')


def _check_context(claims):
    if claims.get('iss') not in settings.HR_ALLOWED_ISSUERS:
        raise AuthError('token issuer is not trusted by this service')
    if claims.get('typ') != 'Bearer':
        raise AuthError('not an access token')
    client_id = settings.HR_OIDC_CLIENT_ID
    if claims.get('azp') != client_id:
        raise AuthError(f'token was not issued to the {client_id} client')


def _check_role(claims):
    realm_access = claims.get('realm_access')
    roles = realm_access.get('roles') if isinstance(realm_access, dict) else None
    roles = roles if isinstance(roles, list) else []
    if settings.HR_REQUIRED_ROLE not in roles:
        raise ForbiddenError(f'{settings.HR_REQUIRED_ROLE} realm role required')


def require_hr(request):
    """Return the verified claims, or raise AuthError / ForbiddenError."""
    claims = _verified_claims(_bearer_token(request))
    _check_context(claims)
    _check_role(claims)
    return claims
