"""Password-reset links (R4, F3, KTD11)."""

import re
from datetime import timedelta

import pytest
from django.utils import timezone

from accounts import tokens
from accounts.models import AccountToken
from leagues.models import Membership

from .conftest import current_code, sign_in

NEW_PASSWORD = {"new_password1": "fresh-test-pass-55", "new_password2": "fresh-test-pass-55"}


@pytest.fixture
def world(make_user, make_league):
    a_comm = make_user("a-comm")
    jordan = make_user("jordan")
    a = make_league("League A", a_comm, members=[(jordan, "Alpha")])
    b_comm = make_user("b-comm")
    b_member = make_user("b-member")
    b = make_league("League B", b_comm, members=[(b_member, "Alpha")])
    admin = make_user("owner", is_superuser=True, is_staff=True)
    return {"a": a["league"], "b": b["league"], "a_comm": a_comm, "jordan": jordan,
            "b_comm": b_comm, "b_member": b_member, "admin": admin}


def reset_url(league, user):
    return f"/league/{league.pk}/members/{user.pk}/reset-link/"


def link_in(response):
    match = re.search(r'value="(https?://[^"]+)"', response.content.decode())
    assert match, "link not shown"
    return match.group(1).replace("http://testserver", "")


# --- Who can generate one ------------------------------------------------------------


def test_commissioner_resets_own_member(client, world):
    client.force_login(world["a_comm"])
    response = client.post(reset_url(world["a"], world["jordan"]))
    assert response.status_code == 200
    assert "no-store" in response["Cache-Control"]
    link = link_in(response)
    assert link.startswith("/reset/")
    token = AccountToken.objects.get(purpose=AccountToken.Purpose.RESET)
    assert (token.user, token.created_by) == (world["jordan"], world["a_comm"])


def test_member_of_another_league_is_404(client, world):
    client.force_login(world["a_comm"])
    assert client.post(reset_url(world["a"], world["b_member"])).status_code == 404
    assert not AccountToken.objects.exists()


def test_site_admin_in_the_league_is_403(client, world):
    Membership.objects.create(league=world["a"], user=world["admin"])
    client.force_login(world["a_comm"])
    assert client.post(reset_url(world["a"], world["admin"])).status_code == 403
    assert not AccountToken.objects.exists()


def test_member_of_two_leagues_is_403_for_the_commissioner_of_only_one(client, world, login_verified):
    Membership.objects.create(league=world["b"], user=world["jordan"])
    client.force_login(world["b_comm"])
    assert client.post(reset_url(world["b"], world["jordan"])).status_code == 403
    assert not AccountToken.objects.exists()
    # The site admin can still reset them.
    login_verified(client, world["admin"])
    response = client.post(f"/site-admin/users/{world['jordan'].pk}/reset-link/")
    assert link_in(response).startswith("/reset/")


def test_commissioner_of_both_leagues_resets_member_of_both(client, world):
    Membership.objects.create(league=world["b"], user=world["jordan"])
    Membership.objects.create(league=world["b"], user=world["a_comm"], is_commissioner=True)
    client.force_login(world["a_comm"])
    assert link_in(client.post(reset_url(world["a"], world["jordan"]))).startswith("/reset/")


def test_plain_member_cannot_reset(client, world, make_user):
    other = make_user("other")
    Membership.objects.create(league=world["a"], user=other)
    client.force_login(world["jordan"])
    assert client.post(reset_url(world["a"], other)).status_code == 403


# --- Using a link --------------------------------------------------------------------


def make_link(world, creator="a_comm"):
    return f"/reset/{tokens.create_reset(world['jordan'], created_by=world[creator])}/"


def test_opening_the_link_does_not_use_it(client, world):
    # Chat apps fetch links to build previews; only submitting the form uses one.
    link = make_link(world)
    assert client.get(link).status_code == 200
    assert client.get(link).status_code == 200
    assert AccountToken.objects.get().used_at is None


def test_reset_sets_the_password_once_and_logs_the_ip(client, world):
    link = make_link(world)
    response = client.post(link, NEW_PASSWORD, HTTP_X_FORWARDED_FOR="10.0.0.1, 203.0.113.50")
    assert response.status_code == 302
    assert response["Location"].startswith("/accounts/login/")
    world["jordan"].refresh_from_db()
    assert world["jordan"].check_password("fresh-test-pass-55")
    # The link does not sign anyone in.
    assert "_auth_user_id" not in client.session
    token = AccountToken.objects.get()
    assert token.used_ip == "203.0.113.50"

    again = client.post(link, {"new_password1": "other-pass-99x", "new_password2": "other-pass-99x"})
    assert "already been used" in again.content.decode()
    world["jordan"].refresh_from_db()
    assert world["jordan"].check_password("fresh-test-pass-55")


def test_expired_reset_link_is_rejected(client, world):
    link = make_link(world)
    AccountToken.objects.update(expires_at=timezone.now() - timedelta(minutes=1))
    assert "expired" in client.post(link, NEW_PASSWORD).content.decode()
    world["jordan"].refresh_from_db()
    assert not world["jordan"].check_password("fresh-test-pass-55")


def test_second_link_voids_the_first(client, world):
    first = make_link(world)
    second = make_link(world)
    assert client.post(first, NEW_PASSWORD).status_code == 200
    world["jordan"].refresh_from_db()
    assert not world["jordan"].check_password("fresh-test-pass-55")
    assert client.post(second, NEW_PASSWORD).status_code == 302


def test_reset_keeps_2fa_and_the_next_sign_in_shows_the_notice_once(client, world, make_device):
    device = make_device(world["jordan"])
    client.post(make_link(world), NEW_PASSWORD)
    assert device.__class__.objects.filter(pk=device.pk, confirmed=True).exists()

    # The new password alone is not enough: the TOTP step still applies.
    response = sign_in(client, "jordan", "fresh-test-pass-55")
    assert response["Location"] == "/accounts/login/verify/"
    assert "_auth_user_id" not in client.session
    response = client.post("/accounts/login/verify/", {"code": current_code(device)}, follow=True)
    assert "_auth_user_id" in client.session
    assert "reset by a-comm" in response.content.decode()

    client.logout()
    device.refresh_from_db()
    device.last_t = -1  # let the same 30-second code verify again
    device.save()
    sign_in(client, "jordan", "fresh-test-pass-55")
    response = client.post("/accounts/login/verify/", {"code": current_code(device)}, follow=True)
    assert "_auth_user_id" in client.session
    assert "reset by a-comm" not in response.content.decode()


def test_used_reset_shows_on_account_page_and_in_site_admin_log(client, world, login_verified):
    client.post(make_link(world), NEW_PASSWORD, REMOTE_ADDR="192.0.2.77")

    world["jordan"].refresh_from_db()
    client.force_login(world["jordan"])
    page = client.get("/accounts/").content.decode()
    assert "a-comm" in page
    assert "192.0.2.77" in page

    login_verified(client, world["admin"])
    log = client.get("/site-admin/").content.decode()
    assert "jordan" in log and "a-comm" in log and "192.0.2.77" in log
    assert "Used" in log


# --- The creator's authority is checked again when the link is used -------------------


def test_link_is_void_once_the_member_joins_a_league_the_creator_does_not_run(client, world):
    link = make_link(world)
    Membership.objects.create(league=world["b"], user=world["jordan"])
    assert "revoked" in client.get(link).content.decode()
    response = client.post(link, NEW_PASSWORD)
    assert response.status_code == 200
    assert "revoked" in response.content.decode()
    world["jordan"].refresh_from_db()
    assert not world["jordan"].check_password("fresh-test-pass-55")
    assert AccountToken.objects.get().used_at is None


def test_authority_lost_between_opening_and_submitting_voids_the_link(client, world, monkeypatch):
    # The check also runs inside the transaction that uses the link, after the lookup.
    from accounts import views

    class JoinsBWhileSubmitting(views.SetPasswordForm):
        def is_valid(self):
            Membership.objects.get_or_create(league=world["b"], user=world["jordan"])
            return super().is_valid()

    monkeypatch.setattr(views, "SetPasswordForm", JoinsBWhileSubmitting)
    link = make_link(world)
    response = client.post(link, NEW_PASSWORD)
    assert "revoked" in response.content.decode()
    world["jordan"].refresh_from_db()
    assert not world["jordan"].check_password("fresh-test-pass-55")
    assert AccountToken.objects.get().used_at is None


def test_link_from_a_disabled_creator_no_longer_works(client, world):
    link = make_link(world)
    world["a_comm"].is_active = False
    world["a_comm"].save()
    assert "revoked" in client.post(link, NEW_PASSWORD).content.decode()
    world["jordan"].refresh_from_db()
    assert not world["jordan"].check_password("fresh-test-pass-55")


def test_link_whose_creator_was_deleted_is_void(client, world):
    link = make_link(world)
    world["a_comm"].delete()
    assert "revoked" in client.post(link, NEW_PASSWORD).content.decode()
    world["jordan"].refresh_from_db()
    assert not world["jordan"].check_password("fresh-test-pass-55")


def test_disabling_the_creator_voids_their_unspent_links(client, world, login_verified):
    link = make_link(world)
    login_verified(client, world["admin"])
    client.post(f"/site-admin/users/{world['a_comm'].pk}/active/")
    assert AccountToken.objects.get().voided_at is not None
    # Re-enabling does not bring the link back.
    client.post(f"/site-admin/users/{world['a_comm'].pk}/active/")
    client.logout()
    assert "revoked" in client.post(link, NEW_PASSWORD).content.decode()
    world["jordan"].refresh_from_db()
    assert not world["jordan"].check_password("fresh-test-pass-55")


def test_site_admin_link_still_works_for_a_member_of_several_leagues(client, world):
    Membership.objects.create(league=world["b"], user=world["jordan"])
    link = make_link(world, creator="admin")
    assert client.post(link, NEW_PASSWORD).status_code == 302
    world["jordan"].refresh_from_db()
    assert world["jordan"].check_password("fresh-test-pass-55")


def test_site_admin_link_is_void_once_the_creator_is_no_longer_site_admin(client, world):
    Membership.objects.create(league=world["b"], user=world["jordan"])
    link = make_link(world, creator="admin")
    world["admin"].is_superuser = False
    world["admin"].save()
    assert "revoked" in client.post(link, NEW_PASSWORD).content.decode()
    world["jordan"].refresh_from_db()
    assert not world["jordan"].check_password("fresh-test-pass-55")


def test_commissioner_link_still_works_while_their_authority_holds(client, world):
    link = make_link(world)
    assert client.get(link).status_code == 200
    assert client.post(link, NEW_PASSWORD).status_code == 302
    world["jordan"].refresh_from_db()
    assert world["jordan"].check_password("fresh-test-pass-55")
