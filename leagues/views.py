"""Leagues, roster, players, teams, seasons, and members (U5: R7-R13).

League pages use @league_view and fetch through get_in_league (KTD3); edit
rights come from leagues.policy.
"""

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from accounts import tokens
from accounts.models import AccountToken
from accounts.views import show_link
from stats import aggregate
from stats.models import StatLine

from . import policy
from .access import get_in_league, league_view, require
from .forms import BioForm, LeagueForm, LinkPlayerForm, PlayerForm, SeasonForm, TeamForm
from .game_views import next_unentered_game
from .models import Game, League, Membership, Player, Season, Team

User = get_user_model()


# --- Landing and league creation (R7) ----------------------------------------------


@login_required
def landing(request):
    """After sign-in: one league goes straight to it, several get a list, none get "Create a league"."""
    leagues = list(League.objects.filter(memberships__user=request.user).order_by("name"))
    if len(leagues) == 1:
        return redirect("leagues:league_home", leagues[0].pk)
    return render(request, "leagues/landing.html", {"leagues": leagues})


@login_required
def league_create(request):
    form = LeagueForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            league = form.save()
            Membership.objects.create(league=league, user=request.user, is_commissioner=True)
        messages.success(request, f"Created {league.name}. You're its commissioner.")
        return redirect("leagues:league_home", league.pk)
    return render(request, "leagues/league_form.html", {"form": form})


# --- League home -----------------------------------------------------------------------


@league_view
def league_home(request, league_id):
    league = request.league
    seasons = list(Season.objects.filter(team__league=league).select_related("team").order_by("-id"))
    season = seasons[0] if seasons else None
    season_id = request.GET.get("season", "")
    if season_id.isdigit():
        season = get_in_league(Season, league, pk=int(season_id))

    games = list(season.games.all()) if season else []
    player = request.membership.player
    can_enter = bool(
        player and season and policy.can_edit_stat_line(request.user, player, request.membership, season=season)
    )
    next_game = next_unentered_game(player, games) if can_enter and games else None

    checklist = None
    if policy.can_manage_seasons(request.user, league):
        checklist = [
            ("Add a team", league.teams.exists(), reverse("leagues:teams", args=[league.pk])),
            ("Add a season", bool(seasons), reverse("leagues:seasons", args=[league.pk])),
            ("Add players", league.players.exists(), reverse("leagues:player_create", args=[league.pk])),
            ("Invite members", league.memberships.count() > 1, reverse("leagues:members", args=[league.pk])),
        ]
        if all(done for _label, done, _url in checklist):
            checklist = None

    return render(
        request,
        "leagues/league_home.html",
        {
            "season": season,
            "other_seasons": [other for other in seasons if other != season],
            "games": games,
            "record": aggregate.record_counts(games),
            "player": player,
            "next_game": next_game,
            "season_locked": bool(season and season.is_locked),
            "checklist": checklist,
            "can_manage_games": policy.can_manage_games(request.user, league),
        },
    )


# --- Roster and players (R11, R12) -------------------------------------------------

ROSTER_SORTS = {
    "name": ["name"],
    "team": ["team__name", "name"],
    "position": ["primary_position", "name"],
}


@league_view
def roster(request, league_id):
    query = request.GET.get("q", "").strip()
    sort = request.GET.get("sort", "name")
    if sort not in ROSTER_SORTS:
        sort = "name"
    players = request.league.players.select_related("team").order_by(*ROSTER_SORTS[sort])
    if query:
        players = players.filter(name__icontains=query)
    players = list(players)
    linked = dict(
        Membership.objects.filter(league=request.league, player__isnull=False).values_list("player_id", "user__username")
    )
    return render(
        request,
        "leagues/roster.html",
        {
            "players": [{"player": p, "linked_to": linked.get(p.pk)} for p in players],
            "query": query,
            "sort": sort,
            "sorts": [("name", "Name"), ("team", "Team"), ("position", "Position")],
            "can_manage": policy.can_manage_players(request.user, request.league),
            "my_player_id": request.membership.player_id,
        },
    )


@league_view
def player_create(request, league_id):
    require(policy.can_manage_players(request.user, request.league))
    form = PlayerForm(request.POST or None, league=request.league)
    if request.method == "POST" and form.is_valid():
        player = form.save()
        messages.success(request, f"Added {player.name}.")
        return redirect("leagues:roster", request.league.pk)
    return render(request, "leagues/player_form.html", {"form": form, "player": None})


@league_view
def player_edit(request, league_id, player_id):
    """The commissioner edits every field; the linked member edits the bio only (R11)."""
    player = get_in_league(Player, request.league, pk=player_id)
    require(policy.can_edit_player_bio(request.user, player))
    form_class = PlayerForm if policy.can_manage_players(request.user, request.league) else BioForm
    form = form_class(request.POST or None, instance=player, league=request.league)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, f"Saved {player.name}.")
        return redirect("leagues:roster", request.league.pk)
    return render(
        request, "leagues/player_form.html", {"form": form, "player": player, "bio_only": form_class is BioForm}
    )


# --- Teams and seasons (R8, R13) -----------------------------------------------------


@league_view
def teams(request, league_id):
    can_manage = policy.can_manage_seasons(request.user, request.league)
    form = TeamForm(request.POST or None, league=request.league)
    if request.method == "POST":
        require(can_manage)
        if form.is_valid():
            team = form.save()
            messages.success(request, f"Added {team.name}.")
            return redirect("leagues:teams", request.league.pk)
    return render(
        request,
        "leagues/teams.html",
        {"teams": request.league.teams.all(), "form": form if can_manage else None},
    )


@league_view
def team_edit(request, league_id, team_id):
    team = get_in_league(Team, request.league, pk=team_id)
    require(policy.can_manage_seasons(request.user, request.league))
    form = TeamForm(request.POST or None, instance=team, league=request.league)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, f"Renamed to {team.name}.")
        return redirect("leagues:teams", request.league.pk)
    return render(request, "leagues/simple_form.html", {"form": form, "title": f"Rename {team.name}"})


@league_view
def seasons(request, league_id):
    can_manage = policy.can_manage_seasons(request.user, request.league)
    form = SeasonForm(request.POST or None, league=request.league)
    if request.method == "POST":
        require(can_manage)
        if form.is_valid():
            season = form.save()
            messages.success(request, f"Added season {season.name}.")
            return redirect("leagues:season", request.league.pk, season.pk)
    teams = request.league.teams.prefetch_related("seasons")
    return render(
        request,
        "leagues/seasons.html",
        {
            "teams": teams,
            "form": form if can_manage and request.league.teams.exists() else None,
            "can_manage": can_manage,
        },
    )


@league_view
def season_edit(request, league_id, season_id):
    season = get_in_league(Season, request.league, pk=season_id)
    require(policy.can_manage_seasons(request.user, request.league))
    form = SeasonForm(request.POST or None, instance=season, league=request.league)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, f"Saved season {season.name}.")
        return redirect("leagues:season", request.league.pk, season.pk)
    return render(request, "leagues/simple_form.html", {"form": form, "title": f"Edit season {season.name}"})


# --- Deleting teams, seasons, and players --------------------------------------------


def _plural(count, word):
    return f"{count} {word}{'' if count == 1 else 's'}"


@league_view
def team_delete(request, league_id, team_id):
    """Delete a team only when it has no seasons, so a mistaken team can go but games and
    stats can't be wiped by accident. Its players stay, with no team."""
    team = get_in_league(Team, request.league, pk=team_id)
    require(policy.can_manage_seasons(request.user, request.league))
    seasons = list(team.seasons.all())
    if request.method == "POST":
        if seasons:
            messages.error(request, f"{team.name} can't be deleted while it has seasons.")
            return redirect("leagues:team_delete", request.league.pk, team.pk)
        name = team.name
        team.delete()
        messages.success(request, f"Deleted {name}.")
        return redirect("leagues:teams", request.league.pk)
    return render(
        request,
        "leagues/team_delete.html",
        {"team": team, "seasons": seasons, "player_count": team.players.count()},
    )


@league_view
def season_delete(request, league_id, season_id):
    """Confirm, showing what goes with it, then delete the season, its games, and their stat lines."""
    season = get_in_league(Season, request.league, pk=season_id)
    require(policy.can_manage_seasons(request.user, request.league))
    if request.method == "POST":
        label = f"{season.team.name} {season.name}"
        season.delete()
        messages.success(request, f"Deleted season {label}, its games, and their stat lines.")
        return redirect("leagues:seasons", request.league.pk)
    game_count = season.games.count()
    line_count = StatLine.objects.filter(game__season=season).count()
    return render(
        request,
        "leagues/confirm_delete.html",
        {
            "title": f"Delete season {season.name}?",
            "consequence": f"This also deletes its {_plural(game_count, 'game')} and "
            f"{_plural(line_count, 'stat line')}. It can't be undone.",
            "button": "Delete season, games, and stat lines",
            "cancel_url": reverse("leagues:seasons", args=[request.league.pk]),
        },
    )


@league_view
def player_delete(request, league_id, player_id):
    """Confirm, then delete a player and their stat lines. A linked member stays in the league, unlinked."""
    player = get_in_league(Player, request.league, pk=player_id)
    require(policy.can_manage_players(request.user, request.league))
    if request.method == "POST":
        name = player.name
        player.delete()
        messages.success(request, f"Deleted {name} and their stat lines.")
        return redirect("leagues:roster", request.league.pk)
    line_count = player.stat_lines.count()
    linked = player.memberships.select_related("user").first()
    consequence = f"This also deletes their {_plural(line_count, 'stat line')}. It can't be undone."
    if linked:
        consequence += f" {linked.user.get_username()} stays in the league but won't be linked to a player."
    return render(
        request,
        "leagues/confirm_delete.html",
        {
            "title": f"Delete {player.name}?",
            "consequence": consequence,
            "alternative": "To keep their stats but take them off the active roster, retire them instead.",
            "button": "Delete player and stat lines",
            "cancel_url": reverse("leagues:player_edit", args=[request.league.pk, player.pk]),
        },
    )


# --- Members and invites (R2, R4, R10) ---------------------------------------------


def _pending_invites(league):
    return (
        AccountToken.objects.unspent()
        .filter(purpose=AccountToken.Purpose.INVITE, league=league, expires_at__gt=timezone.now())
        .select_related("created_by")
    )


def _members_page(request, link_errors=None):
    league = request.league
    memberships = league.memberships.select_related("user", "player").order_by("user__username")
    linked = {m.player_id: m.user.get_username() for m in memberships if m.player_id}
    commissioner_count = sum(1 for m in memberships if m.is_commissioner)
    rows = [
        {
            "membership": m,
            "can_reset": m.user != request.user and policy.can_generate_reset_link(request.user, m.user),
            # The last commissioner can't give up the role (the league always keeps one).
            "can_unmake": m.is_commissioner and commissioner_count > 1,
            "is_me": m.user_id == request.user.pk,
            "error": (link_errors or {}).get(m.user_id),
        }
        for m in memberships
    ]
    return render(
        request,
        "leagues/members.html",
        {
            "rows": rows,
            "players": [{"player": p, "linked_to": linked.get(p.pk)} for p in league.players.all()],
            "invites": _pending_invites(league),
        },
    )


@never_cache
@league_view
def members(request, league_id):
    require(policy.can_manage_players(request.user, request.league))
    return _members_page(request)


def _member(request, user_id):
    return get_object_or_404(Membership.objects.select_related("user", "player"), league=request.league, user_id=user_id)


@league_view
@require_POST
def member_link(request, league_id, user_id):
    """Link a member to one player; a player already linked to someone else is refused (R10)."""
    require(policy.can_manage_players(request.user, request.league))
    membership = _member(request, user_id)
    form = LinkPlayerForm(request.POST, league=request.league)
    if not form.is_valid():
        return _members_page(request, {membership.user_id: "Pick a player."})
    player = form.cleaned_data["player"]
    holder = Membership.objects.filter(player=player).exclude(pk=membership.pk).select_related("user").first()
    if holder is not None:
        error = f"{player.name} is already linked to {holder.user.get_username()}. Unlink them first."
        return _members_page(request, {membership.user_id: error})
    membership.player = player
    membership.save()
    messages.success(request, f"Linked {membership.user.get_username()} to {player.name}.")
    return redirect("leagues:members", request.league.pk)


@league_view
@require_POST
def member_unlink(request, league_id, user_id):
    require(policy.can_manage_players(request.user, request.league))
    membership = _member(request, user_id)
    membership.player = None
    membership.save()
    messages.success(request, f"Unlinked {membership.user.get_username()}.")
    return redirect("leagues:members", request.league.pk)


@league_view
def member_remove(request, league_id, user_id):
    """Confirm, then remove the membership. The player and its stats stay, unclaimed (R10)."""
    require(policy.can_manage_players(request.user, request.league))
    membership = _member(request, user_id)
    if membership.is_commissioner:
        # Removing a commissioner could leave the league with nobody to run it.
        messages.error(request, "A commissioner can't be removed. Remove their commissioner role first.")
        return redirect("leagues:members", request.league.pk)
    if request.method == "POST":
        name = membership.user.get_username()
        membership.delete()
        messages.success(request, f"Removed {name} from {request.league.name}.")
        return redirect("leagues:members", request.league.pk)
    return render(request, "leagues/member_remove.html", {"target": membership})


def _invite(request, invite_id):
    return get_in_league(AccountToken, request.league, pk=invite_id, purpose=AccountToken.Purpose.INVITE)


@league_view
@require_POST
def invite_revoke(request, league_id, invite_id):
    require(policy.can_invite(request.user, request.league))
    invite = _invite(request, invite_id)
    AccountToken.objects.unspent().filter(pk=invite.pk).update(voided_at=timezone.now())
    messages.success(request, "Invite revoked. Its link no longer works.")
    return redirect("leagues:members", request.league.pk)


@never_cache
@league_view
@require_POST
def invite_reissue(request, league_id, invite_id):
    """Revoke the old invite link and show a new one, once (KTD4)."""
    require(policy.can_invite(request.user, request.league))
    invite = _invite(request, invite_id)
    with transaction.atomic():
        revoked = AccountToken.objects.unspent().filter(pk=invite.pk).update(voided_at=timezone.now())
        if not revoked:
            messages.error(request, "That invite was already used or revoked.")
            return redirect("leagues:members", request.league.pk)
        raw = tokens.create_invite(request.league, created_by=request.user, invitee_name=invite.invitee_name)
    name = invite.invitee_name or "the person you are inviting"
    return show_link(
        request,
        title="New invite link",
        route="accounts:invite",
        raw=raw,
        lifetime=tokens.INVITE_LIFETIME,
        description=f"Send this to {name} to join {request.league.name}. The old link no longer works.",
        back_url=reverse("leagues:members", args=[request.league.pk]),
    )


# --- League settings, deleting a league, and commissioners -----------------------------


@league_view
def league_settings(request, league_id):
    """Rename the league; links to deleting it."""
    require(policy.can_manage_league(request.user, request.league))
    form = LeagueForm(request.POST or None, instance=request.league)
    if request.method == "POST" and form.is_valid():
        league = form.save()
        messages.success(request, f"Renamed the league to {league.name}.")
        return redirect("leagues:league_settings", league.pk)
    return render(request, "leagues/league_settings.html", {"form": form})


@league_view
def league_delete(request, league_id):
    """Delete the league and everything in it, after the commissioner types its name.

    Accounts stay: members may belong to other leagues.
    """
    league = request.league
    require(policy.can_manage_league(request.user, league))
    error = None
    if request.method == "POST":
        if request.POST.get("confirm_name", "") == league.name:
            name = league.name
            league.delete()
            messages.success(request, f"Deleted {name} and everything in it.")
            return redirect("leagues:home")
        error = "Type the league name exactly as shown to confirm."
    counts = [
        _plural(league.teams.count(), "team"),
        _plural(Season.objects.filter(team__league=league).count(), "season"),
        _plural(Game.objects.filter(season__team__league=league).count(), "game"),
        _plural(league.players.count(), "player"),
        _plural(StatLine.objects.filter(player__league=league).count(), "stat line"),
        _plural(league.memberships.count(), "member"),
    ]
    return render(request, "leagues/league_delete.html", {"counts": counts, "error": error})


@league_view
@require_POST
def member_make_commissioner(request, league_id, user_id):
    """Give a member the same powers as the commissioner (co-commissioner)."""
    require(policy.can_manage_league(request.user, request.league))
    membership = _member(request, user_id)
    if not membership.is_commissioner:
        membership.is_commissioner = True
        membership.save()
        messages.success(request, f"{membership.user.get_username()} is now a commissioner.")
    return redirect("leagues:members", request.league.pk)


@league_view
@require_POST
def member_remove_commissioner(request, league_id, user_id):
    """Take away a member's commissioner role, or step down yourself. A league always keeps one."""
    require(policy.can_manage_league(request.user, request.league))
    with transaction.atomic():
        membership = _member(request, user_id)
        commissioners = request.league.memberships.filter(is_commissioner=True)
        if membership.is_commissioner and commissioners.count() <= 1:
            messages.error(
                request,
                f"{membership.user.get_username()} is the last commissioner. "
                "Make someone else a commissioner first.",
            )
            return redirect("leagues:members", request.league.pk)
        membership.is_commissioner = False
        membership.save()
    if membership.user_id == request.user.pk:
        messages.success(request, f"You stepped down as commissioner of {request.league.name}.")
        return redirect("leagues:league_home", request.league.pk)
    messages.success(request, f"{membership.user.get_username()} is no longer a commissioner.")
    return redirect("leagues:members", request.league.pk)
