"""Response bodies for the Finance portal, built field-by-field.

Nothing here reads a row and hands it on. Each serializer names the fields it
publishes, so a column added to finance-db tomorrow cannot appear in an API
response by accident — the opposite of `JsonResponse(dict(row))`.

On top of the allowlist there is a denylist, and it is deliberately louder:
if a row arrives carrying a payment token, a PAN-shaped field or a customer
reference, serialization raises. Dropping such a field silently would leave the
next `SELECT *` undetected; failing the request makes the mistake impossible to
miss and still publishes nothing. The view turns that into a 500 with no detail.

The masked card is what Finance actually needs — brand, last four, expiry — and
is all this portal is granted at the database level: `finance_portal` holds
column-level SELECT on `finance.payment_methods` that excludes `token`.
"""
from decimal import Decimal

# Cardholder data, customer identity and anything that looks like either. The
# comparison is case-insensitive.
FORBIDDEN_FIELDS = frozenset({
    'token', 'payment_token', 'pan', 'card_number', 'cardnumber', 'card',
    'cvv', 'cvc', 'security_code',
    'customer_ref', 'customer_id', 'customer', 'email', 'name', 'phone',
    'address', 'ssn', 'iban', 'account_number', 'routing_number',
})

_MASK = '•••• •••• •••• '


class UnsafeFieldError(RuntimeError):
    """A row carried a field this service must never publish."""


def _guard(row):
    for key in row:
        if str(key).lower() in FORBIDDEN_FIELDS:
            # The value is deliberately absent from the message: it may be the
            # very token this exception exists to contain.
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


def payment_method(row):
    """A card as Finance may see it: masked, expiring, never tokenized."""
    _guard(row)
    last4 = str(row.get('last4') or '')[-4:]
    month = _int(row.get('exp_month'))
    year = _int(row.get('exp_year'))
    return {
        'id': _int(row.get('id')),
        'brand': str(row.get('brand') or ''),
        'last4': last4,
        'masked': _MASK + (last4 or '••••'),
        'expires': f'{month:02d}/{year:04d}',
    }


def transaction(row):
    _guard(row)
    order_ref = row.get('order_ref')
    return {
        'id': _int(row.get('id')),
        'order_ref': None if order_ref is None else _int(order_ref),
        'amount_cents': _int(row.get('amount_cents')),
        'kind': str(row.get('kind') or ''),
        'status': str(row.get('status') or ''),
        'processed_at': _iso(row.get('processed_at')),
    }


def revenue_day(row):
    """`net_cents` is derived here rather than trusted from the row."""
    _guard(row)
    gross = _int(row.get('gross_cents'))
    refunds = _int(row.get('refunds_cents'))
    return {
        'day': _iso(row.get('day')),
        'gross_cents': gross,
        'refunds_cents': refunds,
        'net_cents': gross - refunds,
    }


def wallet_total(row):
    """Wallet balances are published per currency only — never per customer."""
    _guard(row)
    return {
        'currency': str(row.get('currency') or ''),
        'wallets': _int(row.get('wallets')),
        'balance_cents': _int(row.get('balance_cents')),
    }


def transaction_totals(totals):
    """Roll-up counts keyed by transaction kind (charge / refund / payout)."""
    return {
        str(kind): {
            'count': _int((values or {}).get('count')),
            'amount_cents': _int((values or {}).get('amount_cents')),
        }
        for kind, values in (totals or {}).items()
    }
