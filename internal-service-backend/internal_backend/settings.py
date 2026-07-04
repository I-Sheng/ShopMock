"""Django settings for the internal-service-backend.

Deliberately minimal: this service is a JSON API with no ORM models, admin,
sessions or templates. All checkout SQL runs as parameterized raw queries
through the three named database connections below.
"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get('DJANGO_SECRET_KEY', 'lab-only-insecure-key')
DEBUG = os.environ.get('DJANGO_DEBUG', '') == '1'
ALLOWED_HOSTS = ['*']  # reachable only through the Traefik edge

ROOT_URLCONF = 'internal_backend.urls'
WSGI_APPLICATION = 'internal_backend.wsgi.application'

INSTALLED_APPS = []
MIDDLEWARE = ['django.middleware.common.CommonMiddleware']


def _db(host, name):
    # Dedicated least-privilege login role; created by seed/*/05 init scripts.
    return {
        'ENGINE': 'django.db.backends.postgresql',
        'HOST': host,
        'PORT': 5432,
        'NAME': name,
        'USER': 'internal_backend',
        'PASSWORD': os.environ['INTERNAL_BACKEND_DB_PASSWORD'],
    }


DATABASES = {
    'default': _db('customer-db', 'customer'),
    'orders': _db('orders-db', 'orders'),
    'finance': _db('finance-db', 'finance'),
}

USE_TZ = True
APPEND_SLASH = False
