"""Account URLs. The league routes use @league_view (see leagues.access)."""

from django.urls import path

from . import admin_views, views

app_name = "accounts"

urlpatterns = [
    path("accounts/login/", views.login_view, name="login"),
    path("accounts/login/verify/", views.login_verify_view, name="login_verify"),
    path("accounts/logout/", views.logout_view, name="logout"),
    path("accounts/", views.account_view, name="account"),
    path("accounts/password/", views.password_change_view, name="password_change"),
    path("accounts/2fa/", views.totp_setup_view, name="totp_setup"),
    path("invite/<str:token>/", views.invite_accept_view, name="invite"),
    path("reset/<str:token>/", views.reset_view, name="reset"),
    path("league/<int:league_id>/invites/new/", views.invite_create_view, name="invite_create"),
    path(
        "league/<int:league_id>/members/<int:user_id>/reset-link/",
        views.member_reset_link_view,
        name="member_reset_link",
    ),
    path("site-admin/", admin_views.users_view, name="site_admin"),
    path("site-admin/users/<int:user_id>/active/", admin_views.toggle_active_view, name="site_admin_toggle_active"),
    path("site-admin/users/<int:user_id>/reset-link/", admin_views.reset_link_view, name="site_admin_reset_link"),
    path("site-admin/users/<int:user_id>/reset-2fa/", admin_views.reset_2fa_view, name="site_admin_reset_2fa"),
]
