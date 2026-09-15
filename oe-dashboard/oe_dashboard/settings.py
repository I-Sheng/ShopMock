"""Django settings for the oe-dashboard (IT operations console).

Deliberately minimal, mirroring seller-backend / internal-service-backend: a
JSON API plus one server-rendered page, with no ORM models, admin, sessions or
databases. The service's only data source is a read-only container-status API.

One deliberate difference from its siblings: nothing here reads a secret at
import time. The pinned realm JWK is resolved per request from settings, so
`manage.py check` and the whole test suite run with an empty environment —
tests mint their own throwaway keys instead of borrowing the lab's.
"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _flag(name, default):
    """An env flag that fails *safe*: unset or blank keeps the secure default."""
    raw = (os.environ.get(name) or '').strip().lower()
    if not raw:
        return default
    return raw not in ('0', 'false', 'no', 'off')

SECRET_KEY = os.environ.get('DJANGO_SECRET_KEY', 'lab-only-insecure-key')
DEBUG = os.environ.get('DJANGO_DEBUG', '') == '1'
ALLOWED_HOSTS = ['*']  # reachable only through the Traefik edge

ROOT_URLCONF = 'oe_dashboard.urls'
WSGI_APPLICATION = 'oe_dashboard.wsgi.application'

INSTALLED_APPS = []
MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

# No datastore: the dashboard reads container status and nothing else.
DATABASES = {}

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'ops' / 'templates'],
        'APP_DIRS': False,
        'OPTIONS': {'context_processors': []},
    },
]

USE_TZ = True
APPEND_SLASH = False

SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = 'same-origin'
X_FRAME_OPTIONS = 'DENY'

# ---------------------------------------------------------------- authorization
# The same pinned RS256 *public* JWK the PostgREST services and the two Django
# backends verify against (PGRST_JWT_SECRET in .env). Blank is allowed here so
# the image builds and self-checks without credentials; a request that needs it
# fails closed.
OE_JWT_JWK = os.environ.get('PGRST_JWT_SECRET', '')

OE_OIDC_REALM = os.environ.get('OE_OIDC_REALM', 'shopmock-workforce')
# A dedicated browser client — never the storefront's or Seller Central's. A
# token minted for another client is rejected even if it carries the role.
OE_OIDC_CLIENT_ID = os.environ.get('OE_OIDC_CLIENT_ID', 'it-operations')
# Realm role, federated from the FreeIPA `it-ops` group via /workforce/it-ops.
OE_REQUIRED_ROLE = os.environ.get('OE_REQUIRED_ROLE', 'it-ops')

PUBLIC_ORIGIN = (os.environ.get('PUBLIC_ORIGIN') or 'http://localhost').rstrip('/')


def _default_issuers():
    """Keycloak stamps `iss` from the origin the browser logged in through.

    Accept the configured public origin and http://localhost — exactly the two
    origins scripts/deploy.sh registers as client redirect origins.
    """
    origins = ['http://localhost']
    if PUBLIC_ORIGIN and PUBLIC_ORIGIN not in origins:
        origins.append(PUBLIC_ORIGIN)
    return [f'{o}/auth/realms/{OE_OIDC_REALM}' for o in origins]


OE_ALLOWED_ISSUERS = [
    i.strip() for i in os.environ.get('OE_ALLOWED_ISSUERS', '').split(',') if i.strip()
] or _default_issuers()

# ------------------------------------------------------------ container status
# Compose project whose containers this console reports on. Anything outside it
# is dropped before normalization, so a co-tenant stack is never disclosed.
OE_PROJECT_NAME = os.environ.get('OE_PROJECT_NAME', 'shopmock')
# A narrowly scoped, read-only proxy in front of the Podman/Docker socket — the
# socket itself is never mounted into this container. See docker-compose.yml.
OE_CONTAINER_API = os.environ.get('OE_CONTAINER_API', 'http://oe-socket-proxy:2375')
OE_CONTAINER_API_TIMEOUT = float(os.environ.get('OE_CONTAINER_API_TIMEOUT', '5'))

# --------------------------------------------------------------- security alerts
# Wazuh alerts are read from OpenSearch over soc_net. Strictly read-only: this
# console never writes an index and never talks to the Wazuh manager API.
#
# Like the realm JWK above, nothing here is read at import time by a *test* —
# blanks are allowed so the image builds and self-checks without credentials,
# and a request that needs them fails closed as a 502.
OE_OPENSEARCH_URL = os.environ.get('OE_OPENSEARCH_URL', 'https://search:9200')
OE_OPENSEARCH_USER = os.environ.get('OE_OPENSEARCH_USER', '')
OE_OPENSEARCH_PASSWORD = os.environ.get('OE_OPENSEARCH_PASSWORD', '')
# Index *pattern*, not a caller-supplied index: the endpoint never takes one.
OE_ALERT_INDEX = os.environ.get('OE_ALERT_INDEX', 'wazuh-alerts-*')
OE_OPENSEARCH_TIMEOUT = float(os.environ.get('OE_OPENSEARCH_TIMEOUT', '5'))
# Verified TLS unless a deployment deliberately opts out — which the lab does,
# because its OpenSearch still serves the bundled demo certificate. See README.
OE_OPENSEARCH_VERIFY_TLS = _flag('OE_OPENSEARCH_VERIFY_TLS', True)
# Page size for the recent-alerts list. `alerts.MAX_LIMIT` caps this regardless,
# so a fat-fingered override cannot turn the endpoint into a SIEM export.
OE_ALERT_LIMIT = int(os.environ.get('OE_ALERT_LIMIT', '25'))
