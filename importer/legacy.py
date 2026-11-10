"""Parse and normalize an old Madden Tracker backup (U8: R23, R26). Pure: no database access.

The old app stored every game separately under each player:
``{"players": [{name, team, positions, bio..., "seasons": [{name, "games": [...]}]}]}``.
parse() checks that shape and returns plain dicts using this app's names:

- heights get plain quotes (6’2” -> 6'2"), weights and overall become integers
  or None, old positions map to catalog positions ("RB" -> "HB");
- old stat keys map to catalog keys, derived stats (total tackles) are dropped,
  and values are validated against the catalog;
- a "did not play" game's all-zero stats are dropped, so it is a valid DNP line.

Scores stay as the raw text; parse_score() turns one into two numbers.
"""

import re

from django.core.exceptions import ValidationError

from leagues.models import Game, Player, normalize_height
from stats import catalog


class LegacyFormatError(Exception):
    """The file isn't an old Madden Tracker backup, or has a value we can't read."""


OUTCOMES = set(Game.Result.values)
_SCORE = re.compile(r"^\s*(\d{1,2})\s*[-–]\s*(\d{1,2})\s*$")


def parse_score(raw, outcome):
    """Return ("ok", team, opponent), ("missing", None, None), or ("malformed", None, None).

    The old score text doesn't say which number is ours, so the outcome decides:
    on a W the team has the higher number, on an L the lower one. A score that
    doesn't read as two numbers of at most two digits ("38=28", "338-7"), or one
    that contradicts or lacks the outcome, is malformed.
    """
    if not raw.strip():
        return ("missing", None, None)
    match = _SCORE.match(raw)
    if not match:
        return ("malformed", None, None)
    high, low = sorted((int(match.group(1)), int(match.group(2))), reverse=True)
    if high == low:
        return ("ok", high, low) if outcome == "T" else ("malformed", None, None)
    if outcome == "W":
        return ("ok", high, low)
    if outcome == "L":
        return ("ok", low, high)
    return ("malformed", None, None)


def _text(value, where, field, required=False):
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise LegacyFormatError(f"{where}: {field} should be text.")
    value = value.strip()
    if required and not value:
        raise LegacyFormatError(f"{where}: {field} is missing.")
    return value


def _whole_number(value, where, field):
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, bool):
        raise LegacyFormatError(f"{where}: {field} should be a number.")
    if isinstance(value, int) and value >= 0:
        return value
    if isinstance(value, str) and value.strip().isascii() and value.strip().isdigit():
        return int(value.strip())
    raise LegacyFormatError(f"{where}: {field} should be a whole number, not {value!r}.")


def _position(value, where, field, required):
    value = _text(value, where, field, required).upper()
    value = catalog.LEGACY_POSITIONS.get(value, value)
    if value and value not in catalog.POSITION_CATEGORIES:
        raise LegacyFormatError(f"{where}: unknown {field} {value!r}.")
    return value


def _list(value, where, field):
    if not isinstance(value, list):
        raise LegacyFormatError(f"{where}: {field} should be a list.")
    return value


def _dict(value, where, field):
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise LegacyFormatError(f"{where}: {field} should be an object.")
    return value


def _stat_values(game, where):
    raw = {}
    for section in ("offense", "defense"):
        for old_key, value in _dict(game.get(section), where, section).items():
            key = catalog.LEGACY_KEYS.get(old_key)
            if key is None:
                raise LegacyFormatError(f"{where}: unknown stat {old_key!r}.")
            if key in catalog.STATS:
                raw[key] = value
    try:
        return catalog.normalize_values(raw)
    except ValidationError as error:
        problems = "; ".join(message for messages in error.message_dict.values() for message in messages)
        raise LegacyFormatError(f"{where}: {problems}")


def _game(game, where):
    if not isinstance(game, dict):
        raise LegacyFormatError(f"{where} should be an object.")
    outcome = _text(game.get("outcome"), where, "outcome").upper()
    if outcome and outcome not in OUTCOMES:
        raise LegacyFormatError(f"{where}: outcome should be W, L, or T, not {outcome!r}.")
    values = _stat_values(game, where)
    did_not_play = bool(game.get("isDNP"))
    if did_not_play:
        if any(values.values()):
            raise LegacyFormatError(f"{where} is marked did-not-play but has stats.")
        values = {}
    return {
        "opponent": _text(game.get("opponent"), where, "opponent", required=True),
        "score": _text(game.get("score"), where, "score"),
        "outcome": outcome,
        "is_playoff": bool(game.get("isPlayoff")),
        "did_not_play": did_not_play,
        "values": values,
    }


def _season(season, where):
    if not isinstance(season, dict):
        raise LegacyFormatError(f"{where} should be an object.")
    name = _text(season.get("name"), where, "season name", required=True)
    games = _list(season.get("games"), f"{where} ({name})", "games")
    return {
        "name": name,
        "games": [_game(game, f"{where} ({name}), game {n}") for n, game in enumerate(games, start=1)],
    }


def _player(player, number):
    where = f"Player {number}"
    if not isinstance(player, dict):
        raise LegacyFormatError(f"{where} should be an object.")
    name = _text(player.get("name"), where, "name", required=True)
    where = f"Player {name!r}"
    status = _text(player.get("status"), where, "status") or Player.Status.ACTIVE.value
    if status not in Player.Status.values:
        raise LegacyFormatError(f"{where}: status should be Active or Retired.")
    awards = [_text(award, where, "award") for award in _list(player.get("awards", []), where, "awards")]
    seasons = _list(player.get("seasons"), where, "seasons")
    return {
        "name": name,
        "team": _text(player.get("team"), where, "team"),
        "primary_position": _position(player.get("primaryPosition"), where, "primary position", required=True),
        "secondary_position": _position(player.get("secondaryPosition"), where, "secondary position", False),
        "height": normalize_height(_text(player.get("height"), where, "height")),
        "weight": _whole_number(player.get("weight"), where, "weight"),
        "overall": _whole_number(player.get("overall"), where, "overall"),
        "super_bowl_wins": _whole_number(player.get("superBowlWins"), where, "Super Bowl wins") or 0,
        "awards": [award for award in awards if award],
        "status": status,
        "retirement_year": _whole_number(player.get("retirementYear"), where, "retirement year"),
        "seasons": [_season(season, f"{where}, season {n}") for n, season in enumerate(seasons, start=1)],
    }


def parse(data):
    """Check and normalize a loaded backup. Raises LegacyFormatError with a readable message."""
    if not isinstance(data, dict) or not isinstance(data.get("players"), list):
        raise LegacyFormatError(
            "This doesn't look like an old Madden Tracker backup: it has no list of players."
        )
    if not data["players"]:
        raise LegacyFormatError("The backup has no players in it.")
    return {"players": [_player(player, n) for n, player in enumerate(data["players"], start=1)]}
