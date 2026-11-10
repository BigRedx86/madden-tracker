"""Create, look up, and consume invite and reset tokens (KTD4).

Raw tokens are 32 random bytes, URL-safe. They exist only in the link shown to
the creator once; the database keeps their SHA-256 hash.
"""

import hashlib
import secrets
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from leagues import policy

from .models import AccountToken

INVITE_LIFETIME = timedelta(days=7)
RESET_LIFETIME = timedelta(hours=24)


class TokenError(Exception):
    MESSAGES = {
        "invalid": "This link isn't valid. Check that you copied all of it.",
        "used": "This link has already been used.",
        "expired": "This link has expired. Ask for a new one.",
        "voided": "This link was replaced by a newer one or revoked. Ask for a new one.",
    }

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason
        self.message = self.MESSAGES[reason]


def hash_token(raw):
    return hashlib.sha256(raw.encode()).hexdigest()


def _create(purpose, lifetime, created_by, **target):
    raw = secrets.token_urlsafe(32)
    now = timezone.now()
    AccountToken.objects.create(
        purpose=purpose,
        token_hash=hash_token(raw),
        created_by=created_by,
        created_at=now,
        expires_at=now + lifetime,
        **target,
    )
    return raw


def create_invite(league, created_by, invitee_name=""):
    return _create(AccountToken.Purpose.INVITE, INVITE_LIFETIME, created_by, league=league, invitee_name=invitee_name)


def create_reset(target, created_by):
    """A new reset link for ``target``; any earlier unused one stops working."""
    with transaction.atomic():
        AccountToken.objects.unspent().filter(purpose=AccountToken.Purpose.RESET, user=target).update(
            voided_at=timezone.now()
        )
        return _create(AccountToken.Purpose.RESET, RESET_LIFETIME, created_by, user=target)


def lookup(raw, purpose):
    """The usable token for ``raw``, or TokenError saying why it can't be used."""
    token = (
        AccountToken.objects.select_related("league", "user", "created_by")
        .filter(token_hash=hash_token(raw), purpose=purpose)
        .first()
    )
    if token is None:
        raise TokenError("invalid")
    if token.used_at:
        raise TokenError("used")
    if token.voided_at:
        raise TokenError("voided")
    if token.expires_at <= timezone.now():
        raise TokenError("expired")
    return token


def check_reset_authority(token):
    """Raise TokenError("voided") unless the reset link's creator could still make it.

    A commissioner's authority is checked when the link is made and again each time it
    is used: the creator must still be active and still pass
    ``policy.can_generate_reset_link`` for the target (a site admin stays valid while
    still a site admin). A link whose creator was deleted is void. Reads the creator and
    target fresh, so call it inside the consuming transaction too.
    """
    if token.created_by_id is None:
        raise TokenError("voided")
    users = get_user_model().objects
    creator = users.filter(pk=token.created_by_id, is_active=True).first()
    target = users.filter(pk=token.user_id).first()
    if creator is None or target is None or not policy.can_generate_reset_link(creator, target):
        raise TokenError("voided")


def void_created_by(user):
    """Void every unspent token ``user`` created (used when the account is disabled)."""
    return AccountToken.objects.unspent().filter(created_by=user).update(voided_at=timezone.now())


def consume(token, ip):
    """Mark ``token`` used, atomically: of two concurrent uses, only one succeeds.

    Call inside the transaction that does the token's work, so a failure rolls both back.
    """
    now = timezone.now()
    claimed = (
        AccountToken.objects.unspent()
        .filter(pk=token.pk, expires_at__gt=now)
        .update(used_at=now, used_ip=ip)
    )
    if not claimed:
        raise TokenError("used")
    token.used_at, token.used_ip = now, ip
