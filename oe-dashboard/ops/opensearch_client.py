"""The one call this service makes against the SIEM's index store.

`POST /<index pattern>/_search` with a body built by `alerts.build_query` is a
fixed shape, never assembled from request input, so there is no path by which a
caller can steer this client at another endpoint — no `_bulk`, no `_cluster`,
no index or document id taken from the browser, and no method but POST.

The credential is the OpenSearch account configured for this console; it
travels in an `Authorization` header, never in the URL, and never appears in a
return value, a log line or an exception. What this module hands back is the
allowlisted model from `alerts.py` — the raw `_source` does not leave it.

Stdlib only, on purpose: the console's dependency list stays at Django, PyJWT
and gunicorn rather than gaining an OpenSearch SDK for a single bounded query.
"""
import json
import ssl
from base64 import b64encode
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen

from django.conf import settings

from .alerts import AlertApiError, build_query, empty_model, model_from_response

# `ignore_unavailable` / `allow_no_indices` are what make "Wazuh is running but
# has not shipped its first alert yet" a normal empty result rather than a 404.
_QUERY_STRING = 'ignore_unavailable=true&allow_no_indices=true'
_MAX_BYTES = 4 * 1024 * 1024
_SCHEMES = ('https', 'http')


def _endpoint():
    """Base URL with any embedded userinfo dropped — this is what may be logged."""
    parts = urlsplit((settings.OE_OPENSEARCH_URL or '').strip().rstrip('/'))
    if parts.scheme not in _SCHEMES or not parts.hostname:
        raise AlertApiError(f'unsupported alert index endpoint: {parts.scheme}://')
    netloc = parts.hostname if parts.port is None else f'{parts.hostname}:{parts.port}'
    return f'{parts.scheme}://{netloc}'


def _url(base):
    index = quote((settings.OE_ALERT_INDEX or '').strip(), safe='')
    if not index:
        raise AlertApiError('no alert index pattern is configured')
    return f'{base}/{index}/_search?{_QUERY_STRING}'


def _headers():
    headers = {'Content-Type': 'application/json', 'Accept': 'application/json'}
    user = settings.OE_OPENSEARCH_USER or ''
    password = settings.OE_OPENSEARCH_PASSWORD or ''
    if user or password:
        secret = b64encode(f'{user}:{password}'.encode()).decode()
        headers['Authorization'] = f'Basic {secret}'
    return headers


def _context():
    """Verified TLS unless the deployment deliberately turned it off."""
    if settings.OE_OPENSEARCH_VERIFY_TLS:
        return ssl.create_default_context()
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return context


def _is_missing_index(error):
    """The normal "that index does not exist yet" reply, not a backend failure."""
    if error.code != 404:
        return False
    try:
        body = json.loads(error.read(_MAX_BYTES) or b'{}')
    except (OSError, ValueError):
        return False
    cause = body.get('error') if isinstance(body, dict) else None
    cause = cause.get('type') if isinstance(cause, dict) else None
    return cause == 'index_not_found_exception'


def fetch_alerts():
    """Summary counts + a bounded page of allowlisted alerts, newest first."""
    base = _endpoint()
    request = Request(
        _url(base),
        data=json.dumps(build_query(settings.OE_ALERT_LIMIT)).encode(),
        headers=_headers(),
        method='POST',
    )
    try:
        with urlopen(request, timeout=settings.OE_OPENSEARCH_TIMEOUT,
                     context=_context()) as response:
            body = response.read(_MAX_BYTES + 1)
            if len(body) > _MAX_BYTES:
                raise AlertApiError('alert index response too large')
            status = getattr(response, 'status', 200)
            if status != 200:
                raise AlertApiError(f'alert index returned HTTP {status}')
            return model_from_response(json.loads(body), settings.OE_ALERT_LIMIT)
    except AlertApiError:
        raise
    except HTTPError as exc:
        # Only the status is carried forward: the reply body can quote the
        # query, the index and the authenticated user back at us.
        if _is_missing_index(exc):
            return empty_model()
        raise AlertApiError(f'{base}: alert index returned HTTP {exc.code}')
    except (URLError, OSError, ValueError) as exc:
        raise AlertApiError(f'{base}: {exc.__class__.__name__}')
