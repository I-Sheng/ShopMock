"""Every SQL statement this service can issue.

They are module constants, never assembled from request input, and they are
collected in `ALL_STATEMENTS` so the test suite can assert properties of the
whole set: no `SELECT *`, no cardholder column, no schema but `finance`, and no
mutating verb. A statement that is not in that mapping does not exist.

The row limit is the only caller-influenced value and it never reaches the SQL
text — it is clamped to `FINANCE_MAX_ROWS` and passed as a bound parameter.

Isolation, restated: this module runs on the single connection declared in
finance_portal/settings.py, authenticated as `finance_portal`, which is granted
column-level SELECT on four finance tables and nothing else. Customer, order
and HR data are unreachable from here — not by convention, by credential.
"""
from django.conf import settings
from django.db import connections

TRANSACTIONS = """
    SELECT id, order_ref, amount_cents, kind, status, processed_at
      FROM finance.transactions
     ORDER BY processed_at DESC, id DESC
     LIMIT %s
"""

# `token` is deliberately absent, and the finance_portal role is not granted it.
PAYMENT_METHODS = """
    SELECT id, brand, last4, exp_month, exp_year
      FROM finance.payment_methods
     ORDER BY id
     LIMIT %s
"""

REVENUE = """
    SELECT day, gross_cents, refunds_cents
      FROM finance.revenue_daily
     ORDER BY day DESC
     LIMIT %s
"""

# Aggregate only: a per-wallet row would carry the customer reference that
# belongs to customer-db, so the grouping is the privacy control.
WALLET_TOTALS = """
    SELECT currency,
           count(*) AS wallets,
           coalesce(sum(balance_cents), 0) AS balance_cents
      FROM finance.wallets
     GROUP BY currency
     ORDER BY currency
"""

TRANSACTION_TOTALS = """
    SELECT kind,
           count(*) AS count,
           coalesce(sum(amount_cents), 0) AS amount_cents
      FROM finance.transactions
     GROUP BY kind
     ORDER BY kind
"""

ALL_STATEMENTS = {
    'transactions': TRANSACTIONS,
    'payment_methods': PAYMENT_METHODS,
    'revenue': REVENUE,
    'wallet_totals': WALLET_TOTALS,
    'transaction_totals': TRANSACTION_TOTALS,
}


def clamp_rows(requested):
    """Clamp a caller-supplied row count into [1, FINANCE_MAX_ROWS].

    Anything unparseable — absent, empty, or a hopeful `1;DROP TABLE` — falls
    back to the configured maximum rather than to the caller's intent. The view
    calls this before the query does, and the query calls it again: the clamp is
    cheap and being wrong here is not.
    """
    cap = settings.FINANCE_MAX_ROWS
    try:
        value = int(requested)
    except (TypeError, ValueError):
        return cap
    return max(1, min(value, cap))


def _rows(sql, params=None):
    with connections['default'].cursor() as cursor:
        cursor.execute(sql, params if params is not None else [])
        columns = [column[0] for column in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]


def transactions(limit):
    return _rows(TRANSACTIONS, [clamp_rows(limit)])


def payment_methods(limit):
    return _rows(PAYMENT_METHODS, [clamp_rows(limit)])


def revenue(limit):
    return _rows(REVENUE, [clamp_rows(limit)])


def wallet_totals():
    return _rows(WALLET_TOTALS)


def transaction_totals():
    return {
        row['kind']: {'count': row['count'], 'amount_cents': row['amount_cents']}
        for row in _rows(TRANSACTION_TOTALS)
    }
