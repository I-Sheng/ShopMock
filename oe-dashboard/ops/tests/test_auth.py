"""Server-side authorization for the IT operations console.

Hiding the UI is not authorization: every one of these cases is a token that a
browser could put on the wire, and each must be settled by verifying the
signature, the issuer, the client the token was minted for, and the `it-ops`
realm role — before any container state is read.
"""
import jwt
from django.test import SimpleTestCase, override_settings

from ops.auth import AuthError, ForbiddenError, require_it_ops
from ops.tests import tokens


class FakeRequest:
    def __init__(self, authorization=None):
        self.headers = {} if authorization is None else {'Authorization': authorization}


def settings_for(**overrides):
    base = {
        'OE_JWT_JWK': tokens.public_jwk(),
        'OE_ALLOWED_ISSUERS': [tokens.ISSUER],
        'OE_OIDC_CLIENT_ID': tokens.CLIENT_ID,
        'OE_REQUIRED_ROLE': 'it-ops',
    }
    base.update(overrides)
    return base


@override_settings(**settings_for())
class AcceptsItOpsTests(SimpleTestCase):
    def test_it_ops_realm_role_is_admitted(self):
        claims = require_it_ops(FakeRequest(tokens.bearer(['it-ops'])))

        self.assertEqual(claims['sub'], tokens.SUBJECT)
        self.assertEqual(claims['preferred_username'], 'it.ops')

    def test_it_ops_alongside_other_workforce_roles_is_admitted(self):
        claims = require_it_ops(
            FakeRequest(tokens.bearer(['employee', 'it-ops', 'offline_access'])))

        self.assertEqual(claims['azp'], tokens.CLIENT_ID)

    def test_bearer_scheme_is_matched_case_insensitively(self):
        token = tokens.make_token(['it-ops'])

        self.assertTrue(require_it_ops(FakeRequest(f'bearer {token}')))


@override_settings(**settings_for())
class RejectsMissingOrMalformedTokenTests(SimpleTestCase):
    def assertUnauthenticated(self, authorization):
        with self.assertRaises(AuthError) as caught:
            require_it_ops(FakeRequest(authorization))
        self.assertNotIsInstance(caught.exception, ForbiddenError)
        self.assertEqual(caught.exception.status, 401)
        return caught.exception

    def test_no_authorization_header(self):
        self.assertUnauthenticated(None)

    def test_empty_authorization_header(self):
        self.assertUnauthenticated('')

    def test_non_bearer_scheme(self):
        self.assertUnauthenticated('Basic aXQub3BzOnBhc3N3b3Jk')

    def test_bearer_with_no_token(self):
        self.assertUnauthenticated('Bearer ')

    def test_garbage_instead_of_a_jwt(self):
        self.assertUnauthenticated('Bearer not-a-jwt-at-all')

    def test_truncated_jwt(self):
        self.assertUnauthenticated('Bearer ' + tokens.make_token()[:-8])

    def test_signature_from_a_foreign_key(self):
        self.assertUnauthenticated(tokens.bearer(['it-ops'], key_tag='attacker'))

    def test_unsigned_alg_none_token(self):
        unsigned = jwt.encode(tokens.claims_for(['it-ops']), key=None, algorithm='none')

        self.assertUnauthenticated(f'Bearer {unsigned}')

    def test_hmac_algorithm_confusion_using_the_public_key_as_the_secret(self):
        forged = jwt.encode(
            tokens.claims_for(['it-ops']), tokens.public_jwk(), algorithm='HS256')

        self.assertUnauthenticated(f'Bearer {forged}')

    def test_expired_token(self):
        self.assertUnauthenticated(tokens.bearer(['it-ops'], exp=1755859200))

    def test_token_without_an_expiry(self):
        self.assertUnauthenticated(tokens.bearer(['it-ops'], exp=None))

    def test_token_without_a_subject(self):
        self.assertUnauthenticated(tokens.bearer(['it-ops'], sub=None))

    def test_fails_closed_when_no_verification_key_is_configured(self):
        with override_settings(OE_JWT_JWK=''):
            self.assertUnauthenticated(tokens.bearer(['it-ops']))


@override_settings(**settings_for())
class RejectsWrongIssuerOrClientTests(SimpleTestCase):
    def assertUnauthenticated(self, authorization):
        with self.assertRaises(AuthError) as caught:
            require_it_ops(FakeRequest(authorization))
        self.assertEqual(caught.exception.status, 401)

    def test_issuer_from_another_realm(self):
        self.assertUnauthenticated(
            tokens.bearer(['it-ops'], iss='http://localhost/auth/realms/master'))

    def test_issuer_from_an_unregistered_origin(self):
        self.assertUnauthenticated(
            tokens.bearer(['it-ops'], iss='http://evil.example/auth/realms/shopmock'))

    def test_missing_issuer(self):
        self.assertUnauthenticated(tokens.bearer(['it-ops'], iss=None))

    def test_token_minted_for_seller_central(self):
        self.assertUnauthenticated(tokens.bearer(['it-ops'], azp='seller-dashboard'))

    def test_token_minted_for_the_storefront(self):
        self.assertUnauthenticated(tokens.bearer(['it-ops'], azp='storefront'))

    def test_missing_client_context(self):
        self.assertUnauthenticated(tokens.bearer(['it-ops'], azp=None))

    def test_id_token_instead_of_an_access_token(self):
        self.assertUnauthenticated(tokens.bearer(['it-ops'], typ='ID'))

    def test_refresh_token_instead_of_an_access_token(self):
        self.assertUnauthenticated(tokens.bearer(['it-ops'], typ='Refresh'))

    def test_a_second_registered_issuer_is_accepted(self):
        campus = 'http://shopmock.uwb.edu/auth/realms/shopmock'
        with override_settings(OE_ALLOWED_ISSUERS=[tokens.ISSUER, campus]):
            claims = require_it_ops(FakeRequest(tokens.bearer(['it-ops'], iss=campus)))

        self.assertEqual(claims['iss'], campus)

    def test_audience_cannot_replace_the_authorized_party(self):
        self.assertUnauthenticated(
            tokens.bearer(['it-ops'], azp='other-client',
                          aud=['account', tokens.CLIENT_ID]))


@override_settings(**settings_for())
class RejectsNonItWorkforceTests(SimpleTestCase):
    def assertForbidden(self, authorization):
        with self.assertRaises(ForbiddenError) as caught:
            require_it_ops(FakeRequest(authorization))
        self.assertEqual(caught.exception.status, 403)

    def test_gadmin_employee_plus_global_admin_is_denied(self):
        """Tier-0 global admin is not IT operations: no it-ops, no dashboard."""
        self.assertForbidden(tokens.bearer(['employee', 'global-admin']))

    def test_finance_clerk_employee_only_is_denied(self):
        self.assertForbidden(tokens.bearer(['employee']))

    def test_seller_is_denied(self):
        self.assertForbidden(tokens.bearer(['seller']))

    def test_customer_is_denied(self):
        self.assertForbidden(tokens.bearer(['customer']))

    def test_no_realm_roles_at_all_is_denied(self):
        self.assertForbidden(tokens.bearer([]))

    def test_missing_realm_access_claim_is_denied(self):
        self.assertForbidden(tokens.bearer(['it-ops'], realm_access=None))

    def test_malformed_realm_access_claim_is_denied(self):
        self.assertForbidden(tokens.bearer(['it-ops'], realm_access='it-ops'))

    def test_a_client_role_named_it_ops_is_not_the_realm_role(self):
        self.assertForbidden(tokens.bearer(
            [], resource_access={'it-operations': {'roles': ['it-ops']}}))

    def test_a_lookalike_role_name_is_denied(self):
        self.assertForbidden(tokens.bearer(['it-ops-readonly']))

    def test_role_matching_is_case_sensitive(self):
        self.assertForbidden(tokens.bearer(['IT-OPS']))
