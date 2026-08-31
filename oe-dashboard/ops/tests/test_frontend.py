import json
import subprocess
from pathlib import Path

from django.test import SimpleTestCase


PKCE = Path(__file__).resolve().parents[1] / 'assets' / 'pkce.js'


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
