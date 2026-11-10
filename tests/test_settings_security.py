"""Transport and header security (R30, KTD12)."""

import os
import subprocess
import sys
from pathlib import Path

import pytest
from django.test import override_settings

REPO_ROOT = Path(__file__).resolve().parent.parent


def production_env(tmp_path):
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("DJANGO_") and key != "DATA_DIR"
    }
    env.update(
        {
            "DJANGO_SETTINGS_MODULE": "config.settings",
            "DJANGO_SECRET_KEY": "x7Gq2-not-a-real-key-but-long-enough-for-the-deploy-check-9fK",
            "DJANGO_ALLOWED_HOSTS": "madden.example.com",
            "DJANGO_CSRF_TRUSTED_ORIGINS": "https://madden.example.com",
            "DATA_DIR": str(tmp_path),
        }
    )
    return env


def run_manage(args, env):
    return subprocess.run(
        [sys.executable, "manage.py", *args],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )


def test_check_deploy_reports_no_warnings_with_production_settings(tmp_path):
    result = run_manage(["check", "--deploy", "--fail-level", "WARNING"], production_env(tmp_path))
    assert result.returncode == 0, result.stdout + result.stderr
    assert "WARNINGS" not in result.stderr


def test_production_refuses_to_start_without_secret_key(tmp_path):
    env = production_env(tmp_path)
    del env["DJANGO_SECRET_KEY"]
    result = run_manage(["check"], env)
    assert result.returncode != 0
    assert "secret key" in result.stderr.lower()


def test_production_reads_secret_key_from_data_dir_file(tmp_path):
    env = production_env(tmp_path)
    del env["DJANGO_SECRET_KEY"]
    (tmp_path / "secret_key").write_text("file-based-secret-key-long-enough-for-the-deploy-check-Zq81\n")
    result = run_manage(["check", "--deploy", "--fail-level", "WARNING"], env)
    assert result.returncode == 0, result.stdout + result.stderr


def test_production_refuses_to_start_without_allowed_hosts(tmp_path):
    env = production_env(tmp_path)
    del env["DJANGO_ALLOWED_HOSTS"]
    result = run_manage(["check"], env)
    assert result.returncode != 0
    assert "allowed_hosts" in result.stderr.lower()


@pytest.mark.urls("tests.urls_security")
@override_settings(SECURE_SSL_REDIRECT=True)
def test_forwarded_proto_https_is_secure_and_cookies_get_secure_flag(client, db):
    response = client.get("/probe/", HTTP_X_FORWARDED_PROTO="https")
    assert response.status_code == 200
    assert response.content == b"secure=True"
    assert response.cookies["sessionid"]["secure"] is True
    assert response.cookies["sessionid"]["httponly"] is True
    assert response.cookies["sessionid"]["samesite"] == "Lax"
    assert response.cookies["csrftoken"]["secure"] is True


@pytest.mark.urls("tests.urls_security")
@override_settings(SECURE_SSL_REDIRECT=True)
def test_plain_http_is_redirected_to_https(client):
    response = client.get("/probe/")
    assert response.status_code == 301
    assert response["Location"].startswith("https://")


@pytest.mark.urls("tests.urls_security")
def test_unknown_host_is_rejected(client):
    response = client.get("/probe/", HTTP_HOST="evil.example.net")
    assert response.status_code == 400


@pytest.mark.urls("tests.urls_security")
def test_security_headers_present(client, db):
    response = client.get("/probe/", HTTP_X_FORWARDED_PROTO="https")
    csp = response["Content-Security-Policy"]
    assert "default-src 'self'" in csp
    assert "script-src 'self'" in csp
    assert "unsafe-inline" not in csp
    assert "frame-ancestors 'none'" in csp
    assert response["X-Frame-Options"] == "DENY"
    assert response["X-Content-Type-Options"] == "nosniff"
    assert response["Referrer-Policy"] == "same-origin"
    assert "max-age=31536000" in response["Strict-Transport-Security"]


def test_argon2_is_the_production_password_hasher():
    from config import settings as production_settings

    assert production_settings.PASSWORD_HASHERS[0] == "django.contrib.auth.hashers.Argon2PasswordHasher"


def test_sqlite_uses_wal_and_immediate_transactions(db):
    from django.db import connection

    with connection.cursor() as cursor:
        cursor.execute("PRAGMA busy_timeout")
        assert cursor.fetchone()[0] == 5000
    options = connection.settings_dict["OPTIONS"]
    assert options["transaction_mode"] == "IMMEDIATE"
    assert "journal_mode=WAL" in options["init_command"]


def test_no_admin_route_is_installed():
    from django.conf import settings

    assert "django.contrib.admin" not in settings.INSTALLED_APPS
