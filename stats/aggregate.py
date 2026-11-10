"""Totals, averages, derived stats, leaders, and records (U7: R18, R19, R22, KTD6).

Pure functions over stat lines (anything with ``did_not_play`` and ``values``)
and games. Nothing here touches the database or stores a result: totals are
computed on every request, so a corrected stat line shows up at once (R18).

Rules from the catalog: an absent key counts as 0 for SUM stats and is skipped
for MAX ("longest") stats. Per-game averages divide by games played; "did not
play" lines are not counted (R19, AE3). A category the lines have no data in
has no totals at all, which the pages show as a dash.
"""

from dataclasses import dataclass, field
from functools import cached_property

from . import catalog

DASH = "—"

# Leaders rank by "most", so stats where more is worse have no leader.
NOT_LEADER_STATS = {"passing_interceptions", "times_sacked", "drops", "fumbles", "fumbles_lost"}
# Derived ratios aren't ranked; total tackles is a count, so it is.
LEADER_DERIVED_STATS = {"total_tackles"}


@dataclass(frozen=True)
class Column:
    """One stat column for display: a catalog stat or a derived stat."""

    key: str
    label: str
    category: str
    derived: bool = False
    combine: str = catalog.SUM

    @property
    def averaged(self):
        """Only summed catalog stats get a per-game average."""
        return not self.derived and self.combine == catalog.SUM


def category_columns(categories, derived=True):
    """[(category key, label, [Column, ...]), ...] in catalog order."""
    wanted = set(categories)
    groups = []
    for category in catalog.CATEGORY_KEYS:
        if category not in wanted:
            continue
        columns = [
            Column(stat.key, stat.label, stat.category, combine=stat.combine)
            for stat in catalog.stats_for_categories([category])
        ]
        if derived:
            columns += [
                Column(key, label, category, derived=True)
                for key, (label, stat_category, _compute) in catalog.DERIVED_STATS.items()
                if stat_category == category
            ]
        groups.append((category, catalog.CATEGORY_LABELS[category], columns))
    return groups


@dataclass
class Summary:
    games_played: int
    did_not_play: int
    totals: dict
    derived: dict = field(init=False)

    def __post_init__(self):
        self.derived = catalog.derived_values(self.totals)

    @cached_property
    def categories(self):
        """Categories with at least one recorded value, in catalog order."""
        present = {catalog.STATS[key].category for key in self.totals}
        return [category for category in catalog.CATEGORY_KEYS if category in present]

    def _category_of(self, key):
        if key in catalog.DERIVED_STATS:
            return catalog.DERIVED_STATS[key][1]
        return catalog.STATS[key].category

    def total(self, key):
        """A stat's total (or a derived value), or None when there's nothing to show."""
        if self._category_of(key) not in self.categories:
            return None
        if key in catalog.DERIVED_STATS:
            return self.derived[key]
        if catalog.STATS[key].combine == catalog.MAX:
            return self.totals.get(key)
        return self.totals.get(key, 0)

    def average(self, key):
        """Per-game average over games played, or None (zero games, a max stat, or no data)."""
        if key in catalog.DERIVED_STATS or catalog.STATS[key].combine != catalog.SUM:
            return None
        total = self.total(key)
        if total is None or not self.games_played:
            return None
        return total / self.games_played


def summarize(lines):
    lines = list(lines)
    played = [line for line in lines if not line.did_not_play]
    totals = {}
    for line in played:
        for key, value in line.values.items():
            if catalog.STATS[key].combine == catalog.MAX:
                totals[key] = max(totals[key], value) if key in totals else value
            else:
                totals[key] = totals.get(key, 0) + value
    return Summary(games_played=len(played), did_not_play=len(lines) - len(played), totals=totals)


# --- Formatting ------------------------------------------------------------------------


def format_total(value):
    """Catalog totals: integers, and sacks in halves (2.5, or 2 for 2.0)."""
    if value is None:
        return DASH
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def format_average(value):
    return DASH if value is None else f"{value:.1f}"


def format_derived(value):
    if isinstance(value, int):
        return str(value)
    return format_average(value)


def total_text(summary, column):
    value = summary.total(column.key)
    return format_derived(value) if column.derived else format_total(value)


def average_text(summary, column):
    """Blank for columns that are never averaged or have no data (the total already shows a dash)."""
    if not column.averaged or summary.total(column.key) is None:
        return ""
    return format_average(summary.average(column.key))


# --- Leaders and records (R22) ---------------------------------------------------------


def leaders(summaries):
    """Top player per stat, grouped by category; ties list every tied player.

    ``summaries`` is [(player, Summary), ...]. A stat leads only with a value
    above 0, so stats nobody recorded have no leader.
    """
    categories = {category for _player, summary in summaries for category in summary.categories}
    groups = []
    for _category, label, columns in category_columns(categories):
        rows = []
        for column in columns:
            if column.key in NOT_LEADER_STATS or (column.derived and column.key not in LEADER_DERIVED_STATS):
                continue
            values = [(player, summary.total(column.key)) for player, summary in summaries]
            values = [(player, value) for player, value in values if value is not None]
            best = max((value for _player, value in values), default=None)
            if best is None or best <= 0:
                continue
            rows.append(
                {
                    "label": column.label,
                    "players": [player for player, value in values if value == best],
                    "value": format_total(best),
                }
            )
        if rows:
            groups.append({"label": label, "rows": rows})
    return groups


def _record_text(counts):
    text = f"{counts['W']}-{counts['L']}"
    return f"{text}-{counts['T']}" if counts["T"] else text


def record_counts(games):
    """{"regular": {"W": 10, "L": 7, "T": 0}, "playoff": {...}}, from games with a result."""
    counts = {"regular": {"W": 0, "L": 0, "T": 0}, "playoff": {"W": 0, "L": 0, "T": 0}}
    for game in games:
        if game.result:
            counts["playoff" if game.is_playoff else "regular"][game.result] += 1
    return counts


def season_record(games):
    """{"regular": "10-7", "playoff": "1-0" or None}, from games with a result."""
    counts = record_counts(games)
    playoff = counts["playoff"]
    return {
        "regular": _record_text(counts["regular"]),
        "playoff": _record_text(playoff) if any(playoff.values()) else None,
    }
