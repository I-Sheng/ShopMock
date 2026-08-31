"""Safe finance serialization.

The single hardest requirement on this portal is that a payment token never
reaches a browser. Three independent layers enforce it and this module tests
the middle one:

  1. the database: `finance_portal` holds column-level SELECT that excludes
     `finance.payment_methods.token` (seed/finance-db/06_finance_portal_role.sh);
  2. the SQL: no statement in ledger/queries.py names the column (test_queries);
  3. serialization: every response body is built field-by-field from an
     allowlist, and emitting a forbidden field is a hard error, not a warning.

Layer 3 is what protects against the next person adding `SELECT *` — the
serializer refuses to pass a token through even when handed one.
"""
from datetime import date, datetime, timezone
from decimal import Decimal

from django.test import SimpleTestCase

from ledger import serializers


def payment_row(**overrides):
    row = {
        'id': 1,
        'brand': 'visa',
        'last4': '4242',
        'exp_month': 8,
        'exp_year': 2028,
    }
    row.update(overrides)
    return row


def transaction_row(**overrides):
    row = {
        'id': 7,
        'order_ref': 1,
        'amount_cents': 142800,
        'kind': 'charge',
        'status': 'settled',
        'processed_at': datetime(2026, 6, 27, 18, 30, tzinfo=timezone.utc),
    }
    row.update(overrides)
    return row


class PaymentMethodNeverLeaksTokensTests(SimpleTestCase):
    def test_a_masked_card_is_published(self):
        card = serializers.payment_method(payment_row())

        self.assertEqual(card['brand'], 'visa')
        self.assertEqual(card['last4'], '4242')
        self.assertEqual(card['masked'], '•••• •••• •••• 4242')
        self.assertEqual(card['expires'], '08/2028')

    def test_the_published_fields_are_exactly_the_allowlist(self):
        self.assertEqual(
            set(serializers.payment_method(payment_row())),
            {'id', 'brand', 'last4', 'masked', 'expires'},
        )

    def test_a_token_in_the_row_is_refused_outright(self):
        """A row that should never have been selected must not be serialized."""
        with self.assertRaises(serializers.UnsafeFieldError):
            serializers.payment_method(payment_row(token='tok_lab_ada_visa'))

    def test_a_pan_shaped_field_is_refused_outright(self):
        with self.assertRaises(serializers.UnsafeFieldError):
            serializers.payment_method(payment_row(card_number='4242424242424242'))

    def test_a_customer_reference_is_refused_outright(self):
        """Customer identity belongs to customer-db, not to a finance report."""
        with self.assertRaises(serializers.UnsafeFieldError):
            serializers.payment_method(payment_row(customer_ref=1))

    def test_unknown_extra_columns_are_dropped_rather_than_published(self):
        card = serializers.payment_method(payment_row(gateway_notes='internal'))

        self.assertNotIn('gateway_notes', card)

    def test_last4_is_never_widened_past_four_digits(self):
        card = serializers.payment_method(payment_row(last4='4242424242424242'))

        self.assertEqual(card['last4'], '4242')
        self.assertEqual(card['masked'], '•••• •••• •••• 4242')

    def test_a_missing_last4_degrades_to_a_fully_masked_card(self):
        card = serializers.payment_method(payment_row(last4=None))

        self.assertEqual(card['last4'], '')
        self.assertEqual(card['masked'], '•••• •••• •••• ••••')


class TransactionSerializationTests(SimpleTestCase):
    def test_the_published_fields_are_exactly_the_allowlist(self):
        self.assertEqual(
            set(serializers.transaction(transaction_row())),
            {'id', 'order_ref', 'amount_cents', 'kind', 'status', 'processed_at'},
        )

    def test_timestamps_are_iso_8601_strings(self):
        row = serializers.transaction(transaction_row())

        self.assertEqual(row['processed_at'], '2026-06-27T18:30:00+00:00')

    def test_a_null_order_reference_survives(self):
        self.assertIsNone(serializers.transaction(transaction_row(order_ref=None))['order_ref'])

    def test_a_customer_reference_is_refused_outright(self):
        with self.assertRaises(serializers.UnsafeFieldError):
            serializers.transaction(transaction_row(customer_ref=3))

    def test_a_token_is_refused_outright(self):
        with self.assertRaises(serializers.UnsafeFieldError):
            serializers.transaction(transaction_row(token='tok_lab_ada_visa'))


class RevenueSerializationTests(SimpleTestCase):
    def test_net_is_derived_rather_than_trusted(self):
        day = serializers.revenue_day({
            'day': date(2026, 6, 27), 'gross_cents': 167700, 'refunds_cents': 700,
            'net_cents': 999999,
        })

        self.assertEqual(day['net_cents'], 167000)

    def test_dates_are_iso_8601_strings(self):
        day = serializers.revenue_day({
            'day': date(2026, 6, 27), 'gross_cents': 1, 'refunds_cents': 0})

        self.assertEqual(day['day'], '2026-06-27')

    def test_the_published_fields_are_exactly_the_allowlist(self):
        day = serializers.revenue_day({
            'day': date(2026, 6, 27), 'gross_cents': 1, 'refunds_cents': 0})

        self.assertEqual(set(day), {'day', 'gross_cents', 'refunds_cents', 'net_cents'})


class WalletTotalsAreAggregateOnlyTests(SimpleTestCase):
    def test_totals_are_reported_per_currency_without_identifying_anyone(self):
        totals = serializers.wallet_total(
            {'currency': 'USD', 'wallets': 2, 'balance_cents': Decimal('5000')})

        self.assertEqual(totals, {'currency': 'USD', 'wallets': 2, 'balance_cents': 5000})

    def test_a_customer_reference_is_refused_outright(self):
        with self.assertRaises(serializers.UnsafeFieldError):
            serializers.wallet_total(
                {'currency': 'USD', 'wallets': 1, 'balance_cents': 1, 'customer_ref': 1})


class ForbiddenFieldPolicyTests(SimpleTestCase):
    def test_the_denylist_covers_the_cardholder_data_vocabulary(self):
        for field in ('token', 'pan', 'card_number', 'cardnumber', 'cvv', 'cvc',
                      'customer_ref', 'customer_id', 'email', 'ssn', 'iban',
                      'account_number'):
            self.assertIn(field, serializers.FORBIDDEN_FIELDS, field)

    def test_the_denylist_is_matched_case_insensitively(self):
        with self.assertRaises(serializers.UnsafeFieldError):
            serializers.payment_method(payment_row(TOKEN='tok_lab_ada_visa'))

    def test_serializing_a_whole_list_applies_the_same_policy(self):
        with self.assertRaises(serializers.UnsafeFieldError):
            serializers.many(serializers.payment_method,
                             [payment_row(), payment_row(token='tok_x')])

    def test_serializing_a_clean_list_returns_every_row(self):
        rows = serializers.many(serializers.payment_method,
                                [payment_row(id=1), payment_row(id=2)])

        self.assertEqual([r['id'] for r in rows], [1, 2])
