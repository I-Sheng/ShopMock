"""HTTP surface of the Finance portal.

Beyond the status codes, three properties matter:

  * an unauthorized caller must never cause a database query to run;
  * no response body may ever contain a payment token, whatever the database
    returned;
  * a database failure must not turn into an information leak.
"""
import json
from datetime import date, datetime, timezone
from unittest.mock import patch

from django.test import Client, SimpleTestCase, override_settings

from ledger.tests import tokens

OVERVIEW = '/api/overview'
TRANSACTIONS = '/api/transactions'
PAYMENT_METHODS = '/api/payment-methods'
API_PATHS = (OVERVIEW, TRANSACTIONS, PAYMENT_METHODS)


def settings_for(**overrides):
    base = {
        'FINANCE_JWT_JWK': tokens.public_jwk(),
        'FINANCE_ALLOWED_ISSUERS': [tokens.ISSUER],
        'FINANCE_OIDC_CLIENT_ID': tokens.CLIENT_ID,
        'FINANCE_REQUIRED_ROLE': 'finance',
        'FINANCE_OIDC_REALM': 'shopmock',
        'FINANCE_MAX_ROWS': 200,
    }
    base.update(overrides)
    return base


FAKE_TRANSACTIONS = [
    {'id': 1, 'order_ref': 1, 'amount_cents': 142800, 'kind': 'charge',
     'status': 'settled',
     'processed_at': datetime(2026, 6, 27, 18, 30, tzinfo=timezone.utc)},
]
FAKE_PAYMENT_METHODS = [
    {'id': 1, 'brand': 'visa', 'last4': '4242', 'exp_month': 8, 'exp_year': 2028},
]
FAKE_REVENUE = [
    {'day': date(2026, 6, 27), 'gross_cents': 167700, 'refunds_cents': 0},
]
FAKE_WALLETS = [{'currency': 'USD', 'wallets': 2, 'balance_cents': 5000}]
FAKE_TOTALS = {'charge': {'count': 3, 'amount_cents': 172600}}


def stub_queries():
    """Patch every data function so the suite never needs a live finance-db."""
    return (
        patch('ledger.views.queries.transactions', return_value=FAKE_TRANSACTIONS),
        patch('ledger.views.queries.payment_methods', return_value=FAKE_PAYMENT_METHODS),
        patch('ledger.views.queries.revenue', return_value=FAKE_REVENUE),
        patch('ledger.views.queries.wallet_totals', return_value=FAKE_WALLETS),
        patch('ledger.views.queries.transaction_totals', return_value=FAKE_TOTALS),
    )


class StubbedQueriesMixin:
    def setUp(self):
        super().setUp()
        self.client = Client()
        self.patches = stub_queries()
        self.stubs = [p.start() for p in self.patches]
        self.addCleanup(lambda: [p.stop() for p in self.patches])

    def get(self, path, roles=('employee', 'finance'), **token_kwargs):
        return self.client.get(
            path, HTTP_AUTHORIZATION=tokens.bearer(roles, **token_kwargs))


@override_settings(**settings_for())
class PublicSurfaceTests(SimpleTestCase):
    def setUp(self):
        self.client = Client()

    def test_healthz_needs_no_token(self):
        response = self.client.get('/healthz')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.content), {'status': 'ok'})

    def test_index_serves_the_signed_out_shell(self):
        response = self.client.get('/')

        self.assertEqual(response.status_code, 200)
        self.assertIn(b'finance-portal', response.content)

    def test_index_never_ships_the_verification_key(self):
        response = self.client.get('/')

        jwk = json.loads(tokens.public_jwk())
        self.assertNotIn(jwk['n'].encode(), response.content)

    def test_index_sets_hardening_headers(self):
        response = self.client.get('/')

        self.assertEqual(response.headers['X-Frame-Options'], 'DENY')
        self.assertEqual(response.headers['X-Content-Type-Options'], 'nosniff')
        csp = response.headers['Content-Security-Policy']
        self.assertIn("default-src 'none'", csp)
        self.assertNotIn('unsafe-inline', csp)

    def test_assets_are_served_same_origin(self):
        for path, content_type in (('/app.css', 'text/css'),
                                   ('/app.js', 'text/javascript'),
                                   ('/pkce.js', 'text/javascript')):
            response = self.client.get(path)

            self.assertEqual(response.status_code, 200, path)
            self.assertIn(content_type, response.headers['Content-Type'], path)


@override_settings(**settings_for())
class AuthorizedReadsTests(StubbedQueriesMixin, SimpleTestCase):
    def test_finance_clerk_reads_the_overview(self):
        response = self.get(OVERVIEW)

        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content)
        self.assertEqual(payload['revenue'][0]['net_cents'], 167700)
        self.assertEqual(payload['wallets'][0]['currency'], 'USD')

    def test_finance_clerk_reads_transactions(self):
        response = self.get(TRANSACTIONS)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.content)['transactions'][0]['id'], 1)

    def test_finance_clerk_reads_masked_payment_methods(self):
        response = self.get(PAYMENT_METHODS)

        self.assertEqual(response.status_code, 200)
        card = json.loads(response.content)['payment_methods'][0]
        self.assertEqual(card['masked'], '•••• •••• •••• 4242')
        self.assertNotIn('token', card)

    def test_responses_are_never_cached(self):
        for path in API_PATHS:
            self.assertEqual(self.get(path).headers['Cache-Control'], 'no-store', path)

    def test_the_row_limit_is_clamped_to_the_configured_maximum(self):
        self.get(f'{TRANSACTIONS}?limit=100000')

        self.assertEqual(self.stubs[0].call_args[0][0], 200)

    def test_a_hostile_limit_parameter_is_ignored(self):
        response = self.get(f'{TRANSACTIONS}?limit=1;DROP+TABLE+finance.wallets')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.stubs[0].call_args[0][0], 200)


@override_settings(**settings_for())
class PaymentTokensNeverReachTheWireTests(StubbedQueriesMixin, SimpleTestCase):
    def test_a_token_leaking_out_of_the_database_becomes_a_server_error(self):
        """Fail closed: a 500 with no body detail beats publishing the token."""
        self.stubs[1].return_value = [
            dict(FAKE_PAYMENT_METHODS[0], token='tok_lab_ada_visa')]

        response = self.get(PAYMENT_METHODS)

        self.assertEqual(response.status_code, 500)
        self.assertNotIn(b'tok_lab_ada_visa', response.content)

    def test_no_endpoint_body_ever_contains_a_token_field(self):
        for path in API_PATHS:
            body = json.loads(self.get(path).content)

            self.assertNotIn(b'token', json.dumps(body).encode().lower(), path)


@override_settings(**settings_for())
class DeniedCallersTouchNoDatabaseTests(StubbedQueriesMixin, SimpleTestCase):
    def assertDenied(self, response, status):
        self.assertEqual(response.status_code, status)
        for stub in self.stubs:
            self.assertFalse(stub.called, 'a denied caller reached the database')

    def test_missing_bearer_token_is_401(self):
        for path in API_PATHS:
            self.assertDenied(self.client.get(path), 401)

    def test_malformed_bearer_token_is_401(self):
        for path in API_PATHS:
            self.assertDenied(
                self.client.get(path, HTTP_AUTHORIZATION='Bearer not-a-jwt'), 401)

    def test_a_token_minted_for_the_hr_portal_is_401(self):
        for path in API_PATHS:
            self.assertDenied(self.get(path, azp=tokens.HR_CLIENT_ID), 401)

    def test_an_hr_specialist_on_this_portals_client_is_403(self):
        """Cross-role denial: authenticated to Finance, entitled to nothing."""
        for path in API_PATHS:
            self.assertDenied(
                self.get(path, roles=['employee', 'hr'],
                         preferred_username='hr.specialist'), 403)

    def test_gadmin_is_403(self):
        for path in API_PATHS:
            self.assertDenied(self.get(path, roles=['employee', 'global-admin']), 403)

    def test_it_operations_is_403(self):
        for path in API_PATHS:
            self.assertDenied(self.get(path, roles=['employee', 'it-ops']), 403)

    def test_seller_is_403(self):
        for path in API_PATHS:
            self.assertDenied(self.get(path, roles=['seller']), 403)

    def test_customer_is_403(self):
        for path in API_PATHS:
            self.assertDenied(self.get(path, roles=['customer']), 403)

    def test_a_denial_body_carries_no_stack_detail(self):
        body = json.loads(self.client.get(OVERVIEW).content)

        self.assertEqual(set(body), {'error'})
        self.assertNotIn('finance-db', body['error'])


@override_settings(**settings_for())
class MethodAndFailureHandlingTests(StubbedQueriesMixin, SimpleTestCase):
    def test_write_methods_are_rejected(self):
        for path in API_PATHS:
            response = self.client.post(
                path, HTTP_AUTHORIZATION=tokens.bearer(['employee', 'finance']))

            self.assertEqual(response.status_code, 405, path)

    def test_a_database_failure_is_a_502_without_connection_details(self):
        self.stubs[0].side_effect = RuntimeError(
            'connection to server at "finance-db" failed: password authentication')

        response = self.get(TRANSACTIONS)

        self.assertEqual(response.status_code, 502)
        self.assertNotIn(b'finance-db', response.content)
        self.assertNotIn(b'password', response.content)
