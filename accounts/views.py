from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet

from tenants.models import Membership
from tenants.permissions import get_request_org
from tenants.serializers import OrganizationSerializer

from .models import Permission, Role, UserProfile
from .permissions import HasPermission, get_user_permission_keys
from .serializers import (
    ChangePasswordSerializer,
    PermissionCreateUpdateSerializer,
    PermissionSerializer,
    ProfileSerializer,
    RoleSerializer,
    UserCreateUpdateSerializer,
    UserListSerializer,
)

User = get_user_model()


def _membership_summary(membership):
    org = membership.organization
    return {
        "id": str(membership.id),
        "organization": {
            "id": str(org.id),
            "name": org.name,
            "slug": org.slug,
            "isActive": org.is_active,
        },
        "roleIds": list(membership.roles.values_list("id", flat=True)),
        "status": membership.status,
    }


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def auth_me(request):
    """Return current user with memberships, active org, and active-org permissions."""
    user = request.user
    try:
        profile = user.profile
        user_status = profile.status
    except UserProfile.DoesNotExist:
        profile = None
        user_status = "active" if user.is_active else "inactive"

    memberships = (
        Membership.objects.filter(user=user)
        .select_related("organization")
        .prefetch_related("roles")
        .order_by("organization__name")
    )
    membership_summaries = [_membership_summary(m) for m in memberships]

    active_org = get_request_org(request)
    if active_org is None and profile and profile.active_organization_id:
        active_org = profile.active_organization

    active_membership = next(
        (m for m in memberships if active_org and m.organization_id == active_org.id),
        None,
    )
    role_ids = (
        list(active_membership.roles.values_list("id", flat=True))
        if active_membership
        else []
    )
    permission_keys = list(
        get_user_permission_keys(user, org=active_org) if active_org or user.is_superuser else set()
    )

    parts = [user.first_name, user.last_name]
    name = " ".join(p for p in parts if p).strip() or user.username

    return Response({
        "user": {
            "id": user.id,
            "name": name,
            "email": user.email,
            "status": user_status,
            "isSuperAdmin": bool(user.is_superuser),
            "roleIds": role_ids,
            "permissionKeys": permission_keys,
        },
        "activeOrganization": (
            OrganizationSerializer(active_org).data if active_org else None
        ),
        "memberships": membership_summaries,
    })


@api_view(["GET", "PATCH"])
@permission_classes([IsAuthenticated])
def profile(request):
    user = request.user
    if request.method == "GET":
        serializer = ProfileSerializer(user)
        return Response(serializer.data)
    serializer = ProfileSerializer(user, data=request.data, partial=True)
    serializer.is_valid(raise_exception=True)
    serializer.save()
    return Response(serializer.data)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def change_password(request):
    serializer = ChangePasswordSerializer(data=request.data, context={"request": request})
    serializer.is_valid(raise_exception=True)
    request.user.set_password(serializer.validated_data["newPassword"])
    request.user.save()
    return Response({"detail": "Password changed successfully."})


class UserViewSet(ModelViewSet):
    """User CRUD.

    Listing/retrieve is org-scoped (you only see users in your active org).
    Superuser sees all users.
    Creation of users with Membership in the active org is done here for
    convenience; assigning roles in OTHER orgs requires the memberships API.
    """
    permission_classes = [IsAuthenticated]
    lookup_url_kwarg = "id"

    def get_serializer_class(self):
        if self.action in ("create", "update", "partial_update"):
            return UserCreateUpdateSerializer
        return UserListSerializer

    def get_serializer_context(self):
        ctx = super().get_serializer_context()
        ctx["organization"] = get_request_org(self.request)
        return ctx

    def get_queryset(self):
        user = self.request.user
        if user.is_superuser:
            return User.objects.all().order_by("id")
        org = get_request_org(self.request)
        if org is None:
            return User.objects.none()
        return User.objects.filter(
            memberships__organization=org, memberships__status="active"
        ).distinct().order_by("id")

    def get_permissions(self):
        base = [IsAuthenticated()]
        if self.action in ("list", "retrieve"):
            return base + [HasPermission(["users.view"])]
        if self.action == "create":
            return base + [HasPermission(["users.create"])]
        if self.action in ("update", "partial_update"):
            return base + [HasPermission(["users.edit"])]
        if self.action == "destroy":
            return base + [HasPermission(["users.delete"])]
        return base

    def create(self, request, *args, **kwargs):
        serializer = UserCreateUpdateSerializer(
            data=request.data, context=self.get_serializer_context()
        )
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        return Response(
            UserListSerializer(user, context=self.get_serializer_context()).data,
            status=status.HTTP_201_CREATED,
        )

    def update(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = UserCreateUpdateSerializer(
            instance,
            data=request.data,
            partial=True,
            context=self.get_serializer_context(),
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(
            UserListSerializer(instance, context=self.get_serializer_context()).data
        )

    def perform_destroy(self, instance):
        """Remove the user from the current organization.

        Org-scoped semantics: deleting a user from the UsersPage means
        "remove them from this org," not "deactivate them globally." The
        user may still have memberships in other orgs.

        Superusers acting WITHOUT an active org fall back to a global
        soft-deactivation (legacy behavior).
        """
        org = get_request_org(self.request)
        if org is not None:
            removed = Membership.objects.filter(
                user=instance, organization=org
            ).delete()[0]
            # If the user's active org pointed here, find a new one.
            profile = getattr(instance, "profile", None)
            if profile and profile.active_organization_id == org.id:
                other = (
                    Membership.objects.filter(user=instance, status="active")
                    .select_related("organization")
                    .first()
                )
                profile.active_organization = other.organization if other else None
                profile.save(update_fields=["active_organization"])
            if removed == 0 and not self.request.user.is_superuser:
                # Non-superuser tried to remove a non-member -> 404 already by qs filter
                pass
            return
        # Superuser, no active org: global deactivate as a fallback.
        instance.is_active = False
        instance.save()


class RoleViewSet(ModelViewSet):
    queryset = Role.objects.all().order_by("id")
    serializer_class = RoleSerializer
    permission_classes = [IsAuthenticated]
    lookup_url_kwarg = "id"

    def get_permissions(self):
        base = [IsAuthenticated()]
        if self.action in ("list", "retrieve"):
            return base + [HasPermission(["roles.view"])]
        if self.action == "create":
            return base + [HasPermission(["roles.create"])]
        if self.action in ("update", "partial_update"):
            return base + [HasPermission(["roles.edit"])]
        if self.action == "destroy":
            return base + [HasPermission(["roles.delete"])]
        return base


class PermissionViewSet(ModelViewSet):
    queryset = Permission.objects.all().order_by("id")
    permission_classes = [IsAuthenticated]
    lookup_url_kwarg = "id"

    def get_permissions(self):
        return [IsAuthenticated(), HasPermission(["permissions.manage"])]

    def get_serializer_class(self):
        if self.action in ("create", "update", "partial_update"):
            return PermissionCreateUpdateSerializer
        return PermissionSerializer
