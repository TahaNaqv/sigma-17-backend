from django.contrib.auth import get_user_model
from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.viewsets import ModelViewSet

from accounts.models import Role

from .models import Membership, Organization
from .permissions import HasOrgPermission, get_request_org
from .serializers import (
    MembershipCreateSerializer,
    MembershipSerializer,
    MembershipUpdateSerializer,
    OrganizationSerializer,
    SwitchOrgSerializer,
)

User = get_user_model()


class IsSuperuser(IsAuthenticated):
    def has_permission(self, request, view):
        return super().has_permission(request, view) and request.user.is_superuser


class OrganizationViewSet(ModelViewSet):
    """Org CRUD. Listing/retrieve is filtered to the user's memberships
    (or all orgs for superuser). Create/update/delete are superuser-only."""

    serializer_class = OrganizationSerializer
    lookup_url_kwarg = "id"

    def get_permissions(self):
        if self.action in ("create", "destroy"):
            return [IsSuperuser()]
        if self.action in ("update", "partial_update"):
            # Superuser or someone with orgs.edit in that org
            return [IsAuthenticated()]
        return [IsAuthenticated()]

    def get_queryset(self):
        user = self.request.user
        if user.is_superuser:
            return Organization.objects.all()
        return Organization.objects.filter(
            memberships__user=user, memberships__status="active"
        ).distinct()

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)

    def update(self, request, *args, **kwargs):
        instance = self.get_object()
        if not request.user.is_superuser:
            # Org admins with orgs.edit can update their org
            from .permissions import user_has_any_permission_in_org
            if not user_has_any_permission_in_org(
                request.user, ["orgs.edit"], instance
            ):
                raise PermissionDenied("You may not edit this organization.")
        return super().update(request, *args, **kwargs)


class OrganizationMembershipsView(APIView):
    """List/create memberships for a given organization."""

    permission_classes = [IsAuthenticated]

    def _get_org(self, request, org_id):
        org = get_object_or_404(Organization, id=org_id)
        if not request.user.is_superuser:
            from .permissions import user_has_any_permission_in_org
            if not user_has_any_permission_in_org(
                request.user, ["memberships.view", "memberships.manage"], org
            ):
                raise PermissionDenied("You may not view members of this organization.")
        return org

    def get(self, request, org_id):
        org = self._get_org(request, org_id)
        memberships = (
            Membership.objects.filter(organization=org)
            .select_related("user")
            .prefetch_related("roles")
            .order_by("user__email")
        )
        return Response(MembershipSerializer(memberships, many=True).data)

    def post(self, request, org_id):
        org = self._get_org(request, org_id)
        if not request.user.is_superuser:
            from .permissions import user_has_any_permission_in_org
            if not user_has_any_permission_in_org(
                request.user, ["memberships.manage"], org
            ):
                raise PermissionDenied("You may not add members to this organization.")
        serializer = MembershipCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            user = User.objects.get(pk=data["userId"])
        except User.DoesNotExist:
            raise ValidationError({"userId": "User not found."})
        if Membership.objects.filter(user=user, organization=org).exists():
            raise ValidationError({"userId": "User is already a member of this organization."})
        with transaction.atomic():
            membership = Membership.objects.create(
                user=user, organization=org, status=data.get("status", "active")
            )
            role_ids = data.get("roleIds", [])
            if role_ids:
                membership.roles.set(Role.objects.filter(id__in=role_ids))
            # If the user has no active_organization yet, set this one
            profile = getattr(user, "profile", None)
            if profile and not profile.active_organization_id:
                profile.active_organization = org
                profile.save(update_fields=["active_organization"])
        return Response(
            MembershipSerializer(membership).data, status=status.HTTP_201_CREATED
        )


class OrganizationMembershipDetailView(APIView):
    """Update/delete a single membership."""

    permission_classes = [IsAuthenticated]

    def _get_membership(self, request, org_id, user_id, write=False):
        org = get_object_or_404(Organization, id=org_id)
        if not request.user.is_superuser:
            from .permissions import user_has_any_permission_in_org
            required = ["memberships.manage"] if write else [
                "memberships.view", "memberships.manage"
            ]
            if not user_has_any_permission_in_org(request.user, required, org):
                raise PermissionDenied()
        return get_object_or_404(Membership, organization=org, user_id=user_id)

    def get(self, request, org_id, user_id):
        m = self._get_membership(request, org_id, user_id, write=False)
        return Response(MembershipSerializer(m).data)

    def patch(self, request, org_id, user_id):
        m = self._get_membership(request, org_id, user_id, write=True)
        serializer = MembershipUpdateSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        if "status" in data:
            m.status = data["status"]
            m.save(update_fields=["status"])
        if "roleIds" in data:
            m.roles.set(Role.objects.filter(id__in=data["roleIds"]))
        return Response(MembershipSerializer(m).data)

    def delete(self, request, org_id, user_id):
        m = self._get_membership(request, org_id, user_id, write=True)
        # Clear active_organization for this user if it pointed here
        profile = getattr(m.user, "profile", None)
        if profile and profile.active_organization_id == m.organization_id:
            other = (
                Membership.objects.filter(user=m.user, status="active")
                .exclude(id=m.id)
                .select_related("organization")
                .first()
            )
            profile.active_organization = other.organization if other else None
            profile.save(update_fields=["active_organization"])
        m.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def switch_org(request):
    """Switch the current user's active organization. The new org must be
    one they have an active membership in (unless they're superuser)."""
    serializer = SwitchOrgSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    org_id = serializer.validated_data["organizationId"]
    user = request.user
    if user.is_superuser:
        org = get_object_or_404(Organization, id=org_id)
    else:
        membership = Membership.objects.filter(
            user=user, organization_id=org_id, status="active"
        ).select_related("organization").first()
        if not membership:
            raise PermissionDenied("You are not a member of that organization.")
        org = membership.organization
    profile = user.profile
    profile.active_organization = org
    profile.save(update_fields=["active_organization"])
    return Response({"activeOrganization": OrganizationSerializer(org).data})
