"""HTTP surface of the IT operations console.

Three kinds of response: an unauthenticated shell (the page and its two assets,
which contain no stack data), an unauthenticated liveness probe, and exactly
one authorized data endpoint. Authorization runs before the container backend
is touched, so a rejected caller produces no runtime traffic at all.
"""
import logging
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET

from .auth import AuthError, require_it_ops
from .containers import normalize_containers, summarize
from .docker_client import list_containers

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
    response = render(request, 'ops/index.html', {
        'realm': settings.OE_OIDC_REALM,
        'client_id': settings.OE_OIDC_CLIENT_ID,
        'required_role': settings.OE_REQUIRED_ROLE,
        'project': settings.OE_PROJECT_NAME,
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


@require_GET
def containers(request):
    try:
        require_it_ops(request)
    except AuthError as exc:
        return JsonResponse({'error': str(exc)}, status=exc.status)

    try:
        raw = list_containers()
    except Exception:
        # Endpoint paths, socket locations and runtime errors stay in the log.
        log.exception('container status backend failed')
        return JsonResponse({'error': 'container status unavailable'}, status=502)

    normalized = normalize_containers(raw)
    response = JsonResponse({
        'project': settings.OE_PROJECT_NAME,
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'summary': summarize(normalized),
        'containers': normalized,
    })
    response.headers['Cache-Control'] = 'no-store'
    return response
