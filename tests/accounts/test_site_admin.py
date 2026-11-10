"""Site-admin pages: users, disable, reset links, 2FA reset (R6, KTD10)."""

import pytest
from django.contrib.auth import get_user_model
from django.urls import get_resolver
from django_otp.plugins.otp_totp.models import TOTPDevice

from accounts.models import AccountToken

from .conftest import sign_in

User = get_user_model()


@pytest.fixture
def admin(make_user):
    return make_user("owner", is_superuser=True, is_staff=True)


def site_admin_pages(user_pk):
    return [
        ("get", "/site-admin/"),
        ("post", f"/site-admin/users/{user_pk}/active/"),
        ("post", f"/site-admin/users/{user_pk}/reset-link/"),
        ("post", f"/site-admin/users/{user_pk}/reset-2fa/"),
    ]


def test_non_superuser_gets_404_even_with_2fa(client, make_user, login_verified):
    user = make_user("jordan")
    login_verified(client, user)
    for method, url in site_admin_pages(user.pk):
        assert getattr(client, method)(url).status_code == 404, url


def test_superuser_without_2fa_gets_404(client, admin):
    client.force_login(admin)
    for method, url in site_admin_pages(admin.pk):
        assert getattr(client, method)(url).status_code == 404, url


def test_superuser_with_device_but_unverified_session_gets_404(client, admin, make_device):
    make_device(admin)
    client.force_login(admin)
    assert client.get("/site-admin/").status_code == 404


def test_users_page_lists_everyone_and_has_backup_slot(client, admin, make_user, login_verified):
    make_user("jordan")
    make_user("blake")
    login_verified(client, admin)
    page = client.get("/site-admin/").content.decode()
    for name in ("owner", "jordan", "blake"):
        assert name in page
    assert 'id="backup-status"' in page


def test_disabling_ends_existing_session_and_blocks_sign_in(client, admin, make_user, login_verified):
    from django.test import Client

    jordan = make_user("jordan")
    jordan_client = Client()
    jordan_client.force_login(jordan)
    assert jordan_client.get("/accounts/").status_code == 200

    login_verified(client, admin)
    client.post(f"/site-admin/users/{jordan.pk}/active/")
    jordan.refresh_from_db()
    assert jordan.is_active is False

    response = jordan_client.get("/accounts/")
    assert response.status_code == 302
    assert response["Location"].startswith("/accounts/login/")
    fresh = Client()
    sign_in(fresh, "jordan")
    assert "_auth_user_id" not in fresh.session

    # Re-enable.
    client.post(f"/site-admin/users/{jordan.pk}/active/")
    jordan.refresh_from_db()
    assert jordan.is_active is True
    assert sign_in(fresh, "jordan").status_code == 302


def test_site_admin_resets_2fa(client, admin, make_user, make_device, login_verified):
    jordan = make_user("jordan")
    make_device(jordan)
    login_verified(client, admin)
    client.post(f"/site-admin/users/{jordan.pk}/reset-2fa/")
    assert not TOTPDevice.objects.filter(user=jordan).exists()
    # Jordan now signs in with the password alone.
    from django.test import Client

    fresh = Client()
    assert sign_in(fresh, "jordan")["Location"] != "/accounts/login/verify/"
    assert "_auth_user_id" in fresh.session


def test_site_admin_generates_reset_link_for_anyone(client, admin, make_user, login_verified):
    other_admin = make_user("other-admin", is_superuser=True)
    login_verified(client, admin)
    response = client.post(f"/site-admin/users/{other_admin.pk}/reset-link/")
    assert response.status_code == 200
    assert "no-store" in response["Cache-Control"]
    assert 'value="http://testserver/reset/' in response.content.decode()
    assert AccountToken.objects.get().user == other_admin


def test_django_admin_is_not_routed():
    routes = [str(p.pattern) for p in get_resolver().url_patterns]
    assert not any(route.startswith("admin") for route in routes)
