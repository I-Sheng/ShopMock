"""Server-side authorization for the HR portal.

Same trust anchor and the same six checks as the Finance portal, but with the
`hr` realm role and the `hr-portal` client. The mirror-image cross-role cases
are the point:

  * a Finance clerk who signs in *through the HR portal's own client* is
    authenticated here but carries `finance`, not `hr`: 403;
  * a token minted for the `finance-portal` client never belongs here at all,
    whatever roles it carries: 401.
"""
import jwt
from django.test import SimpleTestCase, override_settings

from people.auth import AuthError, ForbiddenError, require_hr
from people.tests import tokens


class FakeRequest:
    def __init__(self, authorization=None):
        self.headers = {} if authorization is None else {'Authorization': authorization}


def settings_for(**overrides):
    base = {
        'HR_JWT_JWK': tokens.public_jwk(),
        'HR_ALLOWED_ISSUERS': [tokens.ISSUER],
        'HR_OIDC_CLIENT_ID': tokens.CLIENT_ID,
        'HR_REQUIRED_ROLE': 'hr',
    }
    base.update(overrides)
    return base


@override_settings(**settings_for())
class AcceptsHrTests(SimpleTestCase):
    def test_hr_realm_role_is_admitted(self):
        claims = require_hr(FakeRequest(tokens.bearer(['employee', 'hr'])))

        self.assertEqual(claims['sub'], tokens.SUBJECT)
        self.assertEqual(claims['preferred_username'], 'hr.specialist')

    def test_hr_without_the_employee_role_is_still_admitted(self):
        claims = require_hr(FakeRequest(tokens.bearer(['hr'])))

        self.assertEqual(claims['azp'], tokens.CLIENT_ID)

    def test_hr_alongside_other_workforce_roles_is_admitted(self):
        self.assertTrue(
            require_hr(FakeRequest(tokens.bearer(['employee', 'hr', 'offline_access']))))

    def test_bearer_scheme_is_matched_case_insensitively(self):
        token = tokens.make_token(['hr'])

        self.assertTrue(require_hr(FakeRequest(f'bearer {token}')))


@override_settings(**settings_for())
class RejectsMissingOrMalformedTokenTests(SimpleTestCase):
    def assertUnauthenticated(self, authorization):
        with self.assertRaises(AuthError) as caught:
            require_hr(FakeRequest(authorization))
        self.assertNotIsInstance(caught.exception, ForbiddenError)
        self.assertEqual(caught.exception.status, 401)
        return caught.exception

    def test_no_authorization_header(self):
        self.assertUnauthenticated(None)

    def test_empty_authorization_header(self):
        self.assertUnauthenticated('')

    def test_non_bearer_scheme(self):
        self.assertUnauthenticated('Basic aHIuc3BlY2lhbGlzdDpwYXNzd29yZA==')

    def test_bearer_with_no_token(self):
        self.assertUnauthenticated('Bearer ')

    def test_garbage_instead_of_a_jwt(self):
        self.assertUnauthenticated('Bearer not-a-jwt-at-all')

    def test_truncated_jwt(self):
        self.assertUnauthenticated('Bearer ' + tokens.make_token()[:-8])

    def test_signature_from_a_foreign_key(self):
        self.assertUnauthenticated(tokens.bearer(['hr'], key_tag='attacker'))

    def test_unsigned_alg_none_token(self):
        unsigned = jwt.encode(tokens.claims_for(['hr']), key=None, algorithm='none')

        self.assertUnauthenticated(f'Bearer {unsigned}')

    def test_hmac_algorithm_confusion_using_the_public_key_as_the_secret(self):
        forged = jwt.encode(
            tokens.claims_for(['hr']), tokens.public_jwk(), algorithm='HS256')

        self.assertUnauthenticated(f'Bearer {forged}')

    def test_expired_token(self):
        self.assertUnauthenticated(tokens.bearer(['hr'], exp=1755859200))

    def test_token_without_an_expiry(self):
        self.assertUnauthenticated(tokens.bearer(['hr'], exp=None))

    def test_token_without_a_subject(self):
        self.assertUnauthenticated(tokens.bearer(['hr'], sub=None))

    def test_fails_closed_when_no_verification_key_is_configured(self):
        with override_settings(HR_JWT_JWK=''):
            self.assertUnauthenticated(tokens.bearer(['hr']))

    def test_fails_closed_when_the_verification_key_is_unusable(self):
        with override_settings(HR_JWT_JWK='{not-json'):
            self.assertUnauthenticated(tokens.bearer(['hr']))


@override_settings(**settings_for())
class RejectsWrongIssuerOrClientTests(SimpleTestCase):
    def assertUnauthenticated(self, authorization):
        with self.assertRaises(AuthError) as caught:
            require_hr(FakeRequest(authorization))
        self.assertNotIsInstance(caught.exception, ForbiddenError)
        self.assertEqual(caught.exception.status, 401)

    def test_issuer_from_another_realm(self):
        self.assertUnauthenticated(
            tokens.bearer(['hr'], iss='http://localhost/auth/realms/master'))

    def test_issuer_from_an_unregistered_origin(self):
        self.assertUnauthenticated(
            tokens.bearer(['hr'], iss='http://evil.example/auth/realms/shopmock'))

    def test_missing_issuer(self):
        self.assertUnauthenticated(tokens.bearer(['hr'], iss=None))

    def test_token_minted_for_the_finance_portal(self):
        """The Finance portal's own client is not a way into HR."""
        self.assertUnauthenticated(
            tokens.bearer(['employee', 'hr'], azp=tokens.FINANCE_CLIENT_ID))

    def test_token_minted_for_the_it_console(self):
        self.assertUnauthenticated(tokens.bearer(['hr'], azp='it-operations'))

    def test_token_minted_for_seller_central(self):
        self.assertUnauthenticated(tokens.bearer(['hr'], azp='seller-dashboard'))

    def test_token_minted_for_the_storefront(self):
        self.assertUnauthenticated(tokens.bearer(['hr'], azp='storefront'))

    def test_missing_client_context(self):
        self.assertUnauthenticated(tokens.bearer(['hr'], azp=None))

    def test_id_token_instead_of_an_access_token(self):
        self.assertUnauthenticated(tokens.bearer(['hr'], typ='ID'))

    def test_refresh_token_instead_of_an_access_token(self):
        self.assertUnauthenticated(tokens.bearer(['hr'], typ='Refresh'))

    def test_audience_cannot_replace_the_authorized_party(self):
        self.assertUnauthenticated(
            tokens.bearer(['hr'], azp=tokens.FINANCE_CLIENT_ID,
                          aud=['account', tokens.CLIENT_ID]))

    def test_a_second_registered_issuer_is_accepted(self):
        campus = 'http://shopmock.uwb.edu/auth/realms/shopmock'
        with override_settings(HR_ALLOWED_ISSUERS=[tokens.ISSUER, campus]):
            claims = require_hr(FakeRequest(tokens.bearer(['hr'], iss=campus)))

        self.assertEqual(claims['iss'], campus)


@override_settings(**settings_for())
class RejectsNonHrWorkforceTests(SimpleTestCase):
    """Authenticated to this application, but not entitled to it: 403."""

    def assertForbidden(self, authorization):
        with self.assertRaises(ForbiddenError) as caught:
            require_hr(FakeRequest(authorization))
        self.assertEqual(caught.exception.status, 403)

    def test_finance_clerk_signing_into_the_hr_portal_is_denied(self):
        """The cross-role case: a real Finance session, on this portal's client."""
        self.assertForbidden(tokens.bearer(
            ['employee', 'finance'], preferred_username='finance.clerk'))

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
        self.assertForbidden(tokens.bearer(['hr'], realm_access=None))

    def test_malformed_realm_access_claim_is_denied(self):
        self.assertForbidden(tokens.bearer(['hr'], realm_access='hr'))

    def test_a_client_role_named_hr_is_not_the_realm_role(self):
        self.assertForbidden(tokens.bearer(
            [], resource_access={'hr-portal': {'roles': ['hr']}}))

    def test_a_lookalike_role_name_is_denied(self):
        self.assertForbidden(tokens.bearer(['hr-readonly']))

    def test_a_prefixed_role_name_is_denied(self):
        self.assertForbidden(tokens.bearer(['shopmock-hr']))

    def test_role_matching_is_case_sensitive(self):
        self.assertForbidden(tokens.bearer(['HR']))
