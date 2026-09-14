"""Deployment defaults for the alert source.

The module is reloaded against a controlled environment rather than asserting
on `django.conf.settings`, because what matters here is what a *container* gets
when an operator forgets to set something — and the answer has to be the safe
one, not whatever the test runner happens to export.
"""
import importlib
import os
from unittest.mock import patch

from django.test import SimpleTestCase

from oe_dashboard import settings as settings_module


class AlertSettingsDefaultTests(SimpleTestCase):
    def loaded(self, **env):
        with patch.dict(os.environ, env, clear=True):
            return importlib.reload(settings_module)

    def tearDown(self):
        # Leave the module as the live process configured it.
        importlib.reload(settings_module)

    def test_tls_verification_is_on_unless_deliberately_turned_off(self):
        self.assertTrue(self.loaded().OE_OPENSEARCH_VERIFY_TLS)

    def test_a_blank_override_does_not_silently_disable_verification(self):
        self.assertTrue(self.loaded(OE_OPENSEARCH_VERIFY_TLS='').OE_OPENSEARCH_VERIFY_TLS)
        self.assertTrue(self.loaded(OE_OPENSEARCH_VERIFY_TLS='   ').OE_OPENSEARCH_VERIFY_TLS)

    def test_verification_can_be_opted_out_of_explicitly(self):
        for value in ('0', 'false', 'FALSE', 'no', 'off'):
            self.assertFalse(
                self.loaded(OE_OPENSEARCH_VERIFY_TLS=value).OE_OPENSEARCH_VERIFY_TLS,
                value)

    def test_no_credential_is_baked_into_the_image(self):
        loaded = self.loaded()

        self.assertEqual(loaded.OE_OPENSEARCH_USER, '')
        self.assertEqual(loaded.OE_OPENSEARCH_PASSWORD, '')

    def test_defaults_point_at_the_lab_alert_store(self):
        loaded = self.loaded()

        self.assertEqual(loaded.OE_OPENSEARCH_URL, 'https://search:9200')
        self.assertEqual(loaded.OE_ALERT_INDEX, 'wazuh-alerts-*')
        self.assertEqual(loaded.OE_ALERT_LIMIT, 25)
        self.assertEqual(loaded.OE_OPENSEARCH_TIMEOUT, 5.0)

    def test_every_alert_setting_is_overridable_from_the_environment(self):
        loaded = self.loaded(
            OE_OPENSEARCH_URL='https://siem.example:9200',
            OE_OPENSEARCH_USER='oe-reader',
            OE_OPENSEARCH_PASSWORD='from-the-environment-only',
            OE_ALERT_INDEX='wazuh-alerts-4.x-*',
            OE_OPENSEARCH_TIMEOUT='2.5',
            OE_ALERT_LIMIT='10',
        )

        self.assertEqual(loaded.OE_OPENSEARCH_URL, 'https://siem.example:9200')
        self.assertEqual(loaded.OE_OPENSEARCH_USER, 'oe-reader')
        self.assertEqual(loaded.OE_OPENSEARCH_PASSWORD, 'from-the-environment-only')
        self.assertEqual(loaded.OE_ALERT_INDEX, 'wazuh-alerts-4.x-*')
        self.assertEqual(loaded.OE_OPENSEARCH_TIMEOUT, 2.5)
        self.assertEqual(loaded.OE_ALERT_LIMIT, 10)
