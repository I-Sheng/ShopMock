"""Django settings for the finance-portal (Finance workforce application).

Deliberately minimal, mirroring oe-dashboard and seller-backend: a JSON API
plus one server-rendered page, with no ORM models, admin, sessions or Django
auth. Finance SQL runs as parameterized raw queries through a single named
connection.

Data boundary (design §2): this service talks ONLY to finance-db, through the
least-privilege `finance_portal` login role, which is granted column-level
SELECT that deliberately EXCLUDES `finance.payment_methods.token`. Customer
PII, order rows and HR data live in other databases this service has no
credential for. The HR portal is a separate service with a separate image,
separate Keycloak client and a separate database login — see hr-portal/.

As in oe-dashboard, nothing here reads a secret at import time: the pinned realm
JWK and the DB password are resolved from the environment with a safe default,
so `manage.py check` and the whole test suite run with an empty environment.
"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get('DJANGO_SECRET_KEY', 'lab-only-insecure-key')
DEBUG = os.environ.get('DJANGO_DEBUG', '') == '1'
ALLOWED_HOSTS = ['*']  # reachable only through the Traefik edge

ROOT_URLCONF = 'finance_portal.urls'
WSGI_APPLICATION = 'finance_portal.wsgi.application'

INSTALLED_APPS = []
MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'ledger' / 'templates'],
        'APP_DIRS': False,
        'OPTIONS': {'context_processors': []},
    },
]

USE_TZ = True
APPEND_SLASH = False

SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = 'same-origin'
X_FRAME_OPTIONS = 'DENY'

# ------------------------------------------------------------------- database
# One connection, one database, one least-privilege role. There is deliberately
# no second entry here: an HR or customer query in this service would have no
# connection to run on.
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'HOST': os.environ.get('FINANCE_DB_HOST', 'finance-db'),
        'PORT': 5432,
        'NAME': os.environ.get('FINANCE_DB_NAME', 'finance'),
        # Created by seed/finance-db/06_finance_portal_role.sh.
        'USER': os.environ.get('FINANCE_PORTAL_DB_USER', 'finance_portal'),
        'PASSWORD': os.environ.get('FINANCE_PORTAL_DB_PASSWORD', ''),
        'CONN_MAX_AGE': 60,
        'OPTIONS': {'connect_timeout': 5},
    },
}

# ---------------------------------------------------------------- authorization
# The same pinned RS256 *public* JWK the PostgREST services and the Django
# backends verify against (PGRST_JWT_SECRET in .env). Blank is allowed here so
# the image builds and self-checks without credentials; a request that needs it
# fails closed.
FINANCE_JWT_JWK = os.environ.get('PGRST_JWT_SECRET', '')

FINANCE_OIDC_REALM = os.environ.get('FINANCE_OIDC_REALM', 'shopmock')
# A dedicated browser client — never the storefront's, Seller Central's, the IT
# console's or the HR portal's. A token minted for another client is rejected
# even if it carries the finance role.
FINANCE_OIDC_CLIENT_ID = os.environ.get('FINANCE_OIDC_CLIENT_ID', 'finance-portal')
# Realm role, federated from the FreeIPA `finance` group via /workforce/finance.
FINANCE_REQUIRED_ROLE = os.environ.get('FINANCE_REQUIRED_ROLE', 'finance')

PUBLIC_ORIGIN = (os.environ.get('PUBLIC_ORIGIN') or 'http://localhost').rstrip('/')


def _default_issuers():
    """Keycloak stamps `iss` from the origin the browser logged in through.

    Accept the configured public origin and http://localhost — exactly the two
    origins scripts/deploy.sh registers as client redirect origins.
    """
    origins = ['http://localhost']
    if PUBLIC_ORIGIN and PUBLIC_ORIGIN not in origins:
        origins.append(PUBLIC_ORIGIN)
    return [f'{o}/auth/realms/{FINANCE_OIDC_REALM}' for o in origins]


FINANCE_ALLOWED_ISSUERS = [
    i.strip()
    for i in os.environ.get('FINANCE_ALLOWED_ISSUERS', '').split(',')
    if i.strip()
] or _default_issuers()

# Row caps for the read-only reporting endpoints — a bounded response is also a
# bound on how much of the ledger a single stolen session can drain.
FINANCE_MAX_ROWS = int(os.environ.get('FINANCE_MAX_ROWS', '200'))
