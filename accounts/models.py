"""Invite and password-reset tokens (KTD4); used reset tokens are the reset log (KTD11)."""

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone

from leagues.models import League


class AccountTokenQuerySet(models.QuerySet):
    def unspent(self):
        """Tokens not yet used and not voided (expiry is checked separately)."""
        return self.filter(used_at__isnull=True, voided_at__isnull=True)


class AccountToken(models.Model):
    """A single-use link. Only the SHA-256 hash of the random token is stored."""

    objects = AccountTokenQuerySet.as_manager()

    # Invites belong to a league; leagues.access.get_in_league scopes by this.
    league_lookup = "league"

    class Purpose(models.TextChoices):
        INVITE = "invite", "Invite"
        RESET = "reset", "Password reset"

    purpose = models.CharField(max_length=6, choices=Purpose.choices)
    token_hash = models.CharField(max_length=64, unique=True)
    # Invites: the league to join, and a note on who the invite is for.
    league = models.ForeignKey(League, on_delete=models.CASCADE, null=True, blank=True, related_name="invites")
    invitee_name = models.CharField(max_length=150, blank=True)
    # Resets: whose password the link sets.
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, null=True, blank=True, related_name="reset_tokens"
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    created_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)
    used_ip = models.GenericIPAddressField(null=True, blank=True)
    # Set when a newer reset link replaces this one, or an invite is revoked.
    voided_at = models.DateTimeField(null=True, blank=True)
    # Resets: when the "your password was reset" notice was shown after sign-in.
    notice_shown_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [
            models.CheckConstraint(
                condition=(Q(purpose="invite", league__isnull=False, user__isnull=True))
                | (Q(purpose="reset", user__isnull=False, league__isnull=True)),
                name="token_target_matches_purpose",
            ),
        ]

    def __str__(self):
        return f"{self.get_purpose_display()} created {self.created_at:%Y-%m-%d %H:%M}"

    @property
    def status(self):
        if self.used_at:
            return "Used"
        if self.voided_at:
            return "Replaced"
        if self.expires_at <= timezone.now():
            return "Expired"
        return "Unused"
