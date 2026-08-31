"""HR data isolation.

The requirement is blunt: HR endpoints must never query finance. These tests
assert the structure that makes that true rather than merely intended —

  * exactly one database connection is declared, and it is hr-db;
  * no source file in this service names another domain's database, schema,
    table or service credential;
  * every statement the service can issue reads only the `hr` schema;
  * the most sensitive HR column (individual pay) is not selected by the roster
    query at all, and is not serializable.

They read this service's own sources, so they stay true as the code changes.
"""
import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

from people import queries, serializers

SERVICE_ROOT = Path(__file__).resolve().parents[2]
SOURCES = sorted(
    p for p in SERVICE_ROOT.rglob('*.py')
    if '__pycache__' not in p.parts and 'tests' not in p.parts
)

# Every other datastore in the ShopMock stack, by hostname, database name and
# schema. None of them may appear anywhere in this service.
FOREIGN_DATASTORES = (
    'finance-db', 'customer-db', 'orders-db', 'catalog-db',
    'finance.', 'commerce.', 'sales.', 'catalog.', 'seller.', 'ops.',
    'payment_methods', 'transactions', 'wallets', 'revenue_daily',
    'INTERNAL_BACKEND_DB_PASSWORD', 'SELLER_BACKEND_DB_PASSWORD',
    'FINANCE_PORTAL_DB_PASSWORD',
)


class SourcesAreDiscoverableTests(SimpleTestCase):
    def test_the_isolation_tests_actually_read_the_service(self):
        names = {p.name for p in SOURCES}

        self.assertIn('settings.py', names)
        self.assertIn('queries.py', names)
        self.assertIn('views.py', names)


class OneConnectionAndItIsHrTests(SimpleTestCase):
    def test_exactly_one_database_is_configured(self):
        self.assertEqual(list(settings.DATABASES), ['default'])

    def test_that_database_is_hr_db(self):
        default = settings.DATABASES['default']

        self.assertEqual(default['HOST'], 'hr-db')
        self.assertEqual(default['NAME'], 'hr')

    def test_it_connects_as_the_least_privilege_hr_role(self):
        self.assertEqual(settings.DATABASES['default']['USER'], 'hr_portal')

    def test_no_finance_credential_is_read_from_the_environment(self):
        """Prose may explain the boundary; executable settings may not cross it."""
        code = _strip_prose((SERVICE_ROOT / 'hr_portal' / 'settings.py').read_text())

        self.assertNotIn('finance', code.lower())


class NoFinanceReachabilityTests(SimpleTestCase):
    def test_no_source_file_names_another_domains_datastore(self):
        for path in SOURCES:
            text = path.read_text()
            # The settings docstring names the databases this service is
            # deliberately kept away from; strip comments and docstrings first.
            code = _strip_prose(text)
            for foreign in FOREIGN_DATASTORES:
                self.assertNotIn(
                    foreign, code,
                    f'{path.relative_to(SERVICE_ROOT)} references {foreign}')

    def test_no_source_file_imports_a_finance_module(self):
        for path in SOURCES:
            self.assertNotRegex(
                _strip_prose(path.read_text()),
                r'(?m)^\s*(from|import)\s+(ledger|finance_portal)\b',
                str(path.relative_to(SERVICE_ROOT)))


class EveryStatementStaysInTheHrSchemaTests(SimpleTestCase):
    def test_every_statement_is_registered_for_review(self):
        self.assertEqual(
            set(queries.ALL_STATEMENTS),
            {'employees', 'departments', 'leave_requests', 'headcount'},
        )

    def test_every_statement_reads_the_hr_schema(self):
        for name, sql in queries.ALL_STATEMENTS.items():
            self.assertIn('hr.', sql.lower(), name)

    def test_no_statement_reaches_into_another_schema(self):
        for name, sql in queries.ALL_STATEMENTS.items():
            for schema in ('finance.', 'commerce.', 'sales.', 'catalog.',
                           'seller.', 'ops.', 'pg_catalog.', 'information_schema.'):
                self.assertNotIn(schema, sql.lower(), f'{name} references {schema}')

    def test_no_statement_uses_a_wildcard_projection(self):
        for name, sql in queries.ALL_STATEMENTS.items():
            self.assertNotRegex(sql, r'(?i)select\s+\*', name)
            self.assertNotRegex(sql, r'(?i)select\s+\w+\.\*', name)

    def test_no_statement_mutates_anything(self):
        for name, sql in queries.ALL_STATEMENTS.items():
            lowered = re.sub(r'\s+', ' ', sql.lower())
            for verb in ('insert ', 'update ', 'delete ', 'drop ', 'alter ',
                         'grant ', 'truncate ', 'create ', 'copy '):
                self.assertNotIn(verb, lowered, f'{name} contains {verb.strip()}')


class IndividualPayIsNotPublishedTests(SimpleTestCase):
    """Payroll totals are an HR figure; one colleague's salary is not."""

    def test_the_roster_query_does_not_select_individual_pay(self):
        self.assertNotRegex(
            queries.ALL_STATEMENTS['employees'], r'(?i)salary')

    def test_the_employee_serializer_refuses_a_salary_column(self):
        with self.assertRaises(serializers.UnsafeFieldError):
            serializers.employee({'id': 1, 'base_salary_cents': 900000})

    def test_the_denylist_covers_the_sensitive_hr_vocabulary(self):
        for field in ('base_salary_cents', 'salary', 'ssn', 'national_id',
                      'date_of_birth', 'home_address', 'bank_account', 'iban',
                      'token', 'card_number'):
            self.assertIn(field, serializers.FORBIDDEN_FIELDS, field)

    def test_departments_publish_payroll_only_in_aggregate(self):
        row = serializers.department({
            'id': 1, 'name': 'Finance', 'cost_center': 'CC-100',
            'headcount_budget': 6, 'headcount': 3, 'payroll_cents': 2_700_000})

        self.assertEqual(row['payroll_cents'], 2_700_000)
        self.assertNotIn('base_salary_cents', row)


def _strip_prose(text):
    """Drop comments and triple-quoted strings so prose can explain a boundary
    that the code itself must never cross."""
    text = re.sub(r'(?s)""".*?"""', '', text)
    text = re.sub(r"(?s)'''.*?'''", '', text)
    return re.sub(r'(?m)#.*$', '', text)
