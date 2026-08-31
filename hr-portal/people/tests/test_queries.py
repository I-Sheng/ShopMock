"""Row mapping and limit handling for the HR roster queries.

The schema-boundary properties of these statements live in test_isolation.py;
this module covers the mechanics: cursor rows become dicts, and a caller-supplied
row limit is clamped rather than trusted.
"""
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase, override_settings

from people import queries


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
            ['id', 'employee_no', 'first_name', 'last_name'],
            [(1, 'E-0001', 'Rosa', 'Marek')])

        rows = self._run(cursor, lambda: queries.employees(10))

        self.assertEqual(rows[0]['employee_no'], 'E-0001')
        self.assertEqual(rows[0]['last_name'], 'Marek')

    def test_departments_need_no_limit_parameter(self):
        cursor = self._cursor(['id', 'name'], [(1, 'Engineering')])

        rows = self._run(cursor, lambda: queries.departments())

        self.assertEqual(rows[0]['name'], 'Engineering')

    @override_settings(HR_MAX_ROWS=25)
    def test_the_requested_limit_is_capped_by_the_configured_maximum(self):
        cursor = self._cursor(['id'], [])

        self._run(cursor, lambda: queries.employees(10_000))

        self.assertEqual(cursor.execute.call_args[0][1], [25])

    @override_settings(HR_MAX_ROWS=25)
    def test_a_nonsense_limit_falls_back_to_the_maximum(self):
        cursor = self._cursor(['id'], [])

        self._run(cursor, lambda: queries.employees('; DROP TABLE'))

        self.assertEqual(cursor.execute.call_args[0][1], [25])

    def test_a_limit_below_one_is_clamped(self):
        cursor = self._cursor(['id'], [])

        self._run(cursor, lambda: queries.leave_requests(-5))

        self.assertEqual(cursor.execute.call_args[0][1], [1])

    def test_headcount_is_returned_as_a_status_keyed_mapping(self):
        cursor = self._cursor(['status', 'headcount'],
                              [('active', 7), ('on_leave', 1)])

        self.assertEqual(self._run(cursor, queries.headcount),
                         {'active': 7, 'on_leave': 1})
