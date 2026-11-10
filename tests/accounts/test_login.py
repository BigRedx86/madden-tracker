"""Sign-in, lockout, 2FA, and the "every page needs sign-in" rule (R1, R3, R5, KTD5)."""

import re
from datetime import timedelta

import pytest
from axes.models import AccessAttempt
from django.urls import URLPattern, URLResolver, get_resolver
from django.utils import timezone
from django_otp.plugins.otp_totp.models import TOTPDevice

from .conftest import PASSWORD, current_code, sign_in

# --- R1: every page except these sends anonymous users to sign-in ---------------------

PUBLIC_URL_NAMES = {"accounts:login", "accounts:invite", "accounts:reset"}
DUMMY_ARGS = {"int": "1", "str": "x", "slug": "x", "path": "x", "uuid": "00000000-0000-0000-0000-000000000000"}


def all_routes():
    """(name, concrete path) for every route in the project, with dummy arguments filled in."""
    routes = []

    def walk(patterns, prefix, namespace):
        for pattern in patterns:
            route = prefix + str(pattern.pattern)
            if isinstance(pattern, URLResolver):
                ns = f"{namespace}{pattern.namespace}:" if pattern.namespace else namespace
                walk(pattern.url_patterns, route, ns)
            elif isinstance(pattern, URLPattern):
                assert not route.startswith("^"), f"regex routes need a dummy-arg rule: {route}"
                concrete = re.sub(r"<(?:(\w+):)?\w+>", lambda m: DUMMY_ARGS[m.group(1) or "str"], route)
                routes.append((f"{namespace}{pattern.name}", "/" + concrete))

    walk(get_resolver().url_patterns, "", "")
    return routes


def test_public_urls_exist():
    names = {name for name, _ in all_routes()}
    assert PUBLIC_URL_NAMES <= names


@pytest.mark.parametrize(
    "name, url", [route for route in all_routes() if route[0] not in PUBLIC_URL_NAMES]
)
def test_anonymous_user_is_sent_to_sign_in(client, db, name, url):
    response = client.get(url)
    assert response.status_code == 302, name
    assert response["Location"].startswith("/accounts/login/"), name


def test_health_needs_no_sign_in(client):
    assert client.get("/health").status_code == 200


# --- Password sign-in --------------------------------------------------------------------


def test_correct_password_signs_in(client, make_user):
    user = make_user("jordan")
    response = sign_in(client, "jordan")
    assert response.status_code == 302
    assert client.session["_auth_user_id"] == str(user.pk)


def test_wrong_password_does_not_sign_in(client, make_user):
    make_user("jordan")
    response = sign_in(client, "jordan", "wrong")
    assert response.status_code == 200
    assert "_auth_user_id" not in client.session


# --- R5: lockout by username + client IP ---------------------------------------------


def test_sixth_attempt_is_locked_out_even_with_the_right_password_until_cool_off(client, make_user):
    make_user("jordan")
    for _ in range(5):
        sign_in(client, "jordan", "wrong")
    response = sign_in(client, "jordan", PASSWORD)
    assert response.status_code == 429
    assert "_auth_user_id" not in client.session
    assert b"locked" in response.content.lower()

    # 15 minutes later the cool-off is over.
    AccessAttempt.objects.update(attempt_time=timezone.now() - timedelta(minutes=16))
    response = sign_in(client, "jordan", PASSWORD)
    assert response.status_code == 302
    assert "_auth_user_id" in client.session


def test_lockout_is_per_username(client, make_user):
    make_user("jordan")
    make_user("blake")
    for _ in range(5):
        sign_in(client, "jordan", "wrong")
    assert sign_in(client, "blake").status_code == 302


def test_lockout_keys_on_the_ip_swag_appends_and_ignores_spoofed_entries(client, make_user):
    make_user("jordan")
    # SWAG appends the real client IP as the rightmost X-Forwarded-For entry; anything
    # to its left came from the client and is ignored.
    for spoofed in ("1.1.1.1", "2.2.2.2", "3.3.3.3", "4.4.4.4", "5.5.5.5"):
        sign_in(client, "jordan", "wrong", HTTP_X_FORWARDED_FOR=f"{spoofed}, 198.51.100.7")
    locked = sign_in(client, "jordan", PASSWORD, HTTP_X_FORWARDED_FOR="9.9.9.9, 198.51.100.7")
    assert locked.status_code == 429
    assert set(AccessAttempt.objects.values_list("ip_address", flat=True)) == {"198.51.100.7"}

    # A different real client is not locked out.
    other = sign_in(client, "jordan", PASSWORD, HTTP_X_FORWARDED_FOR="198.51.100.8")
    assert other.status_code == 302


def test_without_forwarded_header_the_socket_address_is_used(client, make_user):
    make_user("jordan")
    sign_in(client, "jordan", "wrong", REMOTE_ADDR="192.0.2.44")
    assert AccessAttempt.objects.get().ip_address == "192.0.2.44"


# --- R3: two-step sign-in with TOTP --------------------------------------------------


def test_with_2fa_the_password_alone_does_not_sign_in(client, make_user, make_device):
    make_device(make_user("jordan"))
    response = sign_in(client, "jordan")
    assert response.status_code == 302
    assert response["Location"] == "/accounts/login/verify/"
    assert "_auth_user_id" not in client.session


def test_with_2fa_a_wrong_code_means_no_session(client, make_user, make_device):
    make_device(make_user("jordan"))
    sign_in(client, "jordan")
    response = client.post("/accounts/login/verify/", {"code": "000000"})
    assert response.status_code == 200
    assert "_auth_user_id" not in client.session


def test_with_2fa_the_right_code_signs_in_verified(client, make_user, make_device):
    user = make_user("jordan")
    device = make_device(user)
    sign_in(client, "jordan")
    response = client.post("/accounts/login/verify/", {"code": current_code(device)})
    assert response.status_code == 302
    assert client.session["_auth_user_id"] == str(user.pk)
    assert client.session["otp_device_id"] == device.persistent_id


def test_verify_step_without_a_password_step_goes_back_to_sign_in(client, make_user, make_device):
    device = make_device(make_user("jordan"))
    response = client.post("/accounts/login/verify/", {"code": current_code(device)})
    assert response.status_code == 302
    assert response["Location"].startswith("/accounts/login/")
    assert "_auth_user_id" not in client.session


def test_2fa_setup_shows_qr_and_secret_and_needs_a_valid_code(client, make_user):
    user = make_user("jordan")
    client.force_login(user)
    page = client.get("/accounts/2fa/")
    assert page.status_code == 200
    device = TOTPDevice.objects.get(user=user)
    assert device.confirmed is False
    html = page.content.decode()
    assert "<svg" in html
    assert "style=" not in html  # inline styles would break the CSP
    assert device.config_url.split("secret=")[1].split("&")[0] in html

    client.post("/accounts/2fa/", {"code": "000000"})
    device.refresh_from_db()
    assert device.confirmed is False

    device.throttle_reset()  # a wrong code makes django-otp wait before the next try
    client.post("/accounts/2fa/", {"code": current_code(device)})
    device.refresh_from_db()
    assert device.confirmed is True


def test_sign_out(client, make_user):
    client.force_login(make_user("jordan"))
    response = client.post("/accounts/logout/")
    assert response.status_code == 302
    assert "_auth_user_id" not in client.session


def test_password_change(client, make_user):
    user = make_user("jordan")
    client.force_login(user)
    response = client.post(
        "/accounts/password/",
        {"old_password": PASSWORD, "new_password1": "a-new-longer-pass-42", "new_password2": "a-new-longer-pass-42"},
    )
    assert response.status_code == 302
    user.refresh_from_db()
    assert user.check_password("a-new-longer-pass-42")
    # The session that changed the password stays signed in.
    assert client.get("/accounts/").status_code == 200


# --- 2FA step edge cases -----------------------------------------------------------------


def test_with_2fa_a_retry_inside_the_throttle_window_is_refused(client, make_user, make_device):
    device = make_device(make_user("jordan"))
    sign_in(client, "jordan")
    client.post("/accounts/login/verify/", {"code": "000000"})
    # No throttle_reset: django-otp makes the next try wait, even with the right code.
    response = client.post("/accounts/login/verify/", {"code": current_code(device)})
    assert response.status_code == 200
    assert "Too many wrong codes" in response.content.decode()
    assert "_auth_user_id" not in client.session


def test_expired_2fa_step_goes_back_to_sign_in(client, make_user, make_device):
    device = make_device(make_user("jordan"))
    sign_in(client, "jordan")
    session = client.session
    session["pending_2fa"]["at"] -= 601
    session.save()
    response = client.post("/accounts/login/verify/", {"code": current_code(device)})
    assert response.status_code == 302
    assert response["Location"].startswith("/accounts/login/")
    assert "_auth_user_id" not in client.session
    assert "pending_2fa" not in client.session


def test_user_disabled_between_steps_goes_back_to_sign_in(client, make_user, make_device):
    user = make_user("jordan")
    device = make_device(user)
    sign_in(client, "jordan")
    user.is_active = False
    user.save()
    response = client.post("/accounts/login/verify/", {"code": current_code(device)})
    assert response.status_code == 302
    assert response["Location"].startswith("/accounts/login/")
    assert "_auth_user_id" not in client.session


# --- Where sign-in goes next -------------------------------------------------------------


def test_sign_in_follows_an_in_site_next(client, make_user):
    make_user("jordan")
    response = sign_in_next(client, "/league/1/")
    assert response["Location"] == "/league/1/"


@pytest.mark.parametrize(
    "next_url",
    ["https://evil.example/", "//evil.example/", "/\\evil.example/", "http://testserver.evil.example/",
     "javascript:alert(1)"],
)
def test_sign_in_ignores_an_off_site_next(client, make_user, next_url):
    make_user("jordan")
    response = sign_in_next(client, next_url)
    assert response.status_code == 302
    assert response["Location"] == "/"
    assert "_auth_user_id" in client.session


def test_next_survives_the_2fa_step(client, make_user, make_device):
    device = make_device(make_user("jordan"))
    assert sign_in_next(client, "/league/1/")["Location"] == "/accounts/login/verify/"
    response = client.post("/accounts/login/verify/", {"code": current_code(device)})
    assert response["Location"] == "/league/1/"


def test_off_site_next_is_dropped_before_the_2fa_step(client, make_user, make_device):
    device = make_device(make_user("jordan"))
    sign_in_next(client, "//evil.example/")
    response = client.post("/accounts/login/verify/", {"code": current_code(device)})
    assert response["Location"] == "/"


def sign_in_next(client, next_url):
    return client.post("/accounts/login/", {"username": "jordan", "password": PASSWORD, "next": next_url})
