"""Checkout orchestration — all checkout SQL lives here.

Replaces the storefront's per-step PostgREST RPC calls with one endpoint that
runs the whole flow: customer upsert -> address save -> tokenized payment
method save -> order + items insert -> payment transaction. Every statement is
parameterized (no string-built SQL). Cross-database steps mirror the previous
saga shape: order placement is atomic within orders-db; the finance charge is
best-effort, as a true cross-DB transaction is impossible under
database-per-service.

Card rule (finance schema: never store PANs): the browser tokenizes the card
and sends only brand/last4/token/expiry; anything PAN-shaped is rejected here.
"""
import json
import re

from django.db import connections, transaction
from django.http import JsonResponse
from django.views.decorators.http import require_POST

from .auth import AuthError, require_customer


def healthz(request):
    return JsonResponse({'status': 'ok'})


def _bad(msg, status=400):
    return JsonResponse({'error': msg}, status=status)


def _validate(body):
    addr = body.get('address') or {}
    pay = body.get('payment') or {}
    items = body.get('items')

    for field in ('line1', 'city', 'country'):
        if not str(addr.get(field) or '').strip():
            return f'address.{field} is required'

    if not re.fullmatch(r'[0-9]{4}', str(pay.get('last4') or '')):
        return 'payment.last4 must be exactly 4 digits'
    token = str(pay.get('token') or '')
    if not token or re.fullmatch(r'[0-9 -]{12,}', token):
        return 'payment.token must be an opaque gateway token, never a card number'
    try:
        month, year = int(pay.get('exp_month')), int(pay.get('exp_year'))
    except (TypeError, ValueError):
        return 'payment expiry is invalid'
    if not (1 <= month <= 12 and 2020 <= year <= 2100):
        return 'payment expiry is invalid'

    if not isinstance(items, list) or not items:
        return 'items must be a non-empty array'
    for it in items:
        if not str(it.get('product_sku') or ''):
            return 'every item needs a product_sku'
        try:
            if int(it.get('qty')) <= 0 or int(it.get('unit_price_cents')) < 0:
                return 'item qty/price out of range'
        except (TypeError, ValueError):
            return 'item qty/price must be integers'
    return None


def _ensure_customer(claims):
    """Same logic as the old commerce.ensure_customer() RPC, keyed on JWT sub."""
    sub = claims['sub']
    email = claims.get('email') or claims.get('preferred_username') or f'{sub}@lab.local'
    name = (
        claims.get('name')
        or ' '.join(filter(None, [claims.get('given_name'), claims.get('family_name')]))
        or claims.get('preferred_username')
        or 'Unknown'
    )
    with transaction.atomic(using='default'):
        with connections['default'].cursor() as cur:
            cur.execute(
                'SELECT id FROM commerce.customers WHERE keycloak_sub = %s', [sub])
            row = cur.fetchone()
            if row:
                return row[0]
            cur.execute(
                """
                INSERT INTO commerce.customers (keycloak_sub, email, full_name)
                VALUES (%s, %s, %s)
                ON CONFLICT (email) DO UPDATE SET keycloak_sub = EXCLUDED.keycloak_sub
                RETURNING id
                """,
                [sub, email, name])
            cid = cur.fetchone()[0]
            cur.execute(
                'INSERT INTO commerce.accounts (customer_id) VALUES (%s)', [cid])
            return cid


def _save_address(cid, addr):
    """One shipping address per customer, updated in place on repeat checkouts."""
    params = [
        addr['line1'].strip(), addr['city'].strip(),
        (addr.get('region') or '').strip() or None,
        (addr.get('postal') or '').strip() or None,
        addr['country'].strip(),
    ]
    with transaction.atomic(using='default'):
        with connections['default'].cursor() as cur:
            cur.execute(
                """
                UPDATE commerce.addresses
                   SET line1 = %s, city = %s, region = %s, postal = %s, country = %s
                 WHERE id = (SELECT min(id) FROM commerce.addresses
                              WHERE customer_id = %s AND kind = 'shipping')
                RETURNING id
                """,
                params + [cid])
            row = cur.fetchone()
            if row:
                return row[0]
            cur.execute(
                """
                INSERT INTO commerce.addresses
                    (customer_id, kind, line1, city, region, postal, country)
                VALUES (%s, 'shipping', %s, %s, %s, %s, %s)
                RETURNING id
                """,
                [cid] + params)
            return cur.fetchone()[0]


def _save_payment_method(cid, pay):
    """Idempotent per (customer, brand, last4, expiry) — token is opaque, never a PAN."""
    brand = str(pay.get('brand') or '').strip() or 'card'
    with transaction.atomic(using='finance'):
        with connections['finance'].cursor() as cur:
            cur.execute(
                """
                SELECT id FROM finance.payment_methods
                 WHERE customer_ref = %s AND brand = %s AND last4 = %s
                   AND exp_month = %s AND exp_year = %s
                """,
                [cid, brand, pay['last4'], int(pay['exp_month']), int(pay['exp_year'])])
            row = cur.fetchone()
            if row:
                return row[0]
            cur.execute(
                """
                INSERT INTO finance.payment_methods
                    (customer_ref, brand, last4, token, exp_month, exp_year)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                [cid, brand, pay['last4'], pay['token'],
                 int(pay['exp_month']), int(pay['exp_year'])])
            return cur.fetchone()[0]


def _place_order(cid, items):
    """Order + items in one orders-db transaction (was sales.place_order)."""
    total = sum(int(it['qty']) * int(it['unit_price_cents']) for it in items)
    with transaction.atomic(using='orders'):
        with connections['orders'].cursor() as cur:
            cur.execute(
                """
                INSERT INTO sales.orders (customer_ref, status, total_cents)
                VALUES (%s, 'placed', %s) RETURNING id
                """,
                [cid, total])
            oid = cur.fetchone()[0]
            cur.executemany(
                """
                INSERT INTO sales.order_items (order_id, product_sku, qty, unit_price_cents)
                VALUES (%s, %s, %s, %s)
                """,
                [[oid, it['product_sku'], int(it['qty']), int(it['unit_price_cents'])]
                 for it in items])
    return oid, total


def _record_payment(oid, amount_cents):
    with connections['finance'].cursor() as cur:
        cur.execute(
            """
            INSERT INTO finance.transactions (order_ref, amount_cents, kind, status)
            VALUES (%s, %s, 'charge', 'settled') RETURNING id
            """,
            [oid, amount_cents])
        return cur.fetchone()[0]


@require_POST
def checkout(request):
    try:
        claims = require_customer(request)
    except AuthError as exc:
        return _bad(str(exc), status=401)

    try:
        body = json.loads(request.body or b'{}')
    except json.JSONDecodeError:
        return _bad('request body must be JSON')

    problem = _validate(body)
    if problem:
        return _bad(problem)

    cid = _ensure_customer(claims)
    _save_address(cid, body['address'])
    _save_payment_method(cid, body['payment'])
    oid, total = _place_order(cid, body['items'])
    try:
        _record_payment(oid, total)
    except Exception:
        # best-effort saga step (cross-DB, non-atomic) — order is already placed
        pass

    return JsonResponse({'order_id': oid, 'total_cents': total}, status=201)
