from django.urls import include, path
from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register(r"orgs", views.OrganizationViewSet, basename="organization")

urlpatterns = [
    path("", include(router.urls)),
    path(
        "class-aliases/",
        views.ReservingClassAliasListCreateView.as_view(),
        name="class-alias-list",
    ),
    path(
        "class-aliases/<uuid:alias_id>/",
        views.ReservingClassAliasDetailView.as_view(),
        name="class-alias-detail",
    ),
    path(
        "orgs/<uuid:org_id>/memberships/",
        views.OrganizationMembershipsView.as_view(),
        name="org-memberships",
    ),
    path(
        "orgs/<uuid:org_id>/memberships/<int:user_id>/",
        views.OrganizationMembershipDetailView.as_view(),
        name="org-membership-detail",
    ),
    path("auth/switch-org/", views.switch_org, name="auth-switch-org"),
    path("scenario-sets/", views.ScenarioSetListCreateView.as_view(), name="scenario-sets"),
    path("scenario-sets/<uuid:pk>/", views.ScenarioSetDetailView.as_view(), name="scenario-set-detail"),
    path("upr-methods/", views.UprMethodCatalogView.as_view(), name="upr-methods"),
    path("upr-policies/", views.UprMethodPolicyListCreateView.as_view(), name="upr-policies"),
    path("upr-policies/<uuid:pk>/", views.UprMethodPolicyDetailView.as_view(), name="upr-policy-detail"),
]
