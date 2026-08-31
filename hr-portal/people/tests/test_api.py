"""HTTP surface of the HR portal.

Beyond the status codes: an unauthorized caller must never cause a database
query to run, no response may carry an individual's pay or a government
identifier, and a database failure must not turn into an information leak.
"""
import json
from datetime import date
from unittest.mock import patch

from django.test import Client, SimpleTestCase, override_settings

from people.tests import tokens

OVERVIEW = '/api/overview'
EMPLOYEES = '/api/employees'
LEAVE = '/api/leave'
API_PATHS = (OVERVIEW, EMPLOYEES, LEAVE)


def settings_for(**overrides):
    base = {
        'HR_JWT_JWK': tokens.public_jwk(),
        'HR_ALLOWED_ISSUERS': [tokens.ISSUER],
        'HR_OIDC_CLIENT_ID': tokens.CLIENT_ID,
        'HR_REQUIRED_ROLE': 'hr',
        'HR_OIDC_REALM': 'shopmock',
        'HR_MAX_ROWS': 200,
    }
    base.update(overrides)
    return base


FAKE_EMPLOYEES = [
    {'id': 1, 'employee_no': 'E-0001', 'first_name': 'Rosa', 'last_name': 'Marek',
     'work_email': 'rosa.marek@shopmock.lab', 'job_title': 'People Partner',
     'department': 'People Operations', 'employment_type': 'full_time',
     'status': 'active', 'hired_on': date(2023, 4, 3)},
]
FAKE_DEPARTMENTS = [
    {'id': 1, 'name': 'People Operations', 'cost_center': 'CC-100',
     'headcount_budget': 4, 'headcount': 2, 'payroll_cents': 1_800_000},
]
FAKE_LEAVE = [
    {'id': 1, 'employee_no': 'E-0001', 'first_name': 'Rosa', 'last_name': 'Marek',
     'kind': 'vacation', 'starts_on': date(2026, 7, 6),
     'ends_on': date(2026, 7, 10), 'days': 5, 'status': 'approved'},
]
FAKE_HEADCOUNT = {'active': 7, 'on_leave': 1}


def stub_queries():
    """Patch every data function so the suite never needs a live hr-db."""
    return (
        patch('people.views.queries.employees', return_value=FAKE_EMPLOYEES),
        patch('people.views.queries.departments', return_value=FAKE_DEPARTMENTS),
        patch('people.views.queries.leave_requests', return_value=FAKE_LEAVE),
        patch('people.views.queries.headcount', return_value=FAKE_HEADCOUNT),
    )


class StubbedQueriesMixin:
    def setUp(self):
        super().setUp()
        self.client = Client()
        self.patches = stub_queries()
        self.stubs = [p.start() for p in self.patches]
        self.addCleanup(lambda: [p.stop() for p in self.patches])

    def get(self, path, roles=('employee', 'hr'), **token_kwargs):
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
        self.assertIn(b'hr-portal', response.content)

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

    def test_the_signed_out_shell_leaks_no_staff_data(self):
        response = self.client.get('/')

        for leaked in (b'Marek', b'shopmock.lab', b'E-0001'):
            self.assertNotIn(leaked, response.content)

    def test_assets_are_served_same_origin(self):
        for path, content_type in (('/app.css', 'text/css'),
                                   ('/app.js', 'text/javascript'),
                                   ('/pkce.js', 'text/javascript')):
            response = self.client.get(path)

            self.assertEqual(response.status_code, 200, path)
            self.assertIn(content_type, response.headers['Content-Type'], path)


@override_settings(**settings_for())
class AuthorizedReadsTests(StubbedQueriesMixin, SimpleTestCase):
    def test_hr_specialist_reads_the_overview(self):
        response = self.get(OVERVIEW)

        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content)
        self.assertEqual(payload['headcount']['active'], 7)
        self.assertEqual(payload['departments'][0]['name'], 'People Operations')

    def test_hr_specialist_reads_the_roster(self):
        response = self.get(EMPLOYEES)

        self.assertEqual(response.status_code, 200)
        row = json.loads(response.content)['employees'][0]
        self.assertEqual(row['employee_no'], 'E-0001')
        self.assertEqual(row['department'], 'People Operations')

    def test_hr_specialist_reads_leave_requests(self):
        response = self.get(LEAVE)

        self.assertEqual(response.status_code, 200)
        row = json.loads(response.content)['leave_requests'][0]
        self.assertEqual(row['kind'], 'vacation')
        self.assertEqual(row['starts_on'], '2026-07-06')

    def test_responses_are_never_cached(self):
        for path in API_PATHS:
            self.assertEqual(self.get(path).headers['Cache-Control'], 'no-store', path)

    def test_the_row_limit_is_clamped_to_the_configured_maximum(self):
        self.get(f'{EMPLOYEES}?limit=100000')

        self.assertEqual(self.stubs[0].call_args[0][0], 200)


@override_settings(**settings_for())
class SensitiveHrFieldsNeverReachTheWireTests(StubbedQueriesMixin, SimpleTestCase):
    def test_a_salary_leaking_out_of_the_database_becomes_a_server_error(self):
        self.stubs[0].return_value = [dict(FAKE_EMPLOYEES[0], base_salary_cents=900000)]

        response = self.get(EMPLOYEES)

        self.assertEqual(response.status_code, 500)
        self.assertNotIn(b'900000', response.content)

    def test_no_endpoint_body_carries_pay_or_identifiers(self):
        for path in API_PATHS:
            body = json.dumps(json.loads(self.get(path).content)).lower()

            for leaked in ('salary', 'ssn', 'national_id', 'bank_account'):
                self.assertNotIn(leaked, body, f'{path} leaked {leaked}')

    def test_no_endpoint_body_carries_finance_or_customer_data(self):
        for path in API_PATHS:
            body = json.dumps(json.loads(self.get(path).content)).lower()

            for leaked in ('order_ref', 'amount_cents', 'last4', 'wallet', 'token'):
                self.assertNotIn(leaked, body, f'{path} leaked {leaked}')


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

    def test_a_token_minted_for_the_finance_portal_is_401(self):
        for path in API_PATHS:
            self.assertDenied(self.get(path, azp=tokens.FINANCE_CLIENT_ID), 401)

    def test_a_finance_clerk_on_this_portals_client_is_403(self):
        """Cross-role denial: authenticated to HR, entitled to nothing."""
        for path in API_PATHS:
            self.assertDenied(
                self.get(path, roles=['employee', 'finance'],
                         preferred_username='finance.clerk'), 403)

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
        body = json.loads(self.client.get(EMPLOYEES).content)

        self.assertEqual(set(body), {'error'})
        self.assertNotIn('hr-db', body['error'])


@override_settings(**settings_for())
class MethodAndFailureHandlingTests(StubbedQueriesMixin, SimpleTestCase):
    def test_write_methods_are_rejected(self):
        for path in API_PATHS:
            response = self.client.post(
                path, HTTP_AUTHORIZATION=tokens.bearer(['employee', 'hr']))

            self.assertEqual(response.status_code, 405, path)

    def test_a_database_failure_is_a_502_without_connection_details(self):
        self.stubs[0].side_effect = RuntimeError(
            'connection to server at "hr-db" failed: password authentication')

        response = self.get(EMPLOYEES)

        self.assertEqual(response.status_code, 502)
        self.assertNotIn(b'hr-db', response.content)
        self.assertNotIn(b'password', response.content)
