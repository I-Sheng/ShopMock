"""Server-side authorization for the Finance portal.

Hiding the UI is not authorization: every case below is a token a browser could
put on the wire, and each must be settled by verifying the RS256 signature, the
issuer, the client the token was minted for, and the exact `finance` realm role
— before any finance row is read.

The cross-role cases are the point of this feature. Two shapes matter and they
fail differently, on purpose:

  * An HR employee who signs in *through the Finance portal's own client* gets a
    structurally valid `finance-portal` token that carries `hr` and not
    `finance`. They are authenticated to this application but not entitled to
    it: 403.
  * A token minted for the `hr-portal` client never belongs here at all, no
    matter which roles it carries: 401.
"""
import jwt
from django.test import SimpleTestCase, override_settings

from ledger.auth import AuthError, ForbiddenError, require_finance
from ledger.tests import tokens


class FakeRequest:
    def __init__(self, authorization=None):
        self.headers = {} if authorization is None else {'Authorization': authorization}


def settings_for(**overrides):
    base = {
        'FINANCE_JWT_JWK': tokens.public_jwk(),
        'FINANCE_ALLOWED_ISSUERS': [tokens.ISSUER],
        'FINANCE_OIDC_CLIENT_ID': tokens.CLIENT_ID,
        'FINANCE_REQUIRED_ROLE': 'finance',
    }
    base.update(overrides)
    return base


@override_settings(**settings_for())
class AcceptsFinanceTests(SimpleTestCase):
    def test_finance_realm_role_is_admitted(self):
        claims = require_finance(FakeRequest(tokens.bearer(['employee', 'finance'])))

        self.assertEqual(claims['sub'], tokens.SUBJECT)
        self.assertEqual(claims['preferred_username'], 'finance.clerk')

    def test_finance_without_the_employee_role_is_still_admitted(self):
        claims = require_finance(FakeRequest(tokens.bearer(['finance'])))

        self.assertEqual(claims['azp'], tokens.CLIENT_ID)

    def test_finance_alongside_other_workforce_roles_is_admitted(self):
        self.assertTrue(
            require_finance(FakeRequest(
                tokens.bearer(['employee', 'finance', 'offline_access']))))

    def test_bearer_scheme_is_matched_case_insensitively(self):
        token = tokens.make_token(['finance'])

        self.assertTrue(require_finance(FakeRequest(f'bearer {token}')))


@override_settings(**settings_for())
class RejectsMissingOrMalformedTokenTests(SimpleTestCase):
    def assertUnauthenticated(self, authorization):
        with self.assertRaises(AuthError) as caught:
            require_finance(FakeRequest(authorization))
        self.assertNotIsInstance(caught.exception, ForbiddenError)
        self.assertEqual(caught.exception.status, 401)
        return caught.exception

    def test_no_authorization_header(self):
        self.assertUnauthenticated(None)

    def test_empty_authorization_header(self):
        self.assertUnauthenticated('')

    def test_non_bearer_scheme(self):
        self.assertUnauthenticated('Basic ZmluYW5jZS5jbGVyazpwYXNzd29yZA==')

    def test_bearer_with_no_token(self):
        self.assertUnauthenticated('Bearer ')

    def test_garbage_instead_of_a_jwt(self):
        self.assertUnauthenticated('Bearer not-a-jwt-at-all')

    def test_truncated_jwt(self):
        self.assertUnauthenticated('Bearer ' + tokens.make_token()[:-8])

    def test_signature_from_a_foreign_key(self):
        self.assertUnauthenticated(tokens.bearer(['finance'], key_tag='attacker'))

    def test_unsigned_alg_none_token(self):
        unsigned = jwt.encode(tokens.claims_for(['finance']), key=None, algorithm='none')

        self.assertUnauthenticated(f'Bearer {unsigned}')

    def test_hmac_algorithm_confusion_using_the_public_key_as_the_secret(self):
        forged = jwt.encode(
            tokens.claims_for(['finance']), tokens.public_jwk(), algorithm='HS256')

        self.assertUnauthenticated(f'Bearer {forged}')

    def test_expired_token(self):
        self.assertUnauthenticated(tokens.bearer(['finance'], exp=1755859200))

    def test_token_without_an_expiry(self):
        self.assertUnauthenticated(tokens.bearer(['finance'], exp=None))

    def test_token_without_a_subject(self):
        self.assertUnauthenticated(tokens.bearer(['finance'], sub=None))

    def test_fails_closed_when_no_verification_key_is_configured(self):
        with override_settings(FINANCE_JWT_JWK=''):
            self.assertUnauthenticated(tokens.bearer(['finance']))

    def test_fails_closed_when_the_verification_key_is_unusable(self):
        with override_settings(FINANCE_JWT_JWK='{not-json'):
            self.assertUnauthenticated(tokens.bearer(['finance']))


@override_settings(**settings_for())
class RejectsWrongIssuerOrClientTests(SimpleTestCase):
    def assertUnauthenticated(self, authorization):
        with self.assertRaises(AuthError) as caught:
            require_finance(FakeRequest(authorization))
        self.assertNotIsInstance(caught.exception, ForbiddenError)
        self.assertEqual(caught.exception.status, 401)

    def test_issuer_from_another_realm(self):
        self.assertUnauthenticated(
            tokens.bearer(['finance'], iss='http://localhost/auth/realms/master'))

    def test_issuer_from_an_unregistered_origin(self):
        self.assertUnauthenticated(
            tokens.bearer(['finance'], iss='http://evil.example/auth/realms/shopmock'))

    def test_missing_issuer(self):
        self.assertUnauthenticated(tokens.bearer(['finance'], iss=None))

    def test_token_minted_for_the_hr_portal(self):
        """The HR portal's own client is not a way into Finance."""
        self.assertUnauthenticated(
            tokens.bearer(['employee', 'finance'], azp=tokens.HR_CLIENT_ID))

    def test_token_minted_for_the_it_console(self):
        self.assertUnauthenticated(tokens.bearer(['finance'], azp='it-operations'))

    def test_token_minted_for_seller_central(self):
        self.assertUnauthenticated(tokens.bearer(['finance'], azp='seller-dashboard'))

    def test_token_minted_for_the_storefront(self):
        self.assertUnauthenticated(tokens.bearer(['finance'], azp='storefront'))

    def test_missing_client_context(self):
        self.assertUnauthenticated(tokens.bearer(['finance'], azp=None))

    def test_id_token_instead_of_an_access_token(self):
        self.assertUnauthenticated(tokens.bearer(['finance'], typ='ID'))

    def test_refresh_token_instead_of_an_access_token(self):
        self.assertUnauthenticated(tokens.bearer(['finance'], typ='Refresh'))

    def test_audience_cannot_replace_the_authorized_party(self):
        self.assertUnauthenticated(
            tokens.bearer(['finance'], azp=tokens.HR_CLIENT_ID,
                          aud=['account', tokens.CLIENT_ID]))

    def test_a_second_registered_issuer_is_accepted(self):
        campus = 'http://shopmock.uwb.edu/auth/realms/shopmock'
        with override_settings(FINANCE_ALLOWED_ISSUERS=[tokens.ISSUER, campus]):
            claims = require_finance(FakeRequest(tokens.bearer(['finance'], iss=campus)))

        self.assertEqual(claims['iss'], campus)


@override_settings(**settings_for())
class RejectsNonFinanceWorkforceTests(SimpleTestCase):
    """Authenticated to this application, but not entitled to it: 403."""

    def assertForbidden(self, authorization):
        with self.assertRaises(ForbiddenError) as caught:
            require_finance(FakeRequest(authorization))
        self.assertEqual(caught.exception.status, 403)

    def test_hr_specialist_signing_into_the_finance_portal_is_denied(self):
        """The cross-role case: a real HR session, on this portal's own client."""
        self.assertForbidden(tokens.bearer(
            ['employee', 'hr'], preferred_username='hr.specialist'))

    def test_gadmin_employee_plus_global_admin_is_denied(self):
        self.assertForbidden(tokens.bearer(['employee', 'global-admin']))

    def test_it_operations_is_denied(self):
        self.assertForbidden(tokens.bearer(['employee', 'it-ops']))

    def test_a_plain_employee_is_denied(self):
        self.assertForbidden(tokens.bearer(['employee']))

    def test_seller_is_denied(self):
        self.assertForbidden(tokens.bearer(['seller']))

    def test_customer_is_denied(self):
        self.assertForbidden(tokens.bearer(['customer']))

    def test_no_realm_roles_at_all_is_denied(self):
        self.assertForbidden(tokens.bearer([]))

    def test_missing_realm_access_claim_is_denied(self):
        self.assertForbidden(tokens.bearer(['finance'], realm_access=None))

    def test_malformed_realm_access_claim_is_denied(self):
        self.assertForbidden(tokens.bearer(['finance'], realm_access='finance'))

    def test_a_client_role_named_finance_is_not_the_realm_role(self):
        self.assertForbidden(tokens.bearer(
            [], resource_access={'finance-portal': {'roles': ['finance']}}))

    def test_a_lookalike_role_name_is_denied(self):
        self.assertForbidden(tokens.bearer(['finance-readonly']))

    def test_a_prefixed_role_name_is_denied(self):
        self.assertForbidden(tokens.bearer(['shopmock-finance']))

    def test_role_matching_is_case_sensitive(self):
        self.assertForbidden(tokens.bearer(['FINANCE']))
