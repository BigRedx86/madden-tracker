"""Site-admin pages (KTD10): users, disable/re-enable, reset links, 2FA reset, reset log, backup status.

Not the Django admin. Every page needs a superuser whose session passed 2FA;
anyone else gets 404, so the pages don't advertise themselves.
"""

from functools import wraps

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Count, Exists, OuterRef
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST
from django_otp.plugins.otp_totp.models import TOTPDevice

from leagues import policy
from ops import backups

from . import tokens
from .models import AccountToken
from .views import show_link

User = get_user_model()


def site_admin_view(view):
    @login_required
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not policy.can_open_site_admin(request.user):
            raise Http404
        return view(request, *args, **kwargs)

    return wrapper


@site_admin_view
def users_view(request):
    users = User.objects.order_by("username").annotate(
        league_count=Count("memberships"),
        has_2fa=Exists(TOTPDevice.objects.filter(user=OuterRef("pk"), confirmed=True)),
    )
    reset_log = AccountToken.objects.filter(purpose=AccountToken.Purpose.RESET).select_related(
        "user", "created_by"
    )[:50]
    return render(
        request,
        "accounts/site_admin/users.html",
        {"users": users, "reset_log": reset_log, "backup": backups.status()},
    )


@site_admin_view
@require_POST
def toggle_active_view(request, user_id):
    """Disable or re-enable an account. A disabled user's sessions and unspent links stop working (R6)."""
    user = get_object_or_404(User, pk=user_id)
    with transaction.atomic():
        user.is_active = not user.is_active
        user.save(update_fields=["is_active"])
        if not user.is_active:
            # Links they made stop working, and stay void if they are re-enabled.
            tokens.void_created_by(user)
    state = "re-enabled" if user.is_active else "disabled"
    messages.success(request, f"{user.get_username()} is {state}.")
    return redirect("accounts:site_admin")


@site_admin_view
@never_cache
@require_POST
def reset_link_view(request, user_id):
    user = get_object_or_404(User, pk=user_id)
    raw = tokens.create_reset(user, created_by=request.user)
    return show_link(
        request,
        title="Password reset link",
        route="accounts:reset",
        raw=raw,
        lifetime=tokens.RESET_LIFETIME,
        description=(
            f"Send this to {user.get_username()} by text or Discord. "
            "Any earlier reset link for them no longer works."
        ),
        back_url=reverse("accounts:site_admin"),
    )


@site_admin_view
@require_POST
def reset_2fa_view(request, user_id):
    """Remove a user's 2FA devices, for someone who lost their phone (KTD10)."""
    user = get_object_or_404(User, pk=user_id)
    TOTPDevice.objects.filter(user=user).delete()
    messages.success(request, f"Two-factor authentication is off for {user.get_username()}.")
    return redirect("accounts:site_admin")
