from django.urls import include, path
from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register(r"orgs", views.OrganizationViewSet, basename="organization")

urlpatterns = [
    path("", include(router.urls)),
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
]
