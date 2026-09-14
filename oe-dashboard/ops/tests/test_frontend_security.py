"""The Security Monitoring section of the console shell.

These are static assertions — no browser, no Node — over the three things the
CSP and the data boundary actually depend on: the shell carries the section's
structure (so nothing has to be injected as markup at runtime), every script is
an external same-origin file, and the page itself never learns where the alert
store is or what credential reaches it.
"""
import re

from django.test import Client, SimpleTestCase, override_settings

from ops.tests.test_api import settings_for

SECURITY_SETTINGS = settings_for(
    OE_OPENSEARCH_URL='https://search:9200',
    OE_OPENSEARCH_USER='admin',
    OE_OPENSEARCH_PASSWORD='SUPERSECRET-LAB-TOKEN',
    OE_ALERT_INDEX='wazuh-alerts-*',
    OE_ALERT_LIMIT=25,
)

# <script> with no src= attribute, i.e. an inline block the CSP would have to
# allow with 'unsafe-inline'.
INLINE_SCRIPT = re.compile(r'<script(?![^>]*\ssrc=)[^>]*>', re.IGNORECASE)
SCRIPT_SRC = re.compile(r'<script[^>]*\ssrc="([^"]+)"', re.IGNORECASE)


def tag_with_id(html, element_id):
    match = re.search(r'<[^>]*\bid="%s"[^>]*>' % re.escape(element_id), html)
    return match.group(0) if match else ''


@override_settings(**SECURITY_SETTINGS)
class SecuritySectionMarkupTests(SimpleTestCase):
    def setUp(self):
        self.client = Client()
        self.html = self.client.get('/').content.decode()

    def test_shell_carries_the_security_section(self):
        self.assertIn('id="security"', self.html)
        self.assertIn('Security monitoring', self.html)

    def test_section_is_labelled_for_assistive_technology(self):
        section = tag_with_id(self.html, 'security')

        self.assertIn('aria-labelledby="security-title"', section)
        self.assertIn('id="security-title"', self.html)

    def test_section_has_the_containers_it_renders_into(self):
        for element_id in ('sec-tiles', 'alert-rows', 'alerts-updated'):
            self.assertIn('id="%s"' % element_id, self.html)

    def test_empty_state_is_present_announced_and_starts_hidden(self):
        empty = tag_with_id(self.html, 'alerts-empty')

        self.assertTrue(empty, 'no #alerts-empty element in the shell')
        self.assertIn('role="status"', empty)
        self.assertIn('hidden', empty)
        # The operator has to be told the difference between "quiet" and "broken".
        self.assertIn('No alerts', self.html)

    def test_unavailable_state_is_distinct_from_the_empty_state(self):
        unavailable = tag_with_id(self.html, 'alerts-unavailable')

        self.assertTrue(unavailable, 'no #alerts-unavailable element in the shell')
        self.assertIn('role="status"', unavailable)
        self.assertIn('hidden', unavailable)

    def test_alert_table_is_a_real_accessible_table(self):
        table = self.html.split('id="alerts"', 1)[-1]

        self.assertIn('<caption', table)
        for column in ('Time', 'Level', 'Rule', 'Agent', 'Location'):
            self.assertIn('<th scope="col">%s</th>' % column, table)

    def test_container_status_ui_is_untouched(self):
        for element_id in ('tiles', 'rows', 'verdict', 'updated', 'containers'):
            self.assertIn('id="%s"' % element_id, self.html)


@override_settings(**SECURITY_SETTINGS)
class SecurityShellBoundaryTests(SimpleTestCase):
    def setUp(self):
        self.client = Client()
        self.response = self.client.get('/')
        self.html = self.response.content.decode()

    def test_the_page_never_learns_the_alert_store_or_its_credential(self):
        for secret in ('SUPERSECRET-LAB-TOKEN', 'search:9200',
                       'wazuh-alerts-*', 'OE_OPENSEARCH'):
            self.assertNotIn(secret, self.html)

    def test_the_shell_still_needs_no_inline_script(self):
        self.assertEqual(INLINE_SCRIPT.findall(self.html), [])

    def test_every_script_is_a_same_origin_file(self):
        sources = SCRIPT_SRC.findall(self.html)

        self.assertEqual(sorted(sources), ['app.js', 'pkce.js'])

    def test_csp_still_confines_fetches_to_this_origin(self):
        csp = self.response.headers['Content-Security-Policy']

        self.assertIn("connect-src 'self'", csp)
        self.assertIn("script-src 'self'", csp)
        self.assertNotIn('unsafe-inline', csp)


class SecurityScriptTests(SimpleTestCase):
    def setUp(self):
        self.client = Client()
        self.js = self.client.get('/app.js').content.decode()

    def test_the_client_calls_the_alerts_endpoint(self):
        self.assertIn('api/security/alerts', self.js)

    def test_the_alerts_call_reuses_the_same_bearer_token(self):
        self.assertIn("freshAccessToken", self.js)
        self.assertEqual(self.js.count("Authorization: 'Bearer ' + token"), 2)

    def test_alert_text_is_never_written_as_markup(self):
        # Alert descriptions and locations are attacker-influenced log content.
        for sink in ('innerHTML', 'outerHTML', 'insertAdjacentHTML',
                     'document.write', 'eval('):
            self.assertNotIn(sink, self.js)

    def test_a_failing_alert_source_does_not_take_down_container_status(self):
        # The container fetch and the alert fetch are separate settlements.
        self.assertIn('alertsRequest', self.js)
        self.assertIn('statusRequest', self.js)
