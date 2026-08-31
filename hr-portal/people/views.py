"""HTTP surface of the HR portal.

Three kinds of response: an unauthenticated shell (the page and its assets,
which contain no staff data), an unauthenticated liveness probe, and three
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
from .auth import AuthError, require_hr

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
    """The signed-out shell. Carries OIDC coordinates, never the realm key —
    and no staff data, since it is served to anyone who asks for the page."""
    response = render(request, 'people/index.html', {
        'realm': settings.HR_OIDC_REALM,
        'client_id': settings.HR_OIDC_CLIENT_ID,
        'required_role': settings.HR_REQUIRED_ROLE,
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


def _guarded(request, build):
    """Authorize, read, serialize — each failure mapped to its own status."""
    try:
        require_hr(request)
    except AuthError as exc:
        return JsonResponse({'error': str(exc)}, status=exc.status)

    try:
        rows = build()
    except serializers.UnsafeFieldError:
        # The offending value is never logged: it is exactly the kind of field
        # this path exists to contain.
        log.error('refused to serialize a staff row carrying a forbidden field')
        return JsonResponse(
            {'error': 'staff data could not be safely rendered'}, status=500)
    except Exception:
        # Hostnames, credentials and driver detail stay in the log.
        log.exception('staff data backend failed')
        return JsonResponse({'error': 'staff data unavailable'}, status=502)

    return _json(dict(rows, generated_at=datetime.now(timezone.utc).isoformat()))


@require_GET
def overview(request):
    def build():
        return {
            'headcount': serializers.headcount(queries.headcount()),
            'departments': serializers.many(
                serializers.department, queries.departments()),
        }

    return _guarded(request, build)


@require_GET
def employees(request):
    def build():
        return {'employees': serializers.many(
            serializers.employee, queries.employees(_requested_rows(request)))}

    return _guarded(request, build)


@require_GET
def leave(request):
    def build():
        return {'leave_requests': serializers.many(
            serializers.leave_request,
            queries.leave_requests(_requested_rows(request)))}

    return _guarded(request, build)
