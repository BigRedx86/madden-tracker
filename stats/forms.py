"""The stat entry form, built from the catalog (KTD6, R16, R17)."""

from django import forms
from django.core.exceptions import ValidationError

from . import catalog


def _widget(stat):
    attrs = {"autocomplete": "off"}
    if stat.value_type == catalog.HALF:
        attrs.update(min="0", step="0.5", inputmode="decimal")
    elif stat.value_type == catalog.YARDAGE:
        # No min, and no inputmode: iOS's numeric and decimal keypads have no
        # minus key, and a -3 yard run is normal. type=number keeps the minus.
        attrs.update(step="1")
    else:
        attrs.update(min="0", step="1", inputmode="numeric")
    return forms.NumberInput(attrs=attrs)


def _display(value):
    """1.0 sacks shows as 1; 0.5 stays 0.5."""
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


class StatLineForm(forms.Form):
    """A DNP checkbox plus one optional input per catalog stat. Blank inputs are left out."""

    did_not_play = forms.BooleanField(label="Did not play", required=False)

    def __init__(self, *args, line=None, **kwargs):
        initial = {}
        if line is not None:
            initial = {key: _display(value) for key, value in line.values.items()}
            initial["did_not_play"] = line.did_not_play
        super().__init__(*args, initial=initial, **kwargs)
        for stat in catalog.STATS.values():
            # CharField, so the catalog (not the form) decides what a valid value is.
            self.fields[stat.key] = forms.CharField(
                label=stat.label, required=False, max_length=12, widget=_widget(stat)
            )

    def clean(self):
        cleaned = super().clean()
        values = {}
        if cleaned.get("did_not_play"):
            # A "did not play" line records the game with no stats (R16).
            cleaned["values"] = {}
            return cleaned
        for key, stat in catalog.STATS.items():
            raw = (cleaned.get(key) or "").strip()
            if not raw:
                continue
            try:
                values[key] = catalog.clean_value(stat, raw)
            except ValidationError as error:
                self.add_error(key, error)
        cleaned["values"] = values
        return cleaned

    def groups(self, categories):
        """[(category label, [bound fields])] for the given category keys, in catalog order."""
        return [
            (catalog.CATEGORY_LABELS[category], [self[stat.key] for stat in catalog.stats_for_categories([category])])
            for category in categories
        ]

    def has_values_in(self, categories):
        keys = {stat.key for stat in catalog.stats_for_categories(categories)}
        if self.is_bound:
            return any((self.data.get(key) or "").strip() for key in keys)
        return any(key in self.initial for key in keys)
