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

from . import policy
from .access import get_in_league, league_view, require
from .forms import BioForm, LeagueForm, LinkPlayerForm, PlayerForm, SeasonForm, TeamForm
from .game_views import next_unentered_game
from .models import League, Membership, Player, Season, Team

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
    next_game = next_unentered_game(player, games) if player and games else None

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
    rows = [
        {
            "membership": m,
            "can_reset": m.user != request.user and policy.can_generate_reset_link(request.user, m.user),
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
        messages.error(request, "A commissioner can't be removed from their league.")
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
