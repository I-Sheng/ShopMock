# Two stages from one base: `runtime` is what compose builds and ships;
# `test` adds only what the suite needs (node, for the PKCE S256 fallback
# check) and never reaches a deployed image.
#
#   docker build --target test -t shopmock-hr-portal-test .
#   docker run --rm shopmock-hr-portal-test
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

FROM base AS test
RUN apt-get update \
 && apt-get install -y --no-install-recommends nodejs \
 && rm -rf /var/lib/apt/lists/*
CMD ["python", "manage.py", "test", "-v", "2"]

FROM base AS runtime
# This service terminates browser requests and holds a database credential, so
# it has no reason to run as root.
RUN useradd --system --uid 10003 --no-create-home hr && chown -R hr:hr /app
USER hr

EXPOSE 8000
CMD ["gunicorn", "hr_portal.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "2"]
