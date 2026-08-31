"""Response bodies for the HR portal, built field-by-field.

The allowlist works exactly as the Finance portal's does: each serializer names
what it publishes, so a column added to hr-db tomorrow cannot reach a browser
by accident.

What differs is the denylist. Staff names and work contact details are the
point of a staff directory and are published; individual pay, government
identifiers, home addresses and bank details are not, and a row carrying one
raises rather than being quietly trimmed. Payroll is published only as a
departmental total, where it answers a budgeting question without exposing
what any one colleague earns.

Cardholder vocabulary is on the denylist too. Nothing in hr-db has such a
column and this service holds no credential for finance-db — but if a value
shaped like one ever appeared here, publishing it is the one outcome worth
failing the request over.
"""
from decimal import Decimal

FORBIDDEN_FIELDS = frozenset({
    'base_salary_cents', 'salary', 'salary_cents', 'annual_salary', 'pay_rate',
    'compensation', 'bonus_cents',
    'ssn', 'social_security_number', 'national_id', 'passport_number',
    'date_of_birth', 'dob', 'home_address', 'personal_email', 'personal_phone',
    'bank_account', 'account_number', 'routing_number', 'iban',
    'token', 'pan', 'card_number', 'cardnumber', 'cvv',
})


class UnsafeFieldError(RuntimeError):
    """A row carried a field this service must never publish."""


def _guard(row):
    for key in row:
        if str(key).lower() in FORBIDDEN_FIELDS:
            raise UnsafeFieldError(f'refusing to serialize forbidden field {key!r}')


def _int(value, default=0):
    if isinstance(value, bool) or value is None:
        return default
    if isinstance(value, (int, Decimal, float)):
        return int(value)
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return default


def _iso(value):
    return value.isoformat() if hasattr(value, 'isoformat') else value


def many(serializer, rows):
    """Apply a serializer to every row. One bad row fails the whole response."""
    return [serializer(row) for row in rows]


def employee(row):
    """The staff directory entry: who someone is and what they do, not what
    they earn."""
    _guard(row)
    return {
        'id': _int(row.get('id')),
        'employee_no': str(row.get('employee_no') or ''),
        'first_name': str(row.get('first_name') or ''),
        'last_name': str(row.get('last_name') or ''),
        'work_email': str(row.get('work_email') or ''),
        'job_title': str(row.get('job_title') or ''),
        'department': str(row.get('department') or ''),
        'employment_type': str(row.get('employment_type') or ''),
        'status': str(row.get('status') or ''),
        'hired_on': _iso(row.get('hired_on')),
    }


def department(row):
    """Payroll appears here — as a departmental total, never per person."""
    _guard(row)
    return {
        'id': _int(row.get('id')),
        'name': str(row.get('name') or ''),
        'cost_center': str(row.get('cost_center') or ''),
        'headcount': _int(row.get('headcount')),
        'headcount_budget': _int(row.get('headcount_budget')),
        'payroll_cents': _int(row.get('payroll_cents')),
    }


def leave_request(row):
    _guard(row)
    return {
        'id': _int(row.get('id')),
        'employee_no': str(row.get('employee_no') or ''),
        'first_name': str(row.get('first_name') or ''),
        'last_name': str(row.get('last_name') or ''),
        'kind': str(row.get('kind') or ''),
        'starts_on': _iso(row.get('starts_on')),
        'ends_on': _iso(row.get('ends_on')),
        'days': _int(row.get('days')),
        'status': str(row.get('status') or ''),
    }


def headcount(counts):
    """Roll-up counts keyed by employment status (active / on_leave / left)."""
    return {str(status): _int(value) for status, value in (counts or {}).items()}
