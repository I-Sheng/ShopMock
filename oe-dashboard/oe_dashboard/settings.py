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

OE_OIDC_REALM = os.environ.get('OE_OIDC_REALM', 'shopmock')
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
