"""Seller portal API — all seller SQL lives here.

Tier-2 line-of-business surface (design §2): a seller manages their own
listings (catalog-db: `seller` + `catalog` schemas) and reads their own sales
out of orders-db. Customer PII and finance data are out of scope by design —
this service has no connection to those databases. Every statement is
parameterized (no string-built SQL) and ownership is always derived from the
verified JWT `sub`, never from the request body, so a seller can only ever
touch rows behind their own `seller.sellers` profile.
"""
import json
import re

from django.db import IntegrityError, connections, transaction
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods, require_POST

from .auth import AuthError, require_seller

_SKU_RE = re.compile(r'[A-Z0-9][A-Z0-9-]{2,31}')


def healthz(request):
    return JsonResponse({'status': 'ok'})


def _bad(msg, status=400):
    return JsonResponse({'error': msg}, status=status)


def _auth(request):
    """Returns (claims, None) or (None, error response)."""
    try:
        return require_seller(request), None
    except AuthError as exc:
        return None, _bad(str(exc), status=401)


def _seller_id(sub):
    with connections['default'].cursor() as cur:
        cur.execute('SELECT id FROM seller.sellers WHERE keycloak_sub = %s', [sub])
        row = cur.fetchone()
        return row[0] if row else None


def _json_body(request):
    try:
        return json.loads(request.body or b'{}'), None
    except json.JSONDecodeError:
        return None, _bad('request body must be JSON')


@require_POST
def ensure_seller(request):
    """Idempotent seller provisioning, keyed on the verified Keycloak sub
    (same shape as the customer flow's ensure_customer)."""
    claims, err = _auth(request)
    if err:
        return err

    sub = claims['sub']
    email = claims.get('email') or claims.get('preferred_username') or f'{sub}@lab.local'
    name = (
        claims.get('name')
        or ' '.join(filter(None, [claims.get('given_name'), claims.get('family_name')]))
        or claims.get('preferred_username')
        or 'Unknown seller'
    )
    with transaction.atomic(using='default'):
        sid = _seller_id(sub)
        if sid is None:
            with connections['default'].cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO seller.sellers (keycloak_sub, display_name, contact_email)
                    VALUES (%s, %s, %s) RETURNING id
                    """,
                    [sub, name, email])
                sid = cur.fetchone()[0]
    return JsonResponse({'seller_id': sid}, status=200)


def _listing_rows(sid, listing_id=None):
    sql = """
        SELECT l.id, l.commission_pct, p.id, p.sku, p.name, p.description,
               p.price_cents, p.currency, p.active, COALESCE(i.qty, 0)
          FROM seller.listings l
          JOIN catalog.products p ON p.id = l.product_id
          LEFT JOIN catalog.inventory i ON i.product_id = p.id
         WHERE l.seller_id = %s
        """
    params = [sid]
    if listing_id is not None:
        sql += ' AND l.id = %s'
        params.append(listing_id)
    sql += ' ORDER BY l.id'
    with connections['default'].cursor() as cur:
        cur.execute(sql, params)
        return [
            {
                'listing_id': r[0], 'commission_pct': float(r[1]),
                'product_id': r[2], 'sku': r[3], 'name': r[4],
                'description': r[5], 'price_cents': r[6], 'currency': r[7],
                'active': r[8], 'qty': r[9],
            }
            for r in cur.fetchall()
        ]


def _validate_new_listing(body):
    if not _SKU_RE.fullmatch(str(body.get('sku') or '')):
        return 'sku must be 3-32 chars of A-Z, 0-9 and dashes'
    if not str(body.get('name') or '').strip():
        return 'name is required'
    try:
        if int(body.get('price_cents')) < 0 or int(body.get('qty', 0)) < 0:
            return 'price_cents/qty out of range'
    except (TypeError, ValueError):
        return 'price_cents/qty must be integers'
    if body.get('category_id') is not None:
        try:
            int(body['category_id'])
        except (TypeError, ValueError):
            return 'category_id must be an integer'
    return None


@require_http_methods(['GET', 'POST'])
def listings(request):
    claims, err = _auth(request)
    if err:
        return err
    sid = _seller_id(claims['sub'])
    if sid is None:
        return _bad('no seller profile — call /sellers/ensure first', status=403)

    if request.method == 'GET':
        return JsonResponse(_listing_rows(sid), safe=False)

    body, err = _json_body(request)
    if err:
        return err
    problem = _validate_new_listing(body)
    if problem:
        return _bad(problem)

    try:
        with transaction.atomic(using='default'):
            with connections['default'].cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO catalog.products (sku, name, description, category_id, price_cents)
                    VALUES (%s, %s, %s, %s, %s) RETURNING id
                    """,
                    [body['sku'], body['name'].strip(),
                     (body.get('description') or '').strip() or None,
                     body.get('category_id'), int(body['price_cents'])])
                pid = cur.fetchone()[0]
                cur.execute(
                    'INSERT INTO catalog.inventory (product_id, qty) VALUES (%s, %s)',
                    [pid, int(body.get('qty', 0))])
                cur.execute(
                    """
                    INSERT INTO seller.listings (seller_id, product_id)
                    VALUES (%s, %s) RETURNING id
                    """,
                    [sid, pid])
                lid = cur.fetchone()[0]
    except IntegrityError as exc:
        if 'products_sku_key' in str(exc):
            return _bad('sku already exists', status=409)
        return _bad('invalid reference (unknown category_id?)', status=400)

    return JsonResponse(_listing_rows(sid, lid)[0], status=201)


@require_http_methods(['PATCH'])
def listing_detail(request, listing_id):
    """Partial update of one owned listing: name, description, price_cents,
    active, qty. Commission stays platform-set — sellers cannot change it."""
    claims, err = _auth(request)
    if err:
        return err
    sid = _seller_id(claims['sub'])
    if sid is None:
        return _bad('no seller profile — call /sellers/ensure first', status=403)

    body, err = _json_body(request)
    if err:
        return err

    # Validate the whole payload before touching the DB so a bad later field
    # can't leave earlier updates committed.
    product_sets, qty = [], None
    if 'name' in body:
        if not str(body['name'] or '').strip():
            return _bad('name cannot be blank')
        product_sets.append(('name', str(body['name']).strip()))
    if 'description' in body:
        product_sets.append(('description', str(body['description'] or '').strip() or None))
    if 'price_cents' in body:
        try:
            price = int(body['price_cents'])
        except (TypeError, ValueError):
            return _bad('price_cents must be an integer')
        if price < 0:
            return _bad('price_cents out of range')
        product_sets.append(('price_cents', price))
    if 'active' in body:
        if not isinstance(body['active'], bool):
            return _bad('active must be a boolean')
        product_sets.append(('active', body['active']))
    if 'qty' in body:
        try:
            qty = int(body['qty'])
        except (TypeError, ValueError):
            return _bad('qty must be an integer')
        if qty < 0:
            return _bad('qty out of range')
    if not product_sets and qty is None:
        return _bad('nothing to update: allowed fields are '
                    'name, description, price_cents, active, qty')

    with transaction.atomic(using='default'):
        with connections['default'].cursor() as cur:
            cur.execute(
                """
                SELECT l.product_id FROM seller.listings l
                 WHERE l.id = %s AND l.seller_id = %s
                """,
                [listing_id, sid])
            row = cur.fetchone()
            if not row:
                return _bad('listing not found', status=404)
            pid = row[0]

            # Column names come from the fixed whitelist above, values are bound.
            for column, value in product_sets:
                cur.execute(
                    f'UPDATE catalog.products SET {column} = %s WHERE id = %s',
                    [value, pid])
            if qty is not None:
                cur.execute(
                    """
                    INSERT INTO catalog.inventory (product_id, qty) VALUES (%s, %s)
                    ON CONFLICT (product_id) DO UPDATE SET qty = EXCLUDED.qty
                    """,
                    [pid, qty])

    return JsonResponse(_listing_rows(sid, listing_id)[0])


@require_http_methods(['GET'])
def sales(request):
    """Read-only view of order lines for the caller's own SKUs (orders-db).
    Cross-DB by ref, like the rest of the stack: SKUs resolved in catalog-db,
    then matched against sales.order_items — no FK exists between the DBs."""
    claims, err = _auth(request)
    if err:
        return err
    sid = _seller_id(claims['sub'])
    if sid is None:
        return _bad('no seller profile — call /sellers/ensure first', status=403)

    with connections['default'].cursor() as cur:
        cur.execute(
            """
            SELECT p.sku FROM seller.listings l
              JOIN catalog.products p ON p.id = l.product_id
             WHERE l.seller_id = %s
            """,
            [sid])
        skus = [r[0] for r in cur.fetchall()]
    if not skus:
        return JsonResponse({'lines': [], 'total_units': 0, 'gross_cents': 0})

    with connections['orders'].cursor() as cur:
        cur.execute(
            """
            SELECT o.id, o.status, o.placed_at, oi.product_sku, oi.qty, oi.unit_price_cents
              FROM sales.order_items oi
              JOIN sales.orders o ON o.id = oi.order_id
             WHERE oi.product_sku = ANY(%s)
             ORDER BY o.placed_at DESC, oi.id
            """,
            [skus])
        lines = [
            {
                'order_id': r[0], 'status': r[1], 'placed_at': r[2].isoformat(),
                'sku': r[3], 'qty': r[4], 'unit_price_cents': r[5],
            }
            for r in cur.fetchall()
        ]

    return JsonResponse({
        'lines': lines,
        'total_units': sum(x['qty'] for x in lines),
        'gross_cents': sum(x['qty'] * x['unit_price_cents'] for x in lines),
    })
