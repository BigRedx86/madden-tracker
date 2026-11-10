"""League export and re-import as a native league file (U9: R27).

A league file is a JSON document marked with FORMAT and VERSION. It holds the
league's teams, seasons, games, players, and stat lines, and nothing about
accounts: no users, emails, password hashes, tokens, memberships, or import
drafts. Importing one creates a new league with the importer as commissioner;
members are invited again. Every object goes through the models' own
validation, in one transaction, so a bad file saves nothing.

Inside the file, seasons are tied to teams by team name (unique per league),
players to teams the same way, and stat lines to players and games by the
``key`` numbers the export assigns. Lists are written in creation order, so
"latest season" and similar orderings survive the round trip.
"""

from django.core.exceptions import ValidationError
from django.db import DatabaseError, transaction
from django.utils import timezone

from leagues.models import Game, League, Membership, Player, Season, Team
from stats.models import StatLine

FORMAT = "madden-tracker-league"
VERSION = 1

PLAYER_FIELDS = [
    "name", "overall", "primary_position", "secondary_position", "height", "weight",
    "super_bowl_wins", "status", "retirement_year", "awards",
]
GAME_FIELDS = ["opponent", "team_score", "opponent_score", "recorded_result", "is_playoff"]


class NativeFormatError(ValueError):
    """The file isn't a league file this site can import; the message says why."""


def is_native(data):
    return isinstance(data, dict) and data.get("format") == FORMAT


def export_league(league):
    teams = list(Team.objects.filter(league=league).order_by("id"))
    seasons = list(Season.objects.filter(team__league=league).select_related("team").order_by("id"))
    players = list(Player.objects.filter(league=league).select_related("team").order_by("id"))
    player_keys = {player.pk: key for key, player in enumerate(players, start=1)}
    game_keys = {}

    season_docs = []
    for season in seasons:
        games = []
        for game in season.games.order_by("order", "id"):
            game_keys[game.pk] = len(game_keys) + 1
            games.append({"key": game_keys[game.pk], "week": game.order,
                          **{field: getattr(game, field) for field in GAME_FIELDS}})
        season_docs.append({"team": season.team.name, "name": season.name, "games": games})

    lines = (
        StatLine.objects.filter(player__league=league)
        .order_by("game__season_id", "game__order", "game_id", "player_id")
    )
    return {
        "format": FORMAT,
        "version": VERSION,
        "exported_at": timezone.now().isoformat(timespec="seconds"),
        "league": {"name": league.name},
        "teams": [{"name": team.name} for team in teams],
        "seasons": season_docs,
        "players": [
            {"key": player_keys[player.pk], "team": player.team.name if player.team else None,
             **{field: getattr(player, field) for field in PLAYER_FIELDS}}
            for player in players
        ],
        "stat_lines": [
            {"player": player_keys[line.player_id], "game": game_keys[line.game_id],
             "did_not_play": line.did_not_play, "values": line.values}
            for line in lines
        ],
    }


def check_header(data):
    """Raise NativeFormatError unless ``data`` is a league file of a version this site reads."""
    if not is_native(data):
        raise NativeFormatError("That file isn't a league file exported from Madden League Tracker.")
    if data.get("version") != VERSION:
        raise NativeFormatError(
            f"This league file is version {data.get('version')}, and this site only reads version {VERSION}. "
            "Nothing was imported."
        )


def _save(obj, where):
    try:
        obj.save()
    except ValidationError as error:
        raise NativeFormatError(f"{where}: {' '.join(error.messages)} Nothing was imported.")


def _create(data, user):
    league = League(name=data["league"]["name"])
    _save(league, "League")
    Membership.objects.create(league=league, user=user, is_commissioner=True)

    teams = {}
    for team_doc in data["teams"]:
        team = Team(league=league, name=team_doc["name"])
        _save(team, f"Team {team.name!r}")
        teams[team.name] = team

    def team_named(name, where):
        if name is None:
            return None
        if name not in teams:
            raise NativeFormatError(f"{where} names a team ({name!r}) that isn't in the file. Nothing was imported.")
        return teams[name]

    games = {}
    for season_doc in data["seasons"]:
        where = f"Season {season_doc['name']!r}"
        season = Season(team=team_named(season_doc["team"], where), name=season_doc["name"])
        _save(season, where)
        for game_doc in season_doc["games"]:
            game = Game(season=season, order=game_doc["week"], **{f: game_doc[f] for f in GAME_FIELDS})
            _save(game, f"{where}, week {game_doc['week']}")
            if game_doc["key"] in games:
                raise NativeFormatError("Two games in the file share a key. Nothing was imported.")
            games[game_doc["key"]] = game

    players = {}
    for player_doc in data["players"]:
        where = f"Player {player_doc['name']!r}"
        player = Player(league=league, team=team_named(player_doc["team"], where),
                        **{field: player_doc[field] for field in PLAYER_FIELDS})
        _save(player, where)
        if player_doc["key"] in players:
            raise NativeFormatError("Two players in the file share a key. Nothing was imported.")
        players[player_doc["key"]] = player

    for line_doc in data["stat_lines"]:
        player, game = players.get(line_doc["player"]), games.get(line_doc["game"])
        if player is None or game is None:
            raise NativeFormatError("A stat line refers to a player or game that isn't in the file. Nothing was imported.")
        _save(
            StatLine(player=player, game=game, did_not_play=line_doc["did_not_play"], values=line_doc["values"]),
            f"{player.name}'s stats for {game.season.name} week {game.order}",
        )
    return league


def import_league(data, user):
    """Create a new league from a league file, with ``user`` as its commissioner. All or nothing."""
    check_header(data)
    try:
        with transaction.atomic():
            return _create(data, user)
    except NativeFormatError:
        raise
    except (KeyError, TypeError, AttributeError, ValueError, DatabaseError):
        raise NativeFormatError("The league file is incomplete or damaged, so nothing was imported.")
