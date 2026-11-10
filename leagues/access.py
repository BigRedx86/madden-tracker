"""League-scoped fetching and the league view decorator (KTD3, R9).

Convention, enforced by find_unguarded_league_urls() and its test in
tests/leagues/test_policy.py:

- Every league-owned page lives under "league/<int:league_id>/" and its view
  is wrapped in @league_view. Any URL that captures ``league_id``, or whose
  route starts with "league/", must resolve to a wrapped view. Pages that
  are not about one league (for example creating a league) go elsewhere,
  such as "leagues/new/".
- Inside a league view, every object is fetched with get_in_league(), so an
  ID from another league is a 404 even through your own league's URL.
- Permission checks go through leagues.policy and require(); a member who can
  see an object but may not change it gets 403.
"""

from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.shortcuts import get_object_or_404
from django.urls import URLPattern, URLResolver, get_resolver

from .models import Membership

LEAGUE_ROUTE_PREFIX = "league/"


def league_view(view):
    """Require a signed-in member of the URL's league, else 404 (not 403, so IDs don't leak).

    Sets ``request.league`` and ``request.membership`` for the view.
    """

    @login_required
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        membership = (
            Membership.objects.select_related("league", "player")
            .filter(league_id=kwargs["league_id"], user=request.user)
            .first()
        )
        if membership is None:
            raise Http404("No such league.")
        request.league = membership.league
        request.membership = membership
        return view(request, *args, **kwargs)

    wrapper.league_scoped = True
    return wrapper


def get_in_league(model, league, **lookup):
    """Fetch one object of ``model`` that belongs to ``league``, or 404."""
    return get_object_or_404(model, **{model.league_lookup: league}, **lookup)


def require(allowed):
    if not allowed:
        raise PermissionDenied


def find_unguarded_league_urls(urlconf=None):
    """Routes that are league URLs by the convention above but lack @league_view."""
    unguarded = []

    def walk(patterns, prefix, captures_league):
        for pattern in patterns:
            route = prefix + str(pattern.pattern)
            captures = captures_league or "league_id" in pattern.pattern.regex.groupindex
            if isinstance(pattern, URLResolver):
                walk(pattern.url_patterns, route, captures)
            elif isinstance(pattern, URLPattern):
                is_league_url = captures or route.startswith(LEAGUE_ROUTE_PREFIX)
                if is_league_url and not getattr(pattern.callback, "league_scoped", False):
                    unguarded.append(route)

    walk(get_resolver(urlconf).url_patterns, "", False)
    return unguarded
