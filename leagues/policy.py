"""Who can do what (KTD3). Views and templates ask these functions; they never check roles inline.

The rules, from the plan's "Who can do what" table:

| Action                                   | Site admin (non-member) | Commissioner | Member, own player | Member, other |
| View league data (R9)                    | No                      | Yes          | Yes                | Yes           |
| Create, edit, delete games (R14)         | No                      | Yes          | No                 | No            |
| Edit a stat line (R15)                   | No                      | Yes          | Yes                | No            |
| Edit player bio (R11)                    | No                      | Yes          | Yes                | No            |
| Add or retire players, link members      | No                      | Yes          | No                 | No            |
| Invite people (R2)                       | No                      | Yes          | No                 | No            |
| Generate reset link (R4, KTD11)          | Anyone                  | Members whose every league they run, except site admins | No | No |
| Disable accounts (R6), reset 2FA (KTD10) | Yes                     | No           | No                 | No            |

A site admin gets league rights only through a membership, like anyone else (KTD10).
"""

from .models import Membership


def membership_for(user, league):
    if not user.is_authenticated:
        return None
    return Membership.objects.select_related("player").filter(league=league, user=user).first()


def is_site_admin(user):
    return bool(user.is_authenticated and user.is_active and user.is_superuser)


def is_commissioner(user, league):
    membership = membership_for(user, league)
    return bool(membership and membership.is_commissioner)


def linked_player(user, league):
    membership = membership_for(user, league)
    return membership.player if membership else None


def can_view_league(user, league):
    return membership_for(user, league) is not None


def can_manage_games(user, league):
    return is_commissioner(user, league)


def can_manage_players(user, league):
    """Add, edit, or retire players, and link, unlink, or remove members (R10, R11)."""
    return is_commissioner(user, league)


def can_manage_seasons(user, league):
    """Add or rename teams and seasons (R8, R13): the commissioner runs the league's setup."""
    return is_commissioner(user, league)


def can_import(user, league):
    """Import an old backup into the league (R23): the commissioner only."""
    return is_commissioner(user, league)


def can_export(user, league):
    """Download the whole league as a league file (R27): the commissioner only."""
    return is_commissioner(user, league)


def can_invite(user, league):
    return is_commissioner(user, league)


def _commissioner_or_linked(user, player, membership=None):
    """``membership`` is the caller's already-loaded membership, used only when it is
    ``user``'s membership in ``player``'s league; otherwise it is looked up."""
    if membership is None or membership.user_id != user.pk or membership.league_id != player.league_id:
        membership = membership_for(user, player.league_id)
    if membership is None:
        return False
    return membership.is_commissioner or membership.player_id == player.pk


def can_edit_stat_line(user, player, membership=None):
    return _commissioner_or_linked(user, player, membership)


def can_edit_player_bio(user, player):
    return _commissioner_or_linked(user, player)


def can_manage_accounts(user):
    """Disable or re-enable accounts and reset 2FA: site admin only."""
    return is_site_admin(user)


def can_generate_reset_link(actor, target):
    if is_site_admin(actor):
        return True
    if not actor.is_authenticated or target.is_superuser:
        return False
    target_leagues = set(Membership.objects.filter(user=target).values_list("league_id", flat=True))
    if not target_leagues:
        return False
    run_by_actor = set(
        Membership.objects.filter(user=actor, is_commissioner=True, league_id__in=target_leagues).values_list(
            "league_id", flat=True
        )
    )
    return target_leagues == run_by_actor


def can_open_site_admin(user):
    """Site-admin pages need a superuser whose session passed 2FA (KTD10).

    ``is_verified`` is installed by django_otp's middleware; it is true only when
    this session signed in with (or set up) a confirmed TOTP device.
    """
    is_verified = getattr(user, "is_verified", None)
    return is_site_admin(user) and bool(is_verified and is_verified())
