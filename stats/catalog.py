"""The stat catalog: one code-defined list that drives storage, forms, and totals (KTD6).

Value types:
- YARDAGE: integers that may be negative (passing, rushing, receiving, and
  return yards, and their "longest" stats). A -3 yard run is normal.
- HALF: non-negative decimals in 0.5 steps (sacks). Stored in JSON as a float;
  every 0.5 step is exact in binary floating point.
- COUNT: every other stat, a non-negative integer.

Stat line values are a dict of catalog key -> value. A blank input is an
absent key, never 0. When aggregating, an absent key counts as 0 for SUM stats
and is skipped for MAX ("longest") stats.
"""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError

YARDAGE = "yardage"
HALF = "half"
COUNT = "count"

SUM = "sum"
MAX = "max"

CATEGORIES = [
    ("passing", "Passing"),
    ("rushing", "Rushing"),
    ("receiving", "Receiving"),
    ("returns", "Returns"),
    ("fumbles", "Fumbles"),
    ("defense", "Defense"),
    ("kicking", "Kicking"),
    ("punting", "Punting"),
]
CATEGORY_KEYS = [key for key, _label in CATEGORIES]
CATEGORY_LABELS = dict(CATEGORIES)


@dataclass(frozen=True)
class Stat:
    key: str
    label: str
    category: str
    value_type: str = COUNT
    combine: str = SUM

    @property
    def minimum(self):
        return None if self.value_type == YARDAGE else 0


def _stats(category, *rows):
    return [Stat(key, label, category, *rest) for key, label, *rest in rows]


_ALL_STATS = [
    *_stats(
        "passing",
        ("passing_completions", "Completions"),
        ("passing_attempts", "Attempts"),
        ("passing_yards", "Passing yards", YARDAGE),
        ("passing_tds", "Passing TDs"),
        ("passing_interceptions", "Interceptions thrown"),
        ("passing_longest", "Longest pass", YARDAGE, MAX),
        ("times_sacked", "Times sacked"),
    ),
    *_stats(
        "rushing",
        ("rushing_attempts", "Rushing attempts"),
        ("rushing_yards", "Rushing yards", YARDAGE),
        ("rushing_tds", "Rushing TDs"),
        ("rushing_longest", "Longest rush", YARDAGE, MAX),
        ("broken_tackles", "Broken tackles"),
    ),
    *_stats(
        "receiving",
        ("receptions", "Receptions"),
        ("receiving_yards", "Receiving yards", YARDAGE),
        ("receiving_tds", "Receiving TDs"),
        ("receiving_longest", "Longest reception", YARDAGE, MAX),
        ("drops", "Drops"),
    ),
    *_stats(
        "returns",
        ("kick_returns", "Kick returns"),
        ("kick_return_yards", "Kick return yards", YARDAGE),
        ("kick_return_tds", "Kick return TDs"),
        ("kick_return_longest", "Longest kick return", YARDAGE, MAX),
        ("punt_returns", "Punt returns"),
        ("punt_return_yards", "Punt return yards", YARDAGE),
        ("punt_return_tds", "Punt return TDs"),
        ("punt_return_longest", "Longest punt return", YARDAGE, MAX),
    ),
    *_stats(
        "fumbles",
        ("fumbles", "Fumbles"),
        ("fumbles_lost", "Fumbles lost"),
    ),
    *_stats(
        "defense",
        ("solo_tackles", "Solo tackles"),
        ("assist_tackles", "Assisted tackles"),
        ("tackles_for_loss", "Tackles for loss"),
        ("sacks", "Sacks", HALF),
        ("interceptions", "Interceptions"),
        ("interception_return_yards", "Interception return yards", YARDAGE),
        ("passes_defended", "Passes defended"),
        ("forced_fumbles", "Forced fumbles"),
        ("fumble_recoveries", "Fumble recoveries"),
        ("defensive_tds", "Defensive TDs"),
        ("safeties", "Safeties"),
    ),
    *_stats(
        "kicking",
        ("field_goals_made", "Field goals made"),
        ("field_goal_attempts", "Field goal attempts"),
        ("field_goal_longest", "Longest field goal", COUNT, MAX),
        ("extra_points_made", "Extra points made"),
        ("extra_point_attempts", "Extra point attempts"),
    ),
    *_stats(
        "punting",
        ("punts", "Punts"),
        ("punt_yards", "Punt yards"),
        ("punt_longest", "Longest punt", COUNT, MAX),
        ("punts_inside_20", "Punts inside the 20"),
    ),
]
STATS = {stat.key: stat for stat in _ALL_STATS}

# Which categories each position shows on the entry form. The form shows the
# union for the primary and secondary positions.
POSITION_CATEGORIES = {
    "QB": {"passing", "rushing", "fumbles"},
    "HB": {"rushing", "receiving", "returns", "fumbles"},
    "FB": {"rushing", "receiving", "returns", "fumbles"},
    "WR": {"receiving", "rushing", "returns", "fumbles"},
    "TE": {"receiving", "rushing", "fumbles"},
    **{pos: {"fumbles"} for pos in ("LT", "LG", "C", "RG", "RT")},
    **{pos: {"defense"} for pos in ("LE", "RE", "DT", "LOLB", "MLB", "ROLB", "LB")},
    **{pos: {"defense", "returns"} for pos in ("CB", "FS", "SS")},
    "K": {"kicking"},
    "P": {"punting"},
}
POSITIONS = list(POSITION_CATEGORIES)

# Old-app position names that are not Madden positions.
LEGACY_POSITIONS = {"RB": "HB"}

# Old-app stat keys -> catalog keys (or a derived stat, which is never stored).
LEGACY_KEYS = {
    "receptions": "receptions",
    "receivingYards": "receiving_yards",
    "receivingTDs": "receiving_tds",
    "rushingAttempts": "rushing_attempts",
    "rushingYards": "rushing_yards",
    "rushingTDs": "rushing_tds",
    "fumbles": "fumbles",
    "soloTackles": "solo_tackles",
    "assistTackles": "assist_tackles",
    "totalTackles": "total_tackles",
    "tacklesForLoss": "tackles_for_loss",
    "sacks": "sacks",
    "interceptions": "interceptions",
    "passDeflections": "passes_defended",
    "forceFumbles": "forced_fumbles",
    "defensiveTDs": "defensive_tds",
}


def categories_for_positions(primary, secondary="", show_all=False):
    """Category keys to show for a player, in catalog order."""
    if show_all:
        return list(CATEGORY_KEYS)
    wanted = POSITION_CATEGORIES.get(primary, set()) | POSITION_CATEGORIES.get(secondary or "", set())
    return [key for key in CATEGORY_KEYS if key in wanted]


def stats_for_categories(categories):
    wanted = set(categories)
    return [stat for stat in _ALL_STATS if stat.category in wanted]


def _to_decimal(raw):
    if isinstance(raw, bool):
        raise ValueError
    if isinstance(raw, float):
        raw = repr(raw)
    value = Decimal(str(raw).strip()) if not isinstance(raw, Decimal) else raw
    if not value.is_finite():
        raise ValueError
    return value


# No single-game stat comes near this. Checked before any arithmetic or int():
# int(Decimal("1e1000000")) expands a million digits, and values past ~1e4300
# can't be serialized to JSON at all.
MAX_MAGNITUDE = 99_999


def clean_value(stat, raw):
    """Return the stored form of one value, or raise ValidationError."""
    try:
        value = _to_decimal(raw)
    except (ValueError, InvalidOperation):
        raise ValidationError(f"{stat.label} must be a number.")
    # adjusted() reads the exponent without expanding the number.
    if value and (value.adjusted() > 4 or abs(value) > MAX_MAGNITUDE):
        raise ValidationError(f"{stat.label} is too large.")
    if stat.minimum is not None and value < stat.minimum:
        raise ValidationError(f"{stat.label} can't be negative.")
    if stat.value_type == HALF:
        if (value * 2) != (value * 2).to_integral_value():
            raise ValidationError(f"{stat.label} must be in steps of 0.5.")
        return float(value)
    if value != value.to_integral_value():
        raise ValidationError(f"{stat.label} must be a whole number.")
    return int(value)


def _is_blank(raw):
    return raw is None or (isinstance(raw, str) and raw.strip() == "")


def normalize_values(raw_values):
    """Validate a stat-line values dict against the catalog.

    Blank inputs are dropped (absent key, not 0). Unknown keys and values
    outside a stat's type or minimum raise ValidationError keyed by stat.
    """
    if not isinstance(raw_values, dict):
        raise ValidationError("Stat values must be a mapping of stat to value.")
    cleaned, errors = {}, {}
    for key, raw in raw_values.items():
        stat = STATS.get(key)
        if stat is None:
            errors[key] = [f"Unknown stat: {key}."]
            continue
        if _is_blank(raw):
            continue
        try:
            cleaned[key] = clean_value(stat, raw)
        except ValidationError as error:
            errors[key] = error.messages
    if errors:
        raise ValidationError(errors)
    return cleaned


# --- Derived stats: computed for display from totals, never stored ---------------


def _get(totals, key):
    return totals.get(key, 0)


def _ratio(numerator, denominator, scale=1):
    return None if not denominator else numerator / denominator * scale


def _passer_rating(totals):
    attempts = _get(totals, "passing_attempts")
    if not attempts:
        return None

    def clamp(value):
        return max(0.0, min(value, 2.375))

    a = clamp((_get(totals, "passing_completions") / attempts - 0.3) * 5)
    b = clamp((_get(totals, "passing_yards") / attempts - 3) * 0.25)
    c = clamp(_get(totals, "passing_tds") / attempts * 20)
    d = clamp(2.375 - _get(totals, "passing_interceptions") / attempts * 25)
    return (a + b + c + d) / 6 * 100


DERIVED_STATS = {
    "total_tackles": (
        "Total tackles",
        "defense",
        lambda t: _get(t, "solo_tackles") + _get(t, "assist_tackles"),
    ),
    "completion_pct": (
        "Completion %",
        "passing",
        lambda t: _ratio(_get(t, "passing_completions"), _get(t, "passing_attempts"), 100),
    ),
    "passer_rating": ("Passer rating", "passing", _passer_rating),
    "yards_per_carry": (
        "Yards per carry",
        "rushing",
        lambda t: _ratio(_get(t, "rushing_yards"), _get(t, "rushing_attempts")),
    ),
    "yards_per_catch": (
        "Yards per catch",
        "receiving",
        lambda t: _ratio(_get(t, "receiving_yards"), _get(t, "receptions")),
    ),
    "yards_per_return": (
        "Yards per return",
        "returns",
        lambda t: _ratio(
            _get(t, "kick_return_yards") + _get(t, "punt_return_yards"),
            _get(t, "kick_returns") + _get(t, "punt_returns"),
        ),
    ),
    "field_goal_pct": (
        "Field goal %",
        "kicking",
        lambda t: _ratio(_get(t, "field_goals_made"), _get(t, "field_goal_attempts"), 100),
    ),
    "punt_average": (
        "Punt average",
        "punting",
        lambda t: _ratio(_get(t, "punt_yards"), _get(t, "punts")),
    ),
}


def derived_values(totals):
    """Derived stats for a totals dict. None where the denominator is zero."""
    return {key: compute(totals) for key, (_label, _category, compute) in DERIVED_STATS.items()}
