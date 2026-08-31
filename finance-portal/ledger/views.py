"""HTTP surface of the Finance portal.

Three kinds of response: an unauthenticated shell (the page and its assets,
which contain no finance data), an unauthenticated liveness probe, and three
authorized read-only endpoints. Authorization runs before any query, so a
rejected caller produces no database traffic at all.

Two failure modes are handled apart on purpose. A backend error is a 502 whose
body names nothing — hostnames, credentials and driver messages stay in the log.
A row that carries a field this service must never publish is a 500: the
serializer refused, and refusing is the correct outcome even though it costs the
whole response.
"""
import logging
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET

from . import queries, serializers
from .auth import AuthError, require_finance

log = logging.getLogger(__name__)

_ASSETS = Path(__file__).resolve().parent / 'assets'

# The page loads its CSS and JS as same-origin files rather than inlining them,
# which lets the policy below stay free of 'unsafe-inline'.
_CSP = "; ".join([
    "default-src 'none'",
    "script-src 'self'",
    "style-src 'self'",
    "img-src 'self' data:",
    "connect-src 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
    "base-uri 'none'",
])

@lru_cache(maxsize=4)
def _asset(name):
    return (_ASSETS / name).read_bytes()


@require_GET
def healthz(request):
    return JsonResponse({'status': 'ok'})


@require_GET
def index(request):
    """The signed-out shell. Carries OIDC coordinates, never the realm key."""
    response = render(request, 'ledger/index.html', {
        'realm': settings.FINANCE_OIDC_REALM,
        'client_id': settings.FINANCE_OIDC_CLIENT_ID,
        'required_role': settings.FINANCE_REQUIRED_ROLE,
    })
    response.headers['Content-Security-Policy'] = _CSP
    return response


@require_GET
def app_css(request):
    return HttpResponse(_asset('app.css'), content_type='text/css; charset=utf-8')


@require_GET
def app_js(request):
    return HttpResponse(_asset('app.js'), content_type='text/javascript; charset=utf-8')


@require_GET
def pkce_js(request):
    return HttpResponse(_asset('pkce.js'), content_type='text/javascript; charset=utf-8')


def _requested_rows(request):
    """Clamped here as well as in the query layer — see queries.clamp_rows."""
    return queries.clamp_rows(request.GET.get('limit'))


def _json(payload):
    response = JsonResponse(payload)
    response.headers['Cache-Control'] = 'no-store'
    return response


def _unavailable():
    return JsonResponse({'error': 'finance data unavailable'}, status=502)


def _unsafe():
    return JsonResponse(
        {'error': 'finance data could not be safely rendered'}, status=500)


def _guarded(request, build):
    """Authorize, read, serialize — each failure mapped to its own status."""
    try:
        require_finance(request)
    except AuthError as exc:
        return JsonResponse({'error': str(exc)}, status=exc.status)

    try:
        rows = build()
    except serializers.UnsafeFieldError:
        # The offending value is never logged: it may be the payment token this
        # whole path exists to contain.
        log.error('refused to serialize a finance row carrying a forbidden field')
        return _unsafe()
    except Exception:
        # Hostnames, credentials and driver detail stay in the log.
        log.exception('finance data backend failed')
        return _unavailable()

    return _json(dict(rows, generated_at=datetime.now(timezone.utc).isoformat()))


@require_GET
def overview(request):
    def build():
        return {
            'revenue': serializers.many(
                serializers.revenue_day, queries.revenue(_requested_rows(request))),
            'wallets': serializers.many(
                serializers.wallet_total, queries.wallet_totals()),
            'totals': serializers.transaction_totals(queries.transaction_totals()),
        }

    return _guarded(request, build)


@require_GET
def transactions(request):
    def build():
        return {'transactions': serializers.many(
            serializers.transaction, queries.transactions(_requested_rows(request)))}

    return _guarded(request, build)


@require_GET
def payment_methods(request):
    def build():
        return {'payment_methods': serializers.many(
            serializers.payment_method,
            queries.payment_methods(_requested_rows(request)))}

    return _guarded(request, build)
