"""Template context for the base layout's league nav and league switcher."""

from .models import League


def league_nav(request):
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return {}
    return {
        # Set by leagues.access.league_view on league pages.
        "league": getattr(request, "league", None),
        "membership": getattr(request, "membership", None),
        "user_leagues": League.objects.filter(memberships__user=user).order_by("name"),
    }
