"""Sign-in (with optional TOTP step), account page, 2FA setup, invites, and reset links.

Public pages: sign-in (and its 2FA step), invite acceptance, and password reset (R1).
Every other view requires a signed-in user.
"""

import time
from base64 import b32encode

import qrcode
import qrcode.image.svg
from django.contrib import messages
from django.contrib.auth import get_user_model, update_session_auth_hash
from django.contrib.auth import login as auth_login
from django.contrib.auth import logout as auth_logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import AuthenticationForm, PasswordChangeForm, SetPasswordForm
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.safestring import mark_safe
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST
from django_otp import login as otp_login
from django_otp import user_has_device
from django_otp.plugins.otp_totp.models import TOTPDevice

from leagues import policy
from leagues.access import league_view, require
from leagues.models import Membership

from . import tokens
from .client_ip import client_ip
from .forms import CodeForm, InviteForm, InviteSignupForm
from .models import AccountToken

User = get_user_model()

PENDING_2FA = "pending_2fa"
# How long the 2FA step stays open after a correct password.
PENDING_2FA_SECONDS = 10 * 60
MODEL_BACKEND = "django.contrib.auth.backends.ModelBackend"


def _safe_next(request, candidate):
    if candidate and url_has_allowed_host_and_scheme(
        candidate, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return candidate
    return reverse("leagues:home")


def _finish_sign_in(request, user, backend, device=None):
    auth_login(request, user, backend=backend)
    if device is not None:
        otp_login(request, device)
    _show_reset_notices(request, user)


def _show_reset_notices(request, user):
    """Tell the user, once, that their password was reset through a link (KTD11)."""
    unseen = AccountToken.objects.filter(
        purpose=AccountToken.Purpose.RESET, user=user, used_at__isnull=False, notice_shown_at__isnull=True
    ).select_related("created_by")
    for reset in unseen:
        creator = reset.created_by.get_username() if reset.created_by else "a removed user"
        when = date_format(timezone.localtime(reset.used_at), "DATETIME_FORMAT")
        messages.warning(
            request,
            f"Your password was reset by {creator} on {when}. If you didn't ask for that, "
            "tell your commissioner or the site admin.",
        )
    unseen.update(notice_shown_at=timezone.now())


@never_cache
def login_view(request):
    if request.user.is_authenticated:
        return redirect("leagues:home")
    form = AuthenticationForm(request, data=request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.get_user()
        next_url = _safe_next(request, request.POST.get("next"))
        if user_has_device(user, confirmed=True):
            request.session[PENDING_2FA] = {
                "user_id": user.pk,
                "backend": user.backend,
                "next": next_url,
                "at": time.time(),
            }
            return redirect("accounts:login_verify")
        _finish_sign_in(request, user, user.backend)
        return redirect(next_url)
    return render(request, "accounts/login.html", {"form": form, "next": request.GET.get("next", "")})


@never_cache
def login_verify_view(request):
    """Second sign-in step for users with 2FA: the TOTP code (R3)."""
    pending = request.session.get(PENDING_2FA)
    if not pending or time.time() - pending["at"] > PENDING_2FA_SECONDS:
        request.session.pop(PENDING_2FA, None)
        return redirect("accounts:login")
    user = User.objects.filter(pk=pending["user_id"], is_active=True).first()
    device = TOTPDevice.objects.filter(user=user, confirmed=True).first() if user else None
    if device is None:
        request.session.pop(PENDING_2FA, None)
        return redirect("accounts:login")

    form = CodeForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        allowed, _ = device.verify_is_allowed()
        if not allowed:
            form.add_error("code", "Too many wrong codes. Wait a little and try again.")
        elif device.verify_token(form.cleaned_data["code"]):
            del request.session[PENDING_2FA]
            _finish_sign_in(request, user, pending["backend"], device)
            return redirect(pending["next"])
        else:
            form.add_error("code", "That code didn't match. Try the current one.")
    return render(request, "accounts/login_verify.html", {"form": form})


@login_required
@require_POST
def logout_view(request):
    auth_logout(request)
    return redirect("accounts:login")


@login_required
def account_view(request):
    recent_resets = (
        AccountToken.objects.filter(purpose=AccountToken.Purpose.RESET, user=request.user, used_at__isnull=False)
        .select_related("created_by")
        .order_by("-used_at")[:10]
    )
    return render(
        request,
        "accounts/account.html",
        {
            "has_2fa": user_has_device(request.user, confirmed=True),
            "recent_resets": recent_resets,
            "is_site_admin": policy.is_site_admin(request.user),
            "can_open_site_admin": policy.can_open_site_admin(request.user),
        },
    )


@login_required
def password_change_view(request):
    form = PasswordChangeForm(request.user, request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        update_session_auth_hash(request, user)
        messages.success(request, "Password changed.")
        return redirect("accounts:account")
    return render(request, "accounts/password_change.html", {"form": form})


def _qr_svg(data):
    """The QR code as inline SVG markup, so the CSP needs no data: images."""
    image = qrcode.make(data, image_factory=qrcode.image.svg.SvgPathImage, box_size=10)
    return mark_safe(image.to_string(encoding="unicode"))


@never_cache
@login_required
def totp_setup_view(request):
    """Turn on TOTP 2FA: scan the QR or type the secret, then prove it with a code (R3)."""
    if user_has_device(request.user, confirmed=True):
        return render(request, "accounts/totp_setup.html", {"enabled": True})
    device = TOTPDevice.objects.filter(user=request.user, confirmed=False).first()
    if device is None:
        device = TOTPDevice.objects.create(user=request.user, name="default", confirmed=False)

    form = CodeForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        allowed, _ = device.verify_is_allowed()
        if not allowed:
            form.add_error("code", "Too many wrong codes. Wait a little and try again.")
        elif device.verify_token(form.cleaned_data["code"]):
            device.confirmed = True
            device.save(update_fields=["confirmed"])
            otp_login(request, device)
            messages.success(request, "Two-factor authentication is on.")
            return redirect("accounts:account")
        else:
            form.add_error("code", "That code didn't match. Check the time on your phone and try again.")
    return render(
        request,
        "accounts/totp_setup.html",
        {"form": form, "qr_svg": _qr_svg(device.config_url), "secret": b32encode(device.bin_key).decode()},
    )


def _token_problem(request, error):
    return render(request, "accounts/link_problem.html", {"message": error.message})


@never_cache
def invite_accept_view(request, token):
    """Join a league from an invite: a new account, or the signed-in user's account (R2)."""
    try:
        invite = tokens.lookup(token, AccountToken.Purpose.INVITE)
    except tokens.TokenError as error:
        return _token_problem(request, error)
    league = invite.league

    if request.user.is_authenticated:
        already_member = Membership.objects.filter(league=league, user=request.user).exists()
        if request.method == "POST" and not already_member:
            try:
                with transaction.atomic():
                    tokens.consume(invite, client_ip(request))
                    Membership.objects.create(league=league, user=request.user)
            except tokens.TokenError as error:
                return _token_problem(request, error)
            messages.success(request, f"You joined {league.name}.")
            return redirect("leagues:home")
        return render(
            request, "accounts/invite_join.html", {"invite_league": league, "already_member": already_member}
        )

    form = InviteSignupForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            with transaction.atomic():
                tokens.consume(invite, client_ip(request))
                user = form.save()
                Membership.objects.create(league=league, user=user)
        except tokens.TokenError as error:
            return _token_problem(request, error)
        auth_login(request, user, backend=MODEL_BACKEND)
        messages.success(request, f"Welcome! You joined {league.name}.")
        return redirect("leagues:home")
    return render(request, "accounts/invite_signup.html", {"form": form, "invite_league": league})


@never_cache
def reset_view(request, token):
    """Set a new password from a reset link. Opening the link does not use it; submitting does (R4).

    The link does not sign anyone in, and 2FA stays on (KTD11). It is void once its
    creator could no longer make it (disabled, or no longer runs every league the
    target is in).
    """
    try:
        reset = tokens.lookup(token, AccountToken.Purpose.RESET)
        tokens.check_reset_authority(reset)
    except tokens.TokenError as error:
        return _token_problem(request, error)
    form = SetPasswordForm(reset.user, request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            with transaction.atomic():
                # Again inside the transaction, so authority lost since the lookup counts.
                tokens.check_reset_authority(reset)
                tokens.consume(reset, client_ip(request))
                form.save()
        except tokens.TokenError as error:
            return _token_problem(request, error)
        messages.success(request, "Password changed. Sign in with your new password.")
        return redirect("accounts:login")
    return render(request, "accounts/reset.html", {"form": form, "target": reset.user})


def show_link(request, *, title, route, raw, lifetime, description, back_url):
    """The generated link, shown this one time only (tokens are stored hashed).

    ``route`` is the URL name the raw token goes into; the link expires ``lifetime`` from now.
    """
    link = request.build_absolute_uri(reverse(route, args=[raw]))
    expires_at = timezone.now() + lifetime
    return render(
        request,
        "accounts/link_created.html",
        {"title": title, "link": link, "expires_at": expires_at, "description": description, "back_url": back_url},
    )


@never_cache
@league_view
def invite_create_view(request, league_id):
    """Commissioner creates an invite link for this league (R2). The members page links here."""
    require(policy.can_invite(request.user, request.league))
    form = InviteForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        name = form.cleaned_data["invitee_name"]
        raw = tokens.create_invite(request.league, created_by=request.user, invitee_name=name)
        return show_link(
            request,
            title="Invite link",
            route="accounts:invite",
            raw=raw,
            lifetime=tokens.INVITE_LIFETIME,
            description=f"Send this to {name or 'the person you are inviting'} to join {request.league.name}.",
            back_url=reverse("leagues:members", args=[request.league.pk]),
        )
    return render(
        request,
        "accounts/invite_create.html",
        {"form": form, "league": request.league, "membership": request.membership},
    )


@never_cache
@league_view
def member_reset_link_view(request, league_id, user_id):
    """Commissioner creates a reset link for a member whose every league they run (R4, KTD11)."""
    target = get_object_or_404(User, pk=user_id, memberships__league=request.league)
    require(policy.can_generate_reset_link(request.user, target))
    if request.method == "POST":
        raw = tokens.create_reset(target, created_by=request.user)
        return show_link(
            request,
            title="Password reset link",
            route="accounts:reset",
            raw=raw,
            lifetime=tokens.RESET_LIFETIME,
            description=(
                f"Send this to {target.get_username()} by text or Discord. "
                "Any earlier reset link for them no longer works."
            ),
            back_url=reverse("leagues:members", args=[request.league.pk]),
        )
    return render(
        request,
        "accounts/reset_link_confirm.html",
        {"target": target, "league": request.league, "membership": request.membership},
    )
