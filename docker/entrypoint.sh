#!/bin/sh
# Runs as 99:100 (set in the Dockerfile), so there is no privilege drop here.
set -eu

DATA_DIR="${DATA_DIR:-/data}"

# Create a persistent secret key on first start unless one is supplied.
if [ -z "${DJANGO_SECRET_KEY:-}" ] && [ ! -s "$DATA_DIR/secret_key" ]; then
    umask 077
    python -c "import secrets; print(secrets.token_urlsafe(64))" > "$DATA_DIR/secret_key"
fi

python manage.py migrate --noinput

exec gunicorn config.wsgi:application \
    --bind 0.0.0.0:8000 \
    --workers "${GUNICORN_WORKERS:-2}" \
    --access-logfile - \
    --error-logfile -
