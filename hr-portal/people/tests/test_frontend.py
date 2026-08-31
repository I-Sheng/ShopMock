"""The HR portal's browser shell.

The PKCE fallback is the same one oe-dashboard uses on the lab's HTTP origin.
The rest of this module asserts that HR is a genuinely distinct application —
its own client, role, copy, denial states and visual identity — and not the
Finance portal or the IT console wearing different words.
"""
import json
import re
import subprocess
from pathlib import Path

from django.test import SimpleTestCase

APP = Path(__file__).resolve().parents[1]
PKCE = APP / 'assets' / 'pkce.js'
APP_JS = APP / 'assets' / 'app.js'
CSS = APP / 'assets' / 'app.css'
TEMPLATE = APP / 'templates' / 'people' / 'index.html'

class InsecureOriginPkceTests(SimpleTestCase):
    def test_s256_challenge_works_without_webcrypto_subtle(self):
        script = f"""
const pkce = require({json.dumps(str(PKCE))});
pkce.challengeFor('abc', {{}}).then(console.log).catch(e => {{ console.error(e); process.exit(1); }});
"""
        result = subprocess.run(
            ['node', '-e', script], text=True, capture_output=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout.strip(),
            'ungWv48Bz-pBQUDeXa4iI7ADYaOWF3qctBD_YfIAFa0',
        )


class DistinctHrFrontendTests(SimpleTestCase):
    def setUp(self):
        self.html = TEMPLATE.read_text()
        self.css = CSS.read_text()

    def test_the_page_is_titled_for_people_operations(self):
        self.assertRegex(self.html, r'<title>[^<]*(People|HR)[^<]*</title>')

    def test_the_page_does_not_present_itself_as_the_it_console(self):
        for borrowed in ('IT Operations', 'container', 'it-ops', 'it-operations'):
            self.assertNotIn(borrowed, self.html, borrowed)

    def test_the_page_does_not_present_itself_as_the_finance_portal(self):
        for borrowed in ('finance-portal', 'Finance portal', 'transactions',
                         'payment', 'revenue', 'wallet'):
            self.assertNotIn(borrowed, self.html, borrowed)

    def test_the_shell_carries_its_own_oidc_coordinates(self):
        self.assertIn('data-client-id="{{ client_id }}"', self.html)
        self.assertIn('data-required-role="{{ required_role }}"', self.html)

    def test_a_denial_state_exists_and_names_the_hr_role(self):
        denied = re.search(r'id="panel-denied".*?</section>', self.html, re.S)

        self.assertIsNotNone(denied)
        self.assertIn('{{ required_role }}', denied.group(0))

    def test_signed_out_and_error_states_exist(self):
        for panel in ('panel-signedout', 'panel-error', 'panel-loading'):
            self.assertIn(f'id="{panel}"', self.html, panel)

    def test_the_page_is_responsive_and_accessible(self):
        self.assertIn('name="viewport"', self.html)
        self.assertIn('Skip to content', self.html)
        self.assertIn('aria-live="polite"', self.html)
        self.assertIn('@media', self.css)

    def test_no_inline_script_or_style_is_used(self):
        self.assertNotRegex(self.html, r'<script(?![^>]*\ssrc=)[^>]*>')
        self.assertNotIn('<style', self.html)


class ThisPortalHasItsOwnPaletteTests(SimpleTestCase):
    """Half of "two genuinely distinct frontends" that this service can see.

    The other half — that the HR and Finance stylesheets share no colour value
    and are not the same document — compares two service trees, which a
    per-service test image does not contain. That comparison lives in
    scripts/verify-workforce-portals.sh, where the whole repository is present.
    """

    def test_the_palette_is_declared_as_named_tokens(self):
        css = CSS.read_text()

        for token in ('--canvas', '--ink', '--brand', '--good', '--warn', '--bad'):
            self.assertIn(token, css, token)

    def test_the_it_consoles_palette_is_not_reused(self):
        css = CSS.read_text()

        self.assertNotIn('#8b6ff0', css)   # oe-dashboard purple
        self.assertNotIn('#c3f53c', css)   # oe-dashboard lime

    def test_the_page_names_its_own_workforce_domain(self):
        self.assertIn('People', TEMPLATE.read_text())


class FrontendIsNotASecurityBoundaryTests(SimpleTestCase):
    def setUp(self):
        self.js = APP_JS.read_text()

    def test_the_browser_never_decides_authorization(self):
        self.assertIn('403', self.js)
        self.assertIn('401', self.js)

    def test_the_client_calls_only_its_own_api(self):
        for foreign in ('/finance/', '/oe/', '/api/checkout', '/api/orders',
                        '/api/customers'):
            self.assertNotIn(foreign, self.js, foreign)

    def test_pkce_s256_is_requested(self):
        self.assertIn("code_challenge_method: 'S256'", self.js)
