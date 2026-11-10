"""Invite links: commissioner creates, a person accepts once (R2, KTD4)."""

import re
from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from accounts import tokens
from accounts.models import AccountToken
from leagues.models import Membership

User = get_user_model()

SIGNUP = {
    "first_name": "Riley Test",
    "username": "riley",
    "password1": "a-long-test-pass-77",
    "password2": "a-long-test-pass-77",
}


@pytest.fixture
def setup(make_user, make_league):
    commissioner = make_user("comm")
    member = make_user("member")
    league = make_league("League A", commissioner, members=[(member, "Alpha")])["league"]
    return {"commissioner": commissioner, "member": member, "league": league}


def invite_link(setup):
    return f"/invite/{tokens.create_invite(setup['league'], created_by=setup['commissioner'])}/"


def link_in(response):
    match = re.search(r'value="(https?://[^"]+)"', response.content.decode())
    assert match, "link not shown"
    return match.group(1).replace("http://testserver", "")


def test_commissioner_creates_an_invite_and_sees_the_link_once(client, setup):
    client.force_login(setup["commissioner"])
    url = f"/league/{setup['league'].pk}/invites/new/"
    response = client.post(url, {"invitee_name": "Riley"})
    assert response.status_code == 200
    assert "no-store" in response["Cache-Control"]
    link = link_in(response)
    token = AccountToken.objects.get(purpose=AccountToken.Purpose.INVITE)
    assert token.created_by == setup["commissioner"]
    assert token.invitee_name == "Riley"
    # The link works for the invitee.
    assert client.get(link).status_code == 200


def test_member_cannot_create_invites(client, setup):
    client.force_login(setup["member"])
    response = client.post(f"/league/{setup['league'].pk}/invites/new/")
    assert response.status_code == 403
    assert not AccountToken.objects.exists()


def test_accepting_creates_account_and_membership_and_signs_in(client, setup):
    response = client.post(invite_link(setup), SIGNUP)
    assert response.status_code == 302
    user = User.objects.get(username="riley")
    assert user.first_name == "Riley Test"
    assert user.check_password("a-long-test-pass-77")
    membership = Membership.objects.get(user=user)
    assert membership.league == setup["league"]
    assert membership.is_commissioner is False
    assert client.session["_auth_user_id"] == str(user.pk)


def test_invite_works_once(client, setup):
    link = invite_link(setup)
    client.post(link, SIGNUP)
    client.logout()
    again = client.post(link, dict(SIGNUP, username="riley2"))
    assert "already been used" in again.content.decode()
    assert not User.objects.filter(username="riley2").exists()


def test_expired_invite_is_rejected(client, setup):
    link = invite_link(setup)
    AccountToken.objects.update(expires_at=timezone.now() - timedelta(minutes=1))
    response = client.post(link, SIGNUP)
    assert "expired" in response.content.decode()
    assert not User.objects.filter(username="riley").exists()


def test_tampered_invite_is_rejected(client, setup):
    link = invite_link(setup)
    tampered = link[:-2] + ("A" if link[-2] != "A" else "B") + "/"
    response = client.post(tampered, SIGNUP)
    assert "Check that you copied all of it" in response.content.decode()
    assert not User.objects.filter(username="riley").exists()


def test_failed_signup_does_not_use_up_the_invite(client, setup):
    link = invite_link(setup)
    client.post(link, dict(SIGNUP, password2="mismatch"))
    assert AccountToken.objects.get().used_at is None
    assert client.post(link, SIGNUP).status_code == 302


def test_signed_in_user_joins_without_a_second_account(client, setup, make_user, make_league):
    other = make_user("other")
    make_league("League B", make_user("b-comm"), members=[(other, "Alpha")])
    link = invite_link(setup)
    client.force_login(other)
    assert client.get(link).status_code == 200
    response = client.post(link)
    assert response.status_code == 302
    assert set(Membership.objects.filter(user=other).values_list("league__name", flat=True)) == {
        "League A",
        "League B",
    }
    assert User.objects.count() == 4
    assert AccountToken.objects.get().used_at is not None
