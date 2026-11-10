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
        ("get", f"/site-admin/users/{user_pk}/delete/"),
        ("post", f"/site-admin/users/{user_pk}/delete/"),
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


# --- Deleting an account ---------------------------------------------------------------


def test_deleting_an_account_keeps_their_player_and_stats_unclaimed(client, admin, make_user, make_league, login_verified):
    from leagues.models import Membership, Player
    from stats.models import StatLine

    jordan = make_user("jordan")
    built = make_league("Lakeside", make_user("comm"), members=[(jordan, "Jordan")], players=("Jordan",))
    player = built["players"]["Jordan"]
    StatLine.objects.create(player=player, game=built["game"], values={"receptions": 3})
    reset_made_by_jordan = AccountToken.objects.create(
        purpose=AccountToken.Purpose.RESET, token_hash="x" * 64, user=admin, created_by=jordan,
        expires_at="2099-01-01T00:00:00Z",
    )
    login_verified(client, admin)
    page = client.get(f"/site-admin/users/{jordan.pk}/delete/").content.decode()
    assert "Lakeside" in page and "Jordan" in page
    response = client.post(f"/site-admin/users/{jordan.pk}/delete/", {"confirm_username": "jordan"})
    assert response.status_code == 302
    assert not User.objects.filter(pk=jordan.pk).exists()
    assert not Membership.objects.filter(user_id=jordan.pk).exists()
    assert Player.objects.filter(pk=player.pk).exists()
    assert StatLine.objects.filter(player=player).count() == 1
    reset_made_by_jordan.refresh_from_db()
    assert reset_made_by_jordan.created_by is None


def test_deleting_an_account_needs_the_exact_username(client, admin, make_user, login_verified):
    jordan = make_user("jordan")
    login_verified(client, admin)
    response = client.post(f"/site-admin/users/{jordan.pk}/delete/", {"confirm_username": "Jordan"})
    assert response.status_code == 200
    assert "Type the username exactly" in response.content.decode()
    assert User.objects.filter(pk=jordan.pk).exists()


def test_the_last_commissioner_of_a_league_cannot_be_deleted(client, admin, make_user, make_league, login_verified):
    comm = make_user("comm")
    make_league("Lakeside", comm)
    login_verified(client, admin)
    page = client.get(f"/site-admin/users/{comm.pk}/delete/").content.decode()
    assert "Lakeside" in page and "only commissioner" in page
    response = client.post(f"/site-admin/users/{comm.pk}/delete/", {"confirm_username": "comm"}, follow=True)
    assert "only commissioner" in response.content.decode()
    assert User.objects.filter(pk=comm.pk).exists()


def test_a_commissioner_with_a_co_commissioner_can_be_deleted(client, admin, make_user, make_league, login_verified):
    from leagues.models import Membership

    comm, other = make_user("comm"), make_user("other")
    built = make_league("Lakeside", comm, members=[(other, None)])
    Membership.objects.filter(league=built["league"], user=other).update(is_commissioner=True)
    login_verified(client, admin)
    client.post(f"/site-admin/users/{comm.pk}/delete/", {"confirm_username": "comm"})
    assert not User.objects.filter(pk=comm.pk).exists()


def test_site_admin_cannot_delete_their_own_account(client, admin, login_verified):
    login_verified(client, admin)
    response = client.post(f"/site-admin/users/{admin.pk}/delete/", {"confirm_username": "owner"}, follow=True)
    assert "can't delete your own account" in response.content.decode().replace("&#x27;", "'")
    assert User.objects.filter(pk=admin.pk).exists()


def test_users_page_links_to_delete_except_for_yourself(client, admin, make_user, login_verified):
    jordan = make_user("jordan")
    login_verified(client, admin)
    page = client.get("/site-admin/").content.decode()
    assert f"/site-admin/users/{jordan.pk}/delete/" in page
    assert f"/site-admin/users/{admin.pk}/delete/" not in page
