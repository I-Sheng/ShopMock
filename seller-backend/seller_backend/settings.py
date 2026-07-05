"""Django settings for the seller-backend.

Deliberately minimal, mirroring internal-service-backend: a JSON API with no
ORM models, admin, sessions or templates. All seller SQL runs as parameterized
raw queries through the two named database connections below.

Data boundary (Tier 2, design §2): this service talks ONLY to catalog-db
(`seller` + `catalog` schemas) and orders-db (read-only). Customer PII and
finance/HR data are internal-service-backend's territory — never wired here.
"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get('DJANGO_SECRET_KEY', 'lab-only-insecure-key')
DEBUG = os.environ.get('DJANGO_DEBUG', '') == '1'
ALLOWED_HOSTS = ['*']  # reachable only through the Traefik edge

ROOT_URLCONF = 'seller_backend.urls'
WSGI_APPLICATION = 'seller_backend.wsgi.application'

INSTALLED_APPS = []
MIDDLEWARE = ['django.middleware.common.CommonMiddleware']


def _db(host, name):
    # Dedicated least-privilege login role; created by seed/*/0X init scripts.
    return {
        'ENGINE': 'django.db.backends.postgresql',
        'HOST': host,
        'PORT': 5432,
        'NAME': name,
        'USER': 'seller_backend',
        'PASSWORD': os.environ['SELLER_BACKEND_DB_PASSWORD'],
    }


DATABASES = {
    'default': _db('catalog-db', 'catalog'),
    'orders': _db('orders-db', 'orders'),
}

USE_TZ = True
APPEND_SLASH = False
