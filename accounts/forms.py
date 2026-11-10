from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import UserCreationForm


class CodeForm(forms.Form):
    code = forms.CharField(
        label="Authentication code",
        max_length=6,
        help_text="The 6-digit code from your authenticator app.",
        widget=forms.TextInput(attrs={"inputmode": "numeric", "autocomplete": "one-time-code"}),
    )


class InviteSignupForm(UserCreationForm):
    """New account from an invite: name, username, password (R2)."""

    class Meta(UserCreationForm.Meta):
        model = get_user_model()
        fields = ("first_name", "username")
        labels = {"first_name": "Name"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["first_name"].required = True


class InviteForm(forms.Form):
    invitee_name = forms.CharField(
        label="Who is this invite for?",
        max_length=150,
        required=False,
        help_text="Optional. Helps you tell pending invites apart.",
    )
