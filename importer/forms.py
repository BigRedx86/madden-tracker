import json

from django import forms

from . import legacy, native

MAX_UPLOAD_BYTES = 2 * 1024 * 1024


def read_json_upload(upload, what):
    """Size-check and parse an uploaded JSON file, or raise ValidationError naming ``what``."""
    if upload.size > MAX_UPLOAD_BYTES:
        raise forms.ValidationError(f"That file is over 2 MB, which is too big for {what}.")
    try:
        return json.loads(upload.read().decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError):
        raise forms.ValidationError(f"That file isn't valid JSON, so it can't be {what}.")


class LegacyUploadForm(forms.Form):
    backup = forms.FileField(label="Old Madden Tracker backup (.json)")

    def clean_backup(self):
        upload = self.cleaned_data["backup"]
        data = read_json_upload(upload, "an old tracker backup")
        if native.is_native(data):
            # A league file makes a new league; it isn't merged into this one (U9).
            raise forms.ValidationError(
                "That's a league file exported from this site, not an old tracker backup. "
                "Use the Import a league file page, which creates a new league from it."
            )
        try:
            self.parsed = legacy.parse(data)
        except legacy.LegacyFormatError as error:
            raise forms.ValidationError(str(error))
        return upload


class NativeUploadForm(forms.Form):
    league_file = forms.FileField(label="League file (.json)")

    def clean_league_file(self):
        upload = self.cleaned_data["league_file"]
        data = read_json_upload(upload, "a league file")
        if isinstance(data, dict) and isinstance(data.get("players"), list) and "format" not in data:
            raise forms.ValidationError(
                "That looks like an old Madden Tracker backup. Create a league, then use its "
                "Import an old backup page."
            )
        try:
            native.check_header(data)
        except native.NativeFormatError as error:
            raise forms.ValidationError(str(error))
        self.document = data
        return upload
