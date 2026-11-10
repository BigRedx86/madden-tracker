"""Season game lists, game management, and game detail (U6: R14, R15, F1, AE2).

Only the commissioner adds, edits, reorders, or deletes games (R14). Everyone
in the league sees the season page and game detail, with entry links only for
the stat lines policy lets them edit (R15).
"""

from django.contrib import messages
from django.db import transaction
from django.db.models import Max
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from stats.forms import StatLineForm
from stats.models import StatLine

from . import policy
from .access import get_in_league, league_view, require
from .forms import GameForm
from .models import Game, Player, Season

ENTERED = "entered"
DNP = "dnp"
MISSING = "missing"


def _status(has_line, did_not_play):
    return MISSING if not has_line else DNP if did_not_play else ENTERED


def entry_statuses(player, games):
    """{game id: ENTERED, DNP, or MISSING} for one player's lines in ``games``."""
    lines = dict(
        StatLine.objects.filter(player=player, game__in=games).values_list("game_id", "did_not_play")
    )
    return {game.pk: _status(game.pk in lines, lines.get(game.pk)) for game in games}


def _first_missing(games, statuses):
    """The first game in ``games`` whose status is MISSING (games without a status are skipped)."""
    return next((game for game in games if statuses.get(game.pk) == MISSING), None)


def next_unentered_game(player, games):
    """The first game, in schedule order, with no stat line for ``player``."""
    games = list(games)
    return _first_missing(games, entry_statuses(player, games))


def season_url(season, player=None):
    url = reverse("leagues:season", args=[season.team.league_id, season.pk])
    return f"{url}?player={player.pk}" if player else url


@league_view
def season_view(request, league_id, season_id):
    """The season's games. Shows one player's entry status: the member's own player,
    or for the commissioner any player picked with ?player= (how unclaimed lines get done)."""
    season = get_in_league(Season, request.league, pk=season_id)
    games = list(season.games.all())
    can_manage = policy.can_manage_games(request.user, request.league)

    focus = request.membership.player
    player_id = request.GET.get("player", "")
    if can_manage and player_id.isdigit():
        focus = get_in_league(Player, request.league, pk=int(player_id))

    statuses = entry_statuses(focus, games) if focus else {}
    next_game = _first_missing(games, statuses)
    can_enter = focus is not None and policy.can_edit_stat_line(request.user, focus, request.membership)
    rows = [
        {
            "game": game,
            "status": statuses.get(game.pk),
            "is_next": game == next_game,
            "can_enter": can_enter,
        }
        for game in games
    ]
    return render(
        request,
        "leagues/season_games.html",
        {
            "season": season,
            "rows": rows,
            "focus": focus,
            "can_manage": can_manage,
            "players": request.league.players.all() if can_manage else [],
            "is_unlinked_member": focus is None and not can_manage,
        },
    )


def _game_form_page(request, form, season, game=None, entries=()):
    return render(
        request,
        "leagues/game_form.html",
        {"form": form, "season": season, "game": game, "entries": entries},
    )


def _player_entries(season, data):
    """One optional stat-line form per active player on the season's team, for the add-game page."""
    players = season.team.players.filter(status=Player.Status.ACTIVE)
    return [(player, StatLineForm(data, prefix=f"player-{player.pk}")) for player in players]


@league_view
def game_create(request, league_id, season_id):
    """Add a game, and optionally each player's stat line for it, in one step.

    The commissioner may edit every stat line (R15), so entering them here is the
    same authority as the per-player entry page. A player left blank gets no line
    and can still enter their own later. Nothing is saved unless everything is valid.
    """
    season = get_in_league(Season, request.league, pk=season_id)
    require(policy.can_manage_games(request.user, request.league))
    next_week = (season.games.aggregate(last=Max("order"))["last"] or 0) + 1
    data = request.POST or None
    form = GameForm(data, instance=Game(season=season), initial={"order": next_week})
    entries = _player_entries(season, data)
    if request.method == "POST":
        game_valid = form.is_valid()
        lines_valid = all([stat_form.is_valid() for _, stat_form in entries])
        if game_valid and lines_valid:
            with transaction.atomic():
                game = form.save()
                saved = 0
                for player, stat_form in entries:
                    if stat_form.is_empty():
                        continue
                    StatLine(
                        player=player,
                        game=game,
                        did_not_play=stat_form.cleaned_data["did_not_play"],
                        values=stat_form.cleaned_data["values"],
                    ).save()
                    saved += 1
            lines_note = f" with {saved} stat line{'s' if saved != 1 else ''}" if saved else ""
            messages.success(request, f"Added {game}{lines_note}.")
            return redirect(season_url(season))
    sections = [
        dict(stat_form.sections(player), open=stat_form.is_bound and _has_input(stat_form))
        for player, stat_form in entries
    ]
    return _game_form_page(request, form, season, entries=sections)


def _has_input(stat_form):
    """Whether a submitted player section has anything in it, or errors, so it renders open."""
    prefix = stat_form.prefix + "-"
    return bool(stat_form.errors) or any(
        key.startswith(prefix) and (value or "").strip() for key, value in stat_form.data.items()
    )


@league_view
def game_edit(request, league_id, game_id):
    game = get_in_league(Game, request.league, pk=game_id)
    require(policy.can_manage_games(request.user, request.league))
    form = GameForm(request.POST or None, instance=game)
    if request.method == "POST" and form.is_valid():
        game = form.save()
        messages.success(request, f"Saved {game}.")
        return redirect(season_url(game.season))
    return _game_form_page(request, form, game.season, game)


@league_view
def game_delete(request, league_id, game_id):
    """Confirm, then delete the game and every stat line on it."""
    game = get_in_league(Game, request.league, pk=game_id)
    require(policy.can_manage_games(request.user, request.league))
    if request.method == "POST":
        season = game.season
        label = str(game)
        game.delete()
        messages.success(request, f"Deleted {label} and its stat lines.")
        return redirect(season_url(season))
    return render(
        request, "leagues/game_delete.html", {"game": game, "line_count": game.stat_lines.count()}
    )


@league_view
@require_POST
def game_move(request, league_id, game_id):
    """Swap a game's week with the game before or after it."""
    game = get_in_league(Game, request.league, pk=game_id)
    require(policy.can_manage_games(request.user, request.league))
    games = list(game.season.games.all())
    index = games.index(game)
    neighbor_index = index - 1 if request.POST.get("direction") == "up" else index + 1
    if 0 <= neighbor_index < len(games):
        neighbor = games[neighbor_index]
        with transaction.atomic():
            game.order, neighbor.order = neighbor.order, game.order
            if game.order == neighbor.order:
                # Same week number on both: nudge so the swap shows.
                game.order += -1 if neighbor_index < index else 1
                game.order = max(game.order, 0)
            game.save()
            neighbor.save()
    return redirect(season_url(game.season))


@league_view
def game_detail(request, league_id, game_id):
    """Every player's status for one game, with entry links where policy allows (R15, F1)."""
    game = get_in_league(Game, request.league, pk=game_id)
    lines = {line.player_id: line for line in game.stat_lines.all()}
    rows = []
    for player in request.league.players.all():
        line = lines.get(player.pk)
        status = _status(line is not None, line is not None and line.did_not_play)
        can_edit = policy.can_edit_stat_line(request.user, player, request.membership)
        rows.append({"player": player, "status": status, "can_edit": can_edit})
    return render(
        request,
        "leagues/game_detail.html",
        {"game": game, "rows": rows, "can_manage": policy.can_manage_games(request.user, request.league)},
    )
