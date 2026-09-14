"""The one call this service makes against OpenSearch.

The properties pinned here are the ones that keep a read-only alert widget from
becoming a foothold in the SIEM: the request is a fixed, bounded `_search` and
nothing else, the admin credential travels in a header and never into a URL, a
log or an exception, TLS verification is on unless it was deliberately turned
off, and no OpenSearch failure mode escapes as anything but AlertApiError.
"""
import io
import json
import ssl
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from django.test import SimpleTestCase, override_settings

from ops.alerts import AlertApiError, empty_model
from ops.opensearch_client import fetch_alerts
from ops.tests.test_alerts import hit, response

PASSWORD = 'SUPERSECRET-LAB-TOKEN'


def settings_for(**overrides):
    base = {
        'OE_OPENSEARCH_URL': 'https://search:9200',
        'OE_OPENSEARCH_USER': 'admin',
        'OE_OPENSEARCH_PASSWORD': PASSWORD,
        'OE_ALERT_INDEX': 'wazuh-alerts-*',
        'OE_OPENSEARCH_TIMEOUT': 3.5,
        'OE_OPENSEARCH_VERIFY_TLS': True,
        'OE_ALERT_LIMIT': 25,
    }
    base.update(overrides)
    return base


class FakeResponse(io.BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


def reply(payload):
    return FakeResponse(json.dumps(payload).encode())


def index_not_found():
    body = json.dumps({
        'error': {
            'root_cause': [{'type': 'index_not_found_exception',
                            'reason': 'no such index [wazuh-alerts-*]',
                            'index': 'wazuh-alerts-*'}],
            'type': 'index_not_found_exception',
            'reason': 'no such index [wazuh-alerts-*]',
        },
        'status': 404,
    }).encode()
    return HTTPError('https://search:9200/wazuh-alerts-*/_search', 404,
                     'Not Found', {}, io.BytesIO(body))


@override_settings(**settings_for())
class RequestTests(SimpleTestCase):
    def call(self, payload=None):
        with patch('ops.opensearch_client.urlopen') as urlopen:
            urlopen.return_value = reply(
                response() if payload is None else payload)
            model = fetch_alerts()
        return model, urlopen.call_args

    def test_posts_a_search_against_the_configured_index_pattern(self):
        _, (args, kwargs) = self.call()
        request = args[0]

        self.assertEqual(request.get_method(), 'POST')
        self.assertTrue(
            request.full_url.startswith('https://search:9200/wazuh-alerts-%2A/_search'),
            request.full_url)
        self.assertEqual(request.headers['Content-type'], 'application/json')

    def test_a_missing_index_pattern_is_not_an_error_at_the_source(self):
        _, (args, _) = self.call()

        self.assertIn('ignore_unavailable=true', args[0].full_url)
        self.assertIn('allow_no_indices=true', args[0].full_url)

    def test_body_is_the_bounded_query_for_the_configured_limit(self):
        _, (args, _) = self.call()

        body = json.loads(args[0].data)
        self.assertEqual(body['size'], 25)
        self.assertEqual(body['sort'][0]['timestamp']['order'], 'desc')
        self.assertIn('severity', body['aggs'])

    @override_settings(**settings_for(OE_ALERT_LIMIT=5000))
    def test_a_misconfigured_limit_cannot_widen_the_query(self):
        _, (args, _) = self.call()

        self.assertEqual(json.loads(args[0].data)['size'], 100)

    def test_credentials_travel_in_a_basic_auth_header_not_the_url(self):
        _, (args, _) = self.call()
        request = args[0]

        self.assertEqual(request.headers['Authorization'], 'Basic YWRtaW46U1VQRVJTRUNSRVQtTEFCLVRPS0VO')
        self.assertNotIn(PASSWORD, request.full_url)

    @override_settings(**settings_for(OE_OPENSEARCH_USER='', OE_OPENSEARCH_PASSWORD=''))
    def test_no_credential_configured_means_no_authorization_header(self):
        _, (args, _) = self.call()

        self.assertNotIn('Authorization', args[0].headers)

    def test_the_call_is_bounded_by_the_configured_timeout(self):
        _, (_, kwargs) = self.call()

        self.assertEqual(kwargs['timeout'], 3.5)

    def test_tls_is_verified_by_default(self):
        _, (_, kwargs) = self.call()
        context = kwargs['context']

        self.assertIsInstance(context, ssl.SSLContext)
        self.assertTrue(context.check_hostname)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)

    @override_settings(**settings_for(OE_OPENSEARCH_VERIFY_TLS=False))
    def test_verification_can_be_turned_off_deliberately(self):
        _, (_, kwargs) = self.call()

        self.assertFalse(kwargs['context'].check_hostname)
        self.assertEqual(kwargs['context'].verify_mode, ssl.CERT_NONE)

    def test_only_the_allowlisted_model_comes_back(self):
        model, _ = self.call(response(hits=[hit()], total=1))

        self.assertEqual(sorted(model), ['alerts', 'summary'])
        self.assertNotIn('LEAK-FULLLOG', json.dumps(model))


@override_settings(**settings_for())
class FailureTests(SimpleTestCase):
    def call_with(self, side_effect):
        with patch('ops.opensearch_client.urlopen') as urlopen:
            urlopen.side_effect = side_effect
            return fetch_alerts()

    def test_missing_index_reads_as_an_empty_result(self):
        self.assertEqual(self.call_with(index_not_found()), empty_model())

    def test_any_other_http_status_is_a_contained_domain_error(self):
        error = HTTPError('https://search:9200/wazuh-alerts-*/_search', 403,
                          'Forbidden', {}, io.BytesIO(b'{"error":"no permissions"}'))

        with self.assertRaises(AlertApiError):
            self.call_with(error)

    def test_a_refused_connection_is_a_contained_domain_error(self):
        with self.assertRaises(AlertApiError):
            self.call_with(URLError(ConnectionRefusedError(111, 'Connection refused')))

    def test_a_tls_failure_is_a_contained_domain_error(self):
        with self.assertRaises(AlertApiError):
            self.call_with(ssl.SSLCertVerificationError('self-signed certificate'))

    def test_a_timeout_is_a_contained_domain_error(self):
        with self.assertRaises(AlertApiError):
            self.call_with(TimeoutError('timed out'))

    def test_an_unparseable_body_is_a_contained_domain_error(self):
        with patch('ops.opensearch_client.urlopen') as urlopen:
            urlopen.return_value = FakeResponse(b'<html>gateway</html>')
            with self.assertRaises(AlertApiError):
                fetch_alerts()

    def test_an_oversized_body_is_refused_rather_than_buffered(self):
        with patch('ops.opensearch_client.urlopen') as urlopen:
            urlopen.return_value = FakeResponse(b'{"hits":' + b'x' * (8 * 1024 * 1024))
            with self.assertRaises(AlertApiError):
                fetch_alerts()

    def test_the_domain_error_never_carries_the_credential(self):
        error = HTTPError('https://admin:%s@search:9200/_search' % PASSWORD, 401,
                          'Unauthorized', {}, io.BytesIO(b'{"error":"bad auth"}'))

        with self.assertRaises(AlertApiError) as caught:
            self.call_with(error)

        self.assertNotIn(PASSWORD, str(caught.exception))
        self.assertNotIn('admin:', str(caught.exception))
