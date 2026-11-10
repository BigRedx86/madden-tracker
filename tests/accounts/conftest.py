"""Account test helpers: TOTP devices, codes, and 2FA-verified sessions."""

import pytest
from django_otp import DEVICE_ID_SESSION_KEY
from django_otp.oath import totp
from django_otp.plugins.otp_totp.models import TOTPDevice

PASSWORD = "pw-for-tests-only"


def current_code(device):
    """The code an authenticator app would show right now for ``device``."""
    return f"{totp(device.bin_key, device.step, device.t0, device.digits, device.drift):06d}"


@pytest.fixture
def make_device(db):
    def make(user):
        return TOTPDevice.objects.create(user=user, name="default", confirmed=True)

    return make


@pytest.fixture
def login_verified(make_device):
    """Sign ``user`` in with a confirmed 2FA device, as if they passed the TOTP step."""

    def login(client, user):
        device = make_device(user)
        client.force_login(user)
        session = client.session
        session[DEVICE_ID_SESSION_KEY] = device.persistent_id
        session.save()
        return device

    return login


def sign_in(client, username, password=PASSWORD, **extra):
    return client.post("/accounts/login/", {"username": username, "password": password}, **extra)
