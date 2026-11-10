FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DATA_DIR=/data

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Static files are collected at build time. These placeholder values exist only
# for this one command and are not kept in the image.
RUN DJANGO_SECRET_KEY=collectstatic-build-only DJANGO_ALLOWED_HOSTS=localhost \
    python manage.py collectstatic --noinput

# Run as Unraid's nobody:users so the app (and every docker exec) writes
# appdata and backups with the right owner.
RUN mkdir -p /data /backups && chown 99:100 /data /backups \
    && chmod +x docker/entrypoint.sh
USER 99:100
VOLUME ["/data", "/backups"]

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).read() == b'ok' else 1)"]

ENTRYPOINT ["docker/entrypoint.sh"]
