"""Settings for the test suite: production settings with test-only inputs.

Security settings are inherited unchanged. Only the inputs a test run cannot
provide (secret key, hosts, data and backup dirs) and test-speed knobs are set here.
"""

import os
import tempfile

os.environ.setdefault("DJANGO_SECRET_KEY", "test-only-secret-key-not-used-anywhere-else-0123456789")
os.environ.setdefault("DJANGO_ALLOWED_HOSTS", "testserver,localhost")
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="madden-tracker-test-"))
os.environ.setdefault("BACKUP_DIR", tempfile.mkdtemp(prefix="madden-tracker-test-backups-"))
os.environ.setdefault("DJANGO_DEBUG", "0")

from .settings import *  # noqa: E402,F403

# Tests use the plain test client over HTTP; the redirect is tested explicitly
# with override_settings in tests/test_settings_security.py.
SECURE_SSL_REDIRECT = False

# Fast hashing for test users; production uses Argon2 (asserted in tests).
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

# No collectstatic manifest during tests.
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}
