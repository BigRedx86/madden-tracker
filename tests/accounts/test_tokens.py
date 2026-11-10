"""Invite and reset tokens: random, stored hashed, single use, expiring (KTD4)."""

import hashlib
from datetime import timedelta

import pytest
from django.utils import timezone

from accounts import tokens
from accounts.models import AccountToken


@pytest.fixture
def league(make_user, make_league):
    return make_league("League A", make_user("comm"))["league"]


def test_raw_token_is_long_and_only_its_hash_is_stored(league):
    raw = tokens.create_invite(league, created_by=None)
    stored = AccountToken.objects.get()
    assert len(raw) >= 43  # 32 random bytes, URL-safe base64
    assert stored.token_hash == hashlib.sha256(raw.encode()).hexdigest()
    assert raw not in str(AccountToken.objects.values().get())


def test_default_lifetimes(league, make_user):
    tokens.create_invite(league, created_by=None)
    tokens.create_reset(make_user("someone"), created_by=None)
    invite = AccountToken.objects.get(purpose=AccountToken.Purpose.INVITE)
    reset = AccountToken.objects.get(purpose=AccountToken.Purpose.RESET)
    assert invite.expires_at - invite.created_at == timedelta(days=7)
    assert reset.expires_at - reset.created_at == timedelta(hours=24)


def test_lookup_reasons(league):
    raw = tokens.create_invite(league, created_by=None)
    assert tokens.lookup(raw, AccountToken.Purpose.INVITE).league == league

    with pytest.raises(tokens.TokenError) as wrong_purpose:
        tokens.lookup(raw, AccountToken.Purpose.RESET)
    assert wrong_purpose.value.reason == "invalid"

    tampered = raw[:-1] + ("A" if raw[-1] != "A" else "B")
    with pytest.raises(tokens.TokenError) as bad:
        tokens.lookup(tampered, AccountToken.Purpose.INVITE)
    assert bad.value.reason == "invalid"

    AccountToken.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
    with pytest.raises(tokens.TokenError) as expired:
        tokens.lookup(raw, AccountToken.Purpose.INVITE)
    assert expired.value.reason == "expired"


def test_consume_works_once(league):
    raw = tokens.create_invite(league, created_by=None)
    token = tokens.lookup(raw, AccountToken.Purpose.INVITE)
    tokens.consume(token, ip="203.0.113.9")
    # A second request that looked the token up before the first one consumed it.
    with pytest.raises(tokens.TokenError) as again:
        tokens.consume(token, ip="203.0.113.9")
    assert again.value.reason == "used"
    with pytest.raises(tokens.TokenError) as lookup_again:
        tokens.lookup(raw, AccountToken.Purpose.INVITE)
    assert lookup_again.value.reason == "used"
    stored = AccountToken.objects.get()
    assert stored.used_at is not None
    assert stored.used_ip == "203.0.113.9"


def test_new_reset_voids_the_earlier_unused_one(make_user):
    target = make_user("target")
    first = tokens.create_reset(target, created_by=None)
    second = tokens.create_reset(target, created_by=None)
    with pytest.raises(tokens.TokenError) as voided:
        tokens.lookup(first, AccountToken.Purpose.RESET)
    assert voided.value.reason == "voided"
    assert tokens.lookup(second, AccountToken.Purpose.RESET).user == target
