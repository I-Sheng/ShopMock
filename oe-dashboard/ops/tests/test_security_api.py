"""HTTP surface of the security-monitoring endpoint.

Exactly the contract `test_api.py` pins for container status, applied to the
SIEM: the same it-ops authorization, checked *before* OpenSearch is touched, so
a rejected caller produces no query against the alert store at all; and a
backend failure that cannot turn into an information leak.
"""
import json
from unittest.mock import patch

from django.test import Client, SimpleTestCase, override_settings

from ops.alerts import AlertApiError, empty_model
from ops.tests import tokens
from ops.tests.test_alerts import hit, response
from ops.tests.test_api import settings_for

API = '/api/security/alerts'


def alert_model(**kwargs):
    from ops.alerts import model_from_response
    return model_from_response(response(**kwargs), 25)


@override_settings(**settings_for())
class SecurityApiAuthorizationTests(SimpleTestCase):
    def setUp(self):
        self.client = Client()
        patcher = patch('ops.views.fetch_alerts')
        self.backend = patcher.start()
        self.backend.return_value = empty_model()
        self.addCleanup(patcher.stop)

    def get(self, authorization=None):
        headers = {} if authorization is None else {'HTTP_AUTHORIZATION': authorization}
        return self.client.get(API, **headers)

    def assertDenied(self, response, status):
        self.assertEqual(response.status_code, status)
        body = json.loads(response.content)
        self.assertIn('error', body)
        self.assertNotIn('alerts', body)
        self.assertNotIn('summary', body)
        # The decisive property: no query was ever sent to the SIEM.
        self.backend.assert_not_called()

    def test_missing_token_is_401_and_never_reaches_opensearch(self):
        self.assertDenied(self.get(), 401)

    def test_malformed_token_is_401_and_never_reaches_opensearch(self):
        self.assertDenied(self.get('Bearer garbage.token.value'), 401)

    def test_foreign_signature_is_401(self):
        self.assertDenied(self.get(tokens.bearer(['it-ops'], key_tag='attacker')), 401)

    def test_expired_token_is_401(self):
        self.assertDenied(self.get(tokens.bearer(['it-ops'], exp=1755859200)), 401)

    def test_token_from_seller_central_is_401(self):
        self.assertDenied(self.get(tokens.bearer(['it-ops'], azp='seller-dashboard')), 401)

    def test_untrusted_issuer_is_401(self):
        self.assertDenied(
            self.get(tokens.bearer(['it-ops'], iss='http://evil.example/auth/realms/shopmock')),
            401)

    def test_gadmin_is_403(self):
        self.assertDenied(self.get(tokens.bearer(['employee', 'global-admin'])), 403)

    def test_finance_clerk_is_403(self):
        self.assertDenied(self.get(tokens.bearer(['employee'])), 403)

    def test_seller_is_403(self):
        self.assertDenied(self.get(tokens.bearer(['seller'])), 403)

    def test_customer_is_403(self):
        self.assertDenied(self.get(tokens.bearer(['customer'])), 403)

    def test_a_client_role_of_the_same_name_is_not_enough(self):
        self.assertDenied(
            self.get(tokens.bearer(
                ['employee'], resource_access={'it-operations': {'roles': ['it-ops']}})),
            403)

    def test_write_methods_are_not_offered(self):
        for method in (self.client.post, self.client.put, self.client.delete):
            response = method(API, HTTP_AUTHORIZATION=tokens.bearer(['it-ops']))

            self.assertEqual(response.status_code, 405)
        self.backend.assert_not_called()


@override_settings(**settings_for())
class SecurityApiPayloadTests(SimpleTestCase):
    def setUp(self):
        self.client = Client()
        patcher = patch('ops.views.fetch_alerts')
        self.backend = patcher.start()
        self.backend.return_value = alert_model(
            hits=[hit(), hit(level=12, rule_id='5712', description='sshd brute force')],
            buckets=[(12, 1), (7, 1)], total=2)
        self.addCleanup(patcher.stop)

    def get(self):
        return self.client.get(API, HTTP_AUTHORIZATION=tokens.bearer(['it-ops']))

    def test_it_user_gets_the_summary_and_the_recent_alerts(self):
        response = self.get()

        self.assertEqual(response.status_code, 200)
        body = json.loads(response.content)
        self.assertIn('generated_at', body)
        self.assertEqual(body['summary']['total'], 2)
        self.assertEqual(body['summary']['critical'], 1)
        self.assertEqual(body['summary']['medium'], 1)
        self.assertEqual(len(body['alerts']), 2)

    def test_each_alert_carries_only_the_allowlisted_fields(self):
        body = json.loads(self.get().content)

        for alert in body['alerts']:
            self.assertEqual(sorted(alert), [
                'agent_id', 'agent_name', 'location', 'rule_description',
                'rule_id', 'rule_level', 'timestamp',
            ])

    def test_payload_carries_no_log_contents_or_backend_internals(self):
        content = self.get().content.decode()

        for marker in ('LEAK-FULLLOG', 'LEAK-DATA', 'LEAK-PASSWORD', 'LEAK-DECODER',
                       'SUPERSECRET-LAB-TOKEN', '_source', 'wazuh-alerts-4.x',
                       'search:9200'):
            self.assertNotIn(marker, content)

    def test_response_is_not_cached(self):
        self.assertIn('no-store', self.get().headers['Cache-Control'])

    def test_an_empty_alert_store_is_a_normal_200(self):
        self.backend.return_value = empty_model()

        response = self.get()

        self.assertEqual(response.status_code, 200)
        body = json.loads(response.content)
        self.assertEqual(body['alerts'], [])
        self.assertEqual(body['summary']['total'], 0)
        self.assertIsNone(body['summary']['highest_level'])

    def test_backend_failure_is_502_without_leaking_internals(self):
        self.backend.side_effect = AlertApiError(
            'https://search:9200: SSLCertVerificationError')

        response = self.get()

        self.assertEqual(response.status_code, 502)
        body = json.loads(response.content)
        self.assertEqual(body['error'], 'security alerts unavailable')
        self.assertNotIn('search:9200', response.content.decode())

    def test_unexpected_backend_error_is_also_contained(self):
        self.backend.side_effect = RuntimeError(
            'Basic YWRtaW46U1VQRVJTRUNSRVQtTEFCLVRPS0VO boom')

        response = self.get()

        self.assertEqual(response.status_code, 502)
        self.assertNotIn('Basic ', response.content.decode())
        self.assertNotIn('Traceback', response.content.decode())
