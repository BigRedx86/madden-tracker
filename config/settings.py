"""Django settings for the Madden League Tracker.

Everything deployment-specific comes from the environment (KTD12):

- DJANGO_DEBUG: "1" for local development only. Defaults to off.
- DJANGO_SECRET_KEY: the secret key. If unset, it is read from
  DATA_DIR/secret_key (the container entrypoint creates that file).
- DJANGO_ALLOWED_HOSTS: comma-separated host names, e.g. "madden.example.com".
- DJANGO_CSRF_TRUSTED_ORIGINS: comma-separated origins, e.g. "https://madden.example.com".
- DJANGO_SECURE_SSL_REDIRECT: "0" to turn off the HTTP-to-HTTPS redirect.
- DJANGO_HSTS_SECONDS: HSTS max-age. Defaults to one year.
- DJANGO_TIME_ZONE: display time zone. Defaults to UTC.
- DATA_DIR: where the SQLite database and secret key live. "/data" in the container.
- BACKUP_DIR: where backup_db writes snapshots. Defaults to "/backups".

Without DEBUG, a missing secret key or ALLOWED_HOSTS stops startup.
"""

import os
from datetime import timedelta
from pathlib import Path

from csp.constants import NONE, SELF
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent


def env_bool(name, default):
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def env_list(name):
    return [item.strip() for item in os.environ.get(name, "").split(",") if item.strip()]


DEBUG = env_bool("DJANGO_DEBUG", False)
DATA_DIR = Path(os.environ.get("DATA_DIR") or BASE_DIR / "data")
BACKUP_DIR = Path(os.environ.get("BACKUP_DIR") or "/backups")


def read_secret_key():
    key = os.environ.get("DJANGO_SECRET_KEY", "").strip()
    if key:
        return key
    key_file = DATA_DIR / "secret_key"
    if key_file.is_file():
        key = key_file.read_text().strip()
        if key:
            return key
    if DEBUG:
        return "django-insecure-local-development-only"
    raise ImproperlyConfigured(
        f"No secret key: set DJANGO_SECRET_KEY or create {key_file}."
    )


SECRET_KEY = read_secret_key()

ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS")
if not ALLOWED_HOSTS:
    if DEBUG:
        ALLOWED_HOSTS = ["localhost", "127.0.0.1"]
    else:
        raise ImproperlyConfigured("DJANGO_ALLOWED_HOSTS must be set when DEBUG is off.")
CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS")

INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "csp",
    "axes",
    "django_otp",
    "django_otp.plugins.otp_totp",
    "accounts",
    "leagues",
    "stats",
    "ops",
    "importer",
]

MIDDLEWARE = [
    # First, so the container healthcheck skips host validation and the HTTPS redirect.
    "ops.middleware.HealthCheckMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django_otp.middleware.OTPMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "csp.middleware.CSPMiddleware",
    # Last, as axes requires: turns a locked-out sign-in into the lockout page.
    "axes.middleware.AxesMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "leagues.context_processors.league_nav",
            ],
        },
    },
]

# SQLite in WAL mode on the data volume (KTD2). IMMEDIATE transactions take the
# write lock up front, so concurrent writers wait on the busy timeout instead of
# failing mid-transaction.
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": DATA_DIR / "db.sqlite3",
        "OPTIONS": {
            "transaction_mode": "IMMEDIATE",
            "timeout": 5,
            "init_command": "PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL;",
        },
    }
}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2SHA1PasswordHasher",
    "django.contrib.auth.hashers.ScryptPasswordHasher",
]
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
LOGIN_URL = "/accounts/login/"
LOGIN_REDIRECT_URL = "/"

# Axes first: it refuses locked-out sign-ins before ModelBackend checks the password.
# ModelBackend also refuses inactive users on every request, so disabling an
# account ends its sessions (R6).
AUTHENTICATION_BACKENDS = [
    "axes.backends.AxesStandaloneBackend",
    "django.contrib.auth.backends.ModelBackend",
]

# Lockout (R5, KTD5): 5 failures for one username from one client IP, then a
# 15-minute cool-off. The client IP is the one SWAG appends (accounts.client_ip).
AXES_FAILURE_LIMIT = 5
AXES_COOLOFF_TIME = timedelta(minutes=15)
AXES_LOCKOUT_PARAMETERS = [["username", "ip_address"]]
AXES_CLIENT_IP_CALLABLE = "accounts.client_ip.client_ip"
AXES_LOCKOUT_TEMPLATE = "accounts/locked_out.html"

# TOTP 2FA (R3): the issuer is the name shown in authenticator apps.
OTP_TOTP_ISSUER = "Madden League Tracker"

# Sessions and CSRF (KTD5): secure, HttpOnly, SameSite=Lax, 14-day sessions.
SESSION_COOKIE_AGE = 14 * 24 * 60 * 60
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SECURE = not DEBUG

# Behind SWAG (R30): SWAG terminates TLS and sets X-Forwarded-Proto.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = env_bool("DJANGO_SECURE_SSL_REDIRECT", not DEBUG)
SECURE_HSTS_SECONDS = 0 if DEBUG else int(os.environ.get("DJANGO_HSTS_SECONDS", 31536000))
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
# Preload stays off: the preload list is a commitment for a whole apex domain,
# which this app (served from a subdomain) cannot make on the owner's behalf.
SECURE_HSTS_PRELOAD = False
SILENCED_SYSTEM_CHECKS = ["security.W021"]
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"

# Strict CSP: self-only, no inline script or style (KTD12).
CONTENT_SECURITY_POLICY = {
    "DIRECTIVES": {
        "default-src": [SELF],
        "script-src": [SELF],
        "style-src": [SELF],
        "img-src": [SELF],
        "font-src": [SELF],
        "connect-src": [SELF],
        "form-action": [SELF],
        "base-uri": [SELF],
        "object-src": [NONE],
        "frame-ancestors": [NONE],
    }
}

LANGUAGE_CODE = "en-us"
TIME_ZONE = os.environ.get("DJANGO_TIME_ZONE", "UTC")
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": "INFO"},
}
