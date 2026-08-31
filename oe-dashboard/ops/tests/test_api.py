"""HTTP surface of the IT operations console.

Two properties matter here beyond the status codes: an unauthorized caller must
never cause the container backend to be touched at all, and a backend failure
must not turn into an information leak.
"""
import json
from unittest.mock import patch

from django.test import Client, SimpleTestCase, override_settings

from ops.containers import ContainerApiError
from ops.tests import tokens
from ops.tests.test_containers import live_stack

API = '/api/containers'


def settings_for(**overrides):
    base = {
        'OE_JWT_JWK': tokens.public_jwk(),
        'OE_ALLOWED_ISSUERS': [tokens.ISSUER],
        'OE_OIDC_CLIENT_ID': tokens.CLIENT_ID,
        'OE_REQUIRED_ROLE': 'it-ops',
        'OE_PROJECT_NAME': 'shopmock',
        'OE_OIDC_REALM': 'shopmock',
    }
    base.update(overrides)
    return base


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
        self.assertIn(b'it-operations', response.content)

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
        self.assertIn("frame-ancestors 'none'", csp)
        self.assertNotIn('unsafe-inline', csp)

    def test_assets_are_served_separately_so_no_inline_script_is_needed(self):
        css = self.client.get('/app.css')
        js = self.client.get('/app.js')

        self.assertEqual(css.status_code, 200)
        self.assertEqual(css.headers['Content-Type'], 'text/css; charset=utf-8')
        self.assertEqual(js.status_code, 200)
        self.assertEqual(js.headers['Content-Type'], 'text/javascript; charset=utf-8')


@override_settings(**settings_for())
class ContainerApiAuthorizationTests(SimpleTestCase):
    def setUp(self):
        self.client = Client()
        patcher = patch('ops.views.list_containers')
        self.backend = patcher.start()
        self.backend.return_value = live_stack()
        self.addCleanup(patcher.stop)

    def get(self, authorization=None):
        headers = {} if authorization is None else {'HTTP_AUTHORIZATION': authorization}
        return self.client.get(API, **headers)

    def assertDenied(self, response, status):
        self.assertEqual(response.status_code, status)
        self.assertIn('error', json.loads(response.content))
        self.assertNotIn('containers', json.loads(response.content))
        self.backend.assert_not_called()

    def test_missing_token_is_401_and_never_reaches_the_socket(self):
        self.assertDenied(self.get(), 401)

    def test_malformed_token_is_401_and_never_reaches_the_socket(self):
        self.assertDenied(self.get('Bearer garbage.token.value'), 401)

    def test_foreign_signature_is_401(self):
        self.assertDenied(self.get(tokens.bearer(['it-ops'], key_tag='attacker')), 401)

    def test_expired_token_is_401(self):
        self.assertDenied(self.get(tokens.bearer(['it-ops'], exp=1755859200)), 401)

    def test_token_from_seller_central_is_401(self):
        self.assertDenied(self.get(tokens.bearer(['it-ops'], azp='seller-dashboard')), 401)

    def test_gadmin_is_403(self):
        self.assertDenied(self.get(tokens.bearer(['employee', 'global-admin'])), 403)

    def test_finance_clerk_is_403(self):
        self.assertDenied(self.get(tokens.bearer(['employee'])), 403)

    def test_seller_is_403(self):
        self.assertDenied(self.get(tokens.bearer(['seller'])), 403)

    def test_customer_is_403(self):
        self.assertDenied(self.get(tokens.bearer(['customer'])), 403)

    def test_write_methods_are_not_offered(self):
        response = self.client.post(
            API, HTTP_AUTHORIZATION=tokens.bearer(['it-ops']))

        self.assertEqual(response.status_code, 405)
        self.backend.assert_not_called()


@override_settings(**settings_for())
class ContainerApiPayloadTests(SimpleTestCase):
    def setUp(self):
        self.client = Client()
        patcher = patch('ops.views.list_containers')
        self.backend = patcher.start()
        self.backend.return_value = live_stack()
        self.addCleanup(patcher.stop)

    def get(self):
        return self.client.get(API, HTTP_AUTHORIZATION=tokens.bearer(['it-ops']))

    def test_it_user_gets_the_normalized_project_status(self):
        response = self.get()

        self.assertEqual(response.status_code, 200)
        body = json.loads(response.content)
        self.assertEqual(body['project'], 'shopmock')
        self.assertEqual(body['summary']['total'], 25)
        self.assertEqual(body['summary']['running'], 23)
        self.assertEqual(body['summary']['exited_ok'], 2)
        self.assertEqual(len(body['containers']), 25)
        self.assertEqual(body['containers'][0]['service'], 'catalog-db')

    def test_payload_carries_no_container_internals(self):
        response = self.get()

        for marker in ('LEAK-COMMAND', 'LEAK-MOUNT', 'LEAK-PORT', 'LEAK-NETWORK',
                       'LEAK-HOSTCONFIG', 'LEAK-LABEL', 'SUPERSECRET-LAB-TOKEN'):
            self.assertNotIn(marker.encode(), response.content)

    def test_response_is_not_cached(self):
        self.assertIn('no-store', self.get().headers['Cache-Control'])

    def test_backend_failure_is_502_without_leaking_internals(self):
        self.backend.side_effect = ContainerApiError(
            'connect unix:///run/user/1000/podman/podman.sock: permission denied')

        response = self.get()

        self.assertEqual(response.status_code, 502)
        body = json.loads(response.content)
        self.assertEqual(body['error'], 'container status unavailable')
        self.assertNotIn('podman.sock', response.content.decode())

    def test_unexpected_backend_error_is_also_contained(self):
        self.backend.side_effect = RuntimeError('/run/secrets/token boom')

        response = self.get()

        self.assertEqual(response.status_code, 502)
        self.assertNotIn('/run/secrets/token', response.content.decode())
