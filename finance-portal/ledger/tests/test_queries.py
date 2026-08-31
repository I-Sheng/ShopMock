"""Every statement this service can run, and the boundary they must respect.

`queries.ALL_STATEMENTS` is the complete set of SQL this portal issues. Reading
it as data lets these tests assert properties the reviewer would otherwise have
to re-check by hand on every change: no `SELECT *`, no cardholder columns, and
nothing outside the `finance` schema.

Cross-database isolation is not enforced here by hope — the `finance_portal`
login has no credential for customer-db, orders-db or hr-db and this service
declares a single connection — but a query that *named* another domain's table
would still be a design error worth failing on.
"""
import re
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase, override_settings

from ledger import queries

_FOREIGN_SCHEMAS = ('commerce.', 'sales.', 'catalog.', 'seller.', 'ops.', 'hr.',
                    'pg_catalog.', 'information_schema.')


class StatementInventoryTests(SimpleTestCase):
    def test_every_statement_is_registered_for_review(self):
        self.assertEqual(
            set(queries.ALL_STATEMENTS),
            {'transactions', 'payment_methods', 'revenue', 'wallet_totals',
             'transaction_totals'},
        )


class NoCardholderDataIsEverSelectedTests(SimpleTestCase):
    def test_no_statement_names_the_payment_token_column(self):
        for name, sql in queries.ALL_STATEMENTS.items():
            self.assertNotRegex(sql, r'(?i)\btoken\b', name)

    def test_no_statement_names_a_customer_reference(self):
        for name, sql in queries.ALL_STATEMENTS.items():
            self.assertNotRegex(sql, r'(?i)\bcustomer_ref\b', name)

    def test_no_statement_uses_a_wildcard_projection(self):
        """`SELECT *` would publish whatever column is added next."""
        for name, sql in queries.ALL_STATEMENTS.items():
            self.assertNotRegex(sql, r'(?i)select\s+\*', name)
            self.assertNotRegex(sql, r'(?i)select\s+\w+\.\*', name)


class StaysInsideTheFinanceSchemaTests(SimpleTestCase):
    def test_no_statement_reaches_into_another_domain(self):
        for name, sql in queries.ALL_STATEMENTS.items():
            for schema in _FOREIGN_SCHEMAS:
                self.assertNotIn(schema, sql.lower(), f'{name} references {schema}')

    def test_every_statement_reads_the_finance_schema(self):
        for name, sql in queries.ALL_STATEMENTS.items():
            self.assertIn('finance.', sql.lower(), name)


class ReadOnlyTests(SimpleTestCase):
    def test_no_statement_mutates_anything(self):
        forbidden = ('insert ', 'update ', 'delete ', 'drop ', 'alter ', 'grant ',
                     'truncate ', 'create ', 'copy ')
        for name, sql in queries.ALL_STATEMENTS.items():
            lowered = re.sub(r'\s+', ' ', sql.lower())
            for verb in forbidden:
                self.assertNotIn(verb, lowered, f'{name} contains {verb.strip()}')

    def test_row_limits_are_bound_parameters_not_interpolated(self):
        for name in ('transactions', 'payment_methods', 'revenue'):
            self.assertIn('%s', queries.ALL_STATEMENTS[name], name)


class RowMappingTests(SimpleTestCase):
    """The cursor is faked: these tests are about mapping, not about Postgres."""

    def _cursor(self, columns, rows):
        cursor = MagicMock()
        cursor.description = [(c,) + (None,) * 6 for c in columns]
        cursor.fetchall.return_value = rows
        cursor.__enter__.return_value = cursor
        cursor.__exit__.return_value = False
        return cursor

    def _run(self, cursor, call):
        connection = MagicMock()
        connection.cursor.return_value = cursor
        with patch.object(queries, 'connections', {'default': connection}):
            return call()

    def test_rows_become_dicts_keyed_by_column_name(self):
        cursor = self._cursor(
            ['id', 'order_ref', 'amount_cents', 'kind', 'status', 'processed_at'],
            [(1, 2, 300, 'charge', 'settled', None)])

        rows = self._run(cursor, lambda: queries.transactions(10))

        self.assertEqual(rows[0]['amount_cents'], 300)
        self.assertEqual(rows[0]['kind'], 'charge')

    @override_settings(FINANCE_MAX_ROWS=25)
    def test_the_requested_limit_is_capped_by_the_configured_maximum(self):
        cursor = self._cursor(['id'], [])

        self._run(cursor, lambda: queries.transactions(10_000))

        self.assertEqual(cursor.execute.call_args[0][1], [25])

    @override_settings(FINANCE_MAX_ROWS=25)
    def test_a_nonsense_limit_falls_back_to_the_maximum(self):
        cursor = self._cursor(['id'], [])

        self._run(cursor, lambda: queries.transactions('; DROP TABLE'))

        self.assertEqual(cursor.execute.call_args[0][1], [25])

    def test_a_limit_below_one_is_clamped(self):
        cursor = self._cursor(['id'], [])

        self._run(cursor, lambda: queries.transactions(0))

        self.assertEqual(cursor.execute.call_args[0][1], [1])
