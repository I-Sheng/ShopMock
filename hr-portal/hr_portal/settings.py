"""Django settings for the hr-portal (HR workforce application).

A separate service from finance-portal on purpose. The two applications could
have shared one Django project behind two URL prefixes, but then a single
process would hold credentials for both domains and one code-execution bug
would breach both. Here the isolation is structural:

  * its own image and its own container;
  * its own Keycloak browser client (`hr-portal`) and realm role (`hr`);
  * ONE database connection, to a dedicated `hr-db` on its own internal network,
    reached through the least-privilege `hr_portal` login;
  * no credential, no hostname and no network path to finance-db, customer-db,
    orders-db or catalog-db.

An HR query against finance data is therefore not merely forbidden by review —
there is no connection here on which such a query could run.

As in oe-dashboard, nothing reads a secret at import time, so `manage.py check`
and the whole test suite run with an empty environment.
"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get('DJANGO_SECRET_KEY', 'lab-only-insecure-key')
DEBUG = os.environ.get('DJANGO_DEBUG', '') == '1'
ALLOWED_HOSTS = ['*']  # reachable only through the Traefik edge

ROOT_URLCONF = 'hr_portal.urls'
WSGI_APPLICATION = 'hr_portal.wsgi.application'

INSTALLED_APPS = []
MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'people' / 'templates'],
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
# Exactly one entry, and it is hr-db. This is the isolation boundary.
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'HOST': os.environ.get('HR_DB_HOST', 'hr-db'),
        'PORT': 5432,
        'NAME': os.environ.get('HR_DB_NAME', 'hr'),
        # Created by seed/hr-db/03_hr_portal_role.sh.
        'USER': os.environ.get('HR_PORTAL_DB_USER', 'hr_portal'),
        'PASSWORD': os.environ.get('HR_PORTAL_DB_PASSWORD', ''),
        'CONN_MAX_AGE': 60,
        'OPTIONS': {'connect_timeout': 5},
    },
}

# ---------------------------------------------------------------- authorization
# The same pinned RS256 *public* JWK the rest of the stack verifies against
# (PGRST_JWT_SECRET in .env). Blank is allowed so the image builds and
# self-checks without credentials; a request that needs it fails closed.
HR_JWT_JWK = os.environ.get('PGRST_JWT_SECRET', '')

HR_OIDC_REALM = os.environ.get('HR_OIDC_REALM', 'shopmock')
# A dedicated browser client — never the storefront's, Seller Central's, the IT
# console's or the Finance portal's.
HR_OIDC_CLIENT_ID = os.environ.get('HR_OIDC_CLIENT_ID', 'hr-portal')
# Realm role, federated from the FreeIPA `hr` group via /workforce/hr.
HR_REQUIRED_ROLE = os.environ.get('HR_REQUIRED_ROLE', 'hr')

PUBLIC_ORIGIN = (os.environ.get('PUBLIC_ORIGIN') or 'http://localhost').rstrip('/')


def _default_issuers():
    """Keycloak stamps `iss` from the origin the browser logged in through.

    Accept the configured public origin and http://localhost — exactly the two
    origins scripts/deploy.sh registers as client redirect origins.
    """
    origins = ['http://localhost']
    if PUBLIC_ORIGIN and PUBLIC_ORIGIN not in origins:
        origins.append(PUBLIC_ORIGIN)
    return [f'{o}/auth/realms/{HR_OIDC_REALM}' for o in origins]


HR_ALLOWED_ISSUERS = [
    i.strip()
    for i in os.environ.get('HR_ALLOWED_ISSUERS', '').split(',')
    if i.strip()
] or _default_issuers()

# Row caps for the read-only roster endpoints — a bounded response is also a
# bound on how much of the staff directory one stolen session can drain.
HR_MAX_ROWS = int(os.environ.get('HR_MAX_ROWS', '200'))
