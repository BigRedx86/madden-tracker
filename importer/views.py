"""Legacy backup import with commissioner review (U8: R23-R26, F2, KTD7), and
league export and re-import (U9: R27).

Legacy: upload -> parse and align (pure, importer.legacy / importer.align) ->
draft -> the commissioner resolves every conflict -> confirm saves everything
in one transaction and deletes the draft. Every legacy page is commissioner-only.

League files (importer.native): export is commissioner-only. Importing one
creates a new league, so its page lives outside the league prefix at
"leagues/import/" and is open to any signed-in user, like creating a league.
Each upload page recognizes the other format and points to the right page.
"""

import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import DatabaseError, transaction
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify
from django.views.decorators.http import require_POST

from leagues import policy
from leagues.access import league_view, require
from leagues.models import Game, Player, Season, Team
from stats.models import StatLine

from . import align, native
from .forms import LegacyUploadForm, NativeUploadForm
from .models import ImportDraft


def _commissioner_only(request):
    require(policy.can_import(request.user, request.league))


def _draft(league):
    return ImportDraft.objects.filter(league=league).first()


def _review_url(league, anchor=""):
    url = reverse("importer:review", args=[league.pk])
    return f"{url}#{anchor}" if anchor else url


@league_view
def upload(request, league_id):
    _commissioner_only(request)
    league = request.league
    form = LegacyUploadForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        proposal = align.propose(form.parsed)
        ImportDraft.objects.filter(league=league).delete()
        ImportDraft.objects.create(
            league=league,
            created_by=request.user,
            file_name=form.cleaned_data["backup"].name[:255],
            proposal=proposal,
        )
        return redirect(_review_url(league))
    return render(
        request,
        "importer/upload.html",
        {"form": form, "draft": _draft(league), "existing_players": league.players.count()},
    )


def _card(conflict, resolutions):
    resolution = resolutions.get(conflict["id"])
    return {
        "conflict": conflict,
        "resolution": resolution,
        "selected": resolution["choice"] if resolution else conflict["suggested"],
        "typed": resolution or {},
    }


@league_view
def review(request, league_id):
    _commissioner_only(request)
    league = request.league
    draft = _draft(league)
    if draft is None:
        return redirect("importer:upload", league.pk)
    proposal, resolutions = draft.proposal, draft.resolutions
    conflicts = {c["id"]: c for c in proposal["conflicts"]}
    names = [p["name"] for p in proposal["players"]]

    seasons = []
    for index, season in enumerate(proposal["seasons"]):
        weeks = []
        for i, slot in enumerate(season["slots"]):
            ids = [cid for cid in (slot["opponent_conflict"], slot["score_conflict"]) if cid]
            weeks.append({
                "number": i + 1,
                "slot": slot,
                "players": [names[line["player"]] for line in slot["lines"]],
                "cards": [_card(conflicts[cid], resolutions) for cid in ids],
            })
        missing = [c for c in proposal["conflicts"] if c["season"] == index and c["type"] == align.MISSING_SCORE]
        seasons.append({
            "index": index,
            "season": season,
            "weeks": weeks,
            "floating": [_card(conflicts[entry["conflict"]], resolutions) for entry in season["floating"]],
            "missing_open": sum(1 for c in missing if c["id"] not in resolutions),
        })

    total, resolved = len(proposal["conflicts"]), align.resolved_count(proposal, resolutions)
    return render(
        request,
        "importer/review.html",
        {
            "draft": draft,
            "players": proposal["players"],
            "seasons": seasons,
            "total": total,
            "resolved": resolved,
            "remaining": total - resolved,
            "existing_players": league.players.count(),
        },
    )


@league_view
@require_POST
def resolve(request, league_id):
    _commissioner_only(request)
    league = request.league
    draft = _draft(league)
    if draft is None:
        return redirect("importer:upload", league.pk)
    conflict_id = request.POST.get("conflict", "")
    conflict = next((c for c in draft.proposal["conflicts"] if c["id"] == conflict_id), None)
    if conflict is None:
        messages.error(request, "That conflict isn't part of this import.")
        return redirect(_review_url(league))
    try:
        draft.resolutions = {**draft.resolutions, conflict_id: align.resolve(conflict, request.POST)}
    except ValueError as error:
        messages.error(request, f"{conflict['summary']}: {error}")
    else:
        draft.save()
    return redirect(_review_url(league, conflict_id))


@league_view
@require_POST
def acknowledge_missing(request, league_id, season):
    _commissioner_only(request)
    league = request.league
    draft = _draft(league)
    if draft is None:
        return redirect("importer:upload", league.pk)
    draft.resolutions = align.acknowledge_missing(draft.proposal, draft.resolutions, season)
    draft.save()
    return redirect(_review_url(league, f"season-{season}"))


@league_view
@require_POST
def discard(request, league_id):
    _commissioner_only(request)
    ImportDraft.objects.filter(league=request.league).delete()
    messages.success(request, "Discarded the import. Nothing from the backup was saved.")
    return redirect("leagues:league_home", request.league.pk)


def save_plan(league, plan):
    """Write an align.build() plan into ``league``. The caller runs this inside one transaction."""
    teams = {}

    def team_named(name):
        name = name or league.name
        if name not in teams:
            teams[name], _created = Team.objects.get_or_create(league=league, name=name)
        return teams[name]

    players = []
    for bio in plan["players"]:
        player = Player(
            league=league,
            team=team_named(bio["team"]) if bio["team"] else None,
            name=bio["name"],
            overall=bio["overall"],
            primary_position=bio["primary_position"],
            secondary_position=bio["secondary_position"],
            height=bio["height"],
            weight=bio["weight"],
            super_bowl_wins=bio["super_bowl_wins"],
            status=bio["status"],
            retirement_year=bio["retirement_year"],
            awards=bio["awards"],
        )
        player.save()
        players.append(player)

    for planned in plan["seasons"]:
        season = Season(team=team_named(planned["team"]), name=planned["name"])
        season.save()
        for planned_game in planned["games"]:
            game = Game(
                season=season,
                order=planned_game["order"],
                opponent=planned_game["opponent"],
                team_score=planned_game["team_score"],
                opponent_score=planned_game["opponent_score"],
                recorded_result=planned_game["recorded_result"],
                is_playoff=planned_game["is_playoff"],
            )
            game.save()
            for line in planned_game["lines"]:
                StatLine(
                    player=players[line["player"]],
                    game=game,
                    did_not_play=line["did_not_play"],
                    values=line["values"],
                ).save()


def _problem_text(error):
    if isinstance(error, ValidationError):
        return " ".join(error.messages)
    return "The database refused the data."


@league_view
@require_POST
def confirm(request, league_id):
    _commissioner_only(request)
    league = request.league
    draft = _draft(league)
    if draft is None:
        return redirect("importer:upload", league.pk)

    existing = league.players.count()
    if existing and request.POST.get("add_alongside") != "yes":
        messages.error(
            request,
            f"This league already has {existing} player{'s' if existing != 1 else ''}. Tick the box to confirm "
            "you want the backup added alongside them.",
        )
        return redirect(_review_url(league, "confirm"))

    try:
        plan = align.build(draft.proposal, draft.resolutions)
    except align.NotReady as not_ready:
        for problem in not_ready.problems:
            messages.error(request, problem)
        return redirect(_review_url(league, "confirm"))

    try:
        with transaction.atomic():
            save_plan(league, plan)
            draft.delete()
    except (ValidationError, DatabaseError) as error:
        messages.error(request, f"Nothing was saved. {_problem_text(error)}")
        return redirect(_review_url(league, "confirm"))

    games = sum(len(s["games"]) for s in plan["seasons"])
    messages.success(
        request,
        f"Imported {len(plan['players'])} players, {len(plan['seasons'])} seasons, and {games} games. "
        "Next, invite each friend and link them to their player on the Members page.",
    )
    return redirect("leagues:league_home", league.pk)


# --- League files (U9) -----------------------------------------------------------------


@league_view
def export(request, league_id):
    require(policy.can_export(request.user, request.league))
    league = request.league
    file_name = f"{slugify(league.name) or 'league'}-{timezone.localdate():%Y-%m-%d}.json"
    response = HttpResponse(
        json.dumps(native.export_league(league), indent=2, ensure_ascii=False),
        content_type="application/json; charset=utf-8",
    )
    response["Content-Disposition"] = f'attachment; filename="{file_name}"'
    response["Cache-Control"] = "no-store"
    return response


@login_required
def native_import(request):
    form = NativeUploadForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        try:
            league = native.import_league(form.document, request.user)
        except native.NativeFormatError as error:
            form.add_error("league_file", str(error))
        else:
            messages.success(
                request,
                f"Imported {league.name}. You're its commissioner. Invite each member again and link them "
                "to their player on the Members page.",
            )
            return redirect("leagues:league_home", league.pk)
    return render(request, "importer/native_upload.html", {"form": form})
