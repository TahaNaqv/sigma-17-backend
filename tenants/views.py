from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
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


# ---------------------------------------------------------------------------
# Sensitivity scenario sets
# ---------------------------------------------------------------------------


class ScenarioSetListCreateView(APIView):
    """GET  /api/scenario-sets/     list this org's sets (active by default)
    POST /api/scenario-sets/     create a new set (version 1)
    """

    def get_permissions(self):
        from accounts.permissions import HasPermission
        # HasPermission is ANY-of. Reading the sets is a prerequisite for running a
        # sensitivity job, so `module2.run` alone must be enough — otherwise an org
        # with custom roles gets a runner who cannot see what to run.
        need = (
            ["scenarios.view", "module2.run"]
            if self.request.method == "GET"
            else ["scenarios.manage"]
        )
        return [IsAuthenticated(), HasPermission(need)]

    def _org(self, request):
        org = get_request_org(request)
        if org is None:
            raise PermissionDenied("Select an organization first.")
        return org

    def get(self, request):
        from .serializers import ScenarioSetSerializer
        from .models import ScenarioSet
        qs = ScenarioSet.objects.filter(organization=self._org(request))
        if request.query_params.get("all") not in ("1", "true", "yes"):
            qs = qs.filter(is_active=True)
        qs = qs.prefetch_related("scenarios")
        return Response({"results": ScenarioSetSerializer(qs, many=True).data})

    def post(self, request):
        from .serializers import ScenarioSetSerializer
        ser = ScenarioSetSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        obj = ser.save(organization=self._org(request), created_by=request.user)
        return Response(ScenarioSetSerializer(obj).data, status=status.HTTP_201_CREATED)


class ScenarioSetDetailView(APIView):
    """GET / PUT / DELETE one set.

    PUT **forks a new version** rather than mutating in place: historic jobs
    reference the version they ran under, and a sensitivity disclosure must remain
    reproducible after the definition changes.
    """

    def get_permissions(self):
        from accounts.permissions import HasPermission
        need = (
            ["scenarios.view", "module2.run"]
            if self.request.method == "GET"
            else ["scenarios.manage"]
        )
        return [IsAuthenticated(), HasPermission(need)]

    def _get(self, request, pk):
        from .models import ScenarioSet
        org = get_request_org(request)
        if org is None:
            raise PermissionDenied("Select an organization first.")
        return get_object_or_404(ScenarioSet, pk=pk, organization=org)

    def get(self, request, pk):
        from .serializers import ScenarioSetSerializer
        return Response(ScenarioSetSerializer(self._get(request, pk)).data)

    @transaction.atomic
    def put(self, request, pk):
        from .models import Scenario, ScenarioSet
        from .serializers import ScenarioSetSerializer
        current = self._get(request, pk)
        ser = ScenarioSetSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        scenarios = ser.validated_data.pop("scenarios", [])

        next_version = (
            ScenarioSet.objects.filter(
                organization=current.organization, name=current.name
            ).order_by("-version").first().version + 1
        )
        # Deactivate first: the partial unique constraint allows only one active
        # set per (org, name).
        ScenarioSet.objects.filter(
            organization=current.organization, name=current.name, is_active=True
        ).update(is_active=False)
        new = ScenarioSet.objects.create(
            organization=current.organization,
            name=current.name,
            description=ser.validated_data.get("description", current.description),
            version=next_version,
            is_active=True,
            created_by=request.user,
        )
        Scenario.objects.bulk_create([
            Scenario(scenario_set=new, order=i, **s) for i, s in enumerate(scenarios)
        ])
        return Response(ScenarioSetSerializer(new).data)

    def delete(self, request, pk):
        obj = self._get(request, pk)
        obj.is_active = False
        obj.save(update_fields=["is_active"])
        return Response(status=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# UPR method policy (requirement 4)
# ---------------------------------------------------------------------------


def _upr_permissions(method: str):
    from accounts.permissions import HasPermission

    # HasPermission is ANY-of. Reading the policy is a prerequisite for running a job, so
    # `module1.run` alone must be enough to READ — otherwise an org with custom roles gets
    # a runner who cannot see which methodology their own run will use.
    need = (
        ["upr_policy.view", "module1.run"]
        if method == "GET"
        else ["upr_policy.manage"]
    )
    return [IsAuthenticated(), HasPermission(need)]


class UprMethodPolicyListCreateView(APIView):
    """GET/POST /api/upr-policies/ — the org's UPR earning-method policies."""

    def get_permissions(self):
        return _upr_permissions(self.request.method)

    def _org(self, request):
        org = get_request_org(request)
        if org is None:
            raise PermissionDenied("Select an organization first.")
        return org

    def get(self, request):
        from .models import UprMethodPolicy
        from .serializers import UprMethodPolicySerializer

        qs = UprMethodPolicy.objects.filter(organization=self._org(request))
        if request.query_params.get("all") not in ("1", "true", "yes"):
            qs = qs.filter(is_active=True)
        return Response(
            {"results": UprMethodPolicySerializer(qs.prefetch_related("rules"), many=True).data}
        )

    def post(self, request):
        from .serializers import UprMethodPolicySerializer

        ser = UprMethodPolicySerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        obj = ser.save(organization=self._org(request), created_by=request.user)
        return Response(UprMethodPolicySerializer(obj).data, status=status.HTTP_201_CREATED)


class UprMethodPolicyDetailView(APIView):
    """GET / PUT / DELETE one policy.

    PUT **forks a new version** rather than mutating in place: jobs reference the version
    they ran under, and a reserving basis must stay reproducible after the policy changes.
    """

    def get_permissions(self):
        return _upr_permissions(self.request.method)

    def _get(self, request, pk):
        from .models import UprMethodPolicy

        org = get_request_org(request)
        if org is None:
            raise PermissionDenied("Select an organization first.")
        return get_object_or_404(UprMethodPolicy, pk=pk, organization=org)

    def get(self, request, pk):
        from .serializers import UprMethodPolicySerializer

        return Response(UprMethodPolicySerializer(self._get(request, pk)).data)

    @transaction.atomic
    def put(self, request, pk):
        from .models import UprMethodPolicy, UprMethodRule
        from .serializers import UprMethodPolicySerializer

        current = self._get(request, pk)
        ser = UprMethodPolicySerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        rules = ser.validated_data.pop("rules", [])

        next_version = (
            UprMethodPolicy.objects.filter(
                organization=current.organization, name=current.name
            ).order_by("-version").first().version + 1
        )
        # Deactivate first — the partial unique constraint allows one active per name.
        UprMethodPolicy.objects.filter(
            organization=current.organization, name=current.name, is_active=True
        ).update(is_active=False)
        new = UprMethodPolicy.objects.create(
            organization=current.organization,
            name=current.name,
            description=ser.validated_data.get("description", current.description),
            note=ser.validated_data.get("note", ""),
            version=next_version,
            is_active=True,
            created_by=request.user,
        )
        UprMethodRule.objects.bulk_create(
            [UprMethodRule(policy=new, order=i, **r) for i, r in enumerate(rules)]
        )
        return Response(UprMethodPolicySerializer(new).data)

    def delete(self, request, pk):
        obj = self._get(request, pk)
        obj.is_active = False
        obj.save(update_fields=["is_active"])
        return Response(status=status.HTTP_204_NO_CONTENT)


class UprMethodCatalogView(APIView):
    """GET /api/upr-methods/ — the method registry, so the UI never hard-codes it."""

    def get_permissions(self):
        return _upr_permissions("GET")

    def get(self, request):
        from module1_engine.upr_methods import MATCH_MODES, METHODS

        return Response({
            "methods": [
                {
                    "key": m.key,
                    "label": m.label,
                    "description": m.description,
                    "selfGatesOnExpiry": m.self_gates_on_expiry,
                    "needsGuard": not m.self_gates_on_expiry,
                    "params": list(m.params_schema),
                }
                for m in METHODS.values()
            ],
            "matchModes": list(MATCH_MODES),
        })


# ---------------------------------------------------------------------------
# Reserving-class aliases (WP0)
# ---------------------------------------------------------------------------


def _alias_permissions(method):
    """Reading an alias is part of running a job; changing one is an org-admin act.

    An alias decides which claims enter a reserve, so writing it is gated behind
    `class_alias.manage` — held by Actuary and Org Admin — rather than by `module1.run`,
    which every Analyst has.
    """
    if method in ("GET", "HEAD", "OPTIONS"):
        return [IsAuthenticated(), HasOrgPermission(["class_alias.view", "class_alias.manage"])]
    return [IsAuthenticated(), HasOrgPermission(["class_alias.manage"])]


class ReservingClassAliasListCreateView(APIView):
    """GET/POST /api/class-aliases/ — the org's reserving-class alias map."""

    def get_permissions(self):
        return _alias_permissions(self.request.method)

    def _org(self, request):
        org = get_request_org(request)
        if org is None:
            raise PermissionDenied("Select an organization first.")
        return org

    def get(self, request):
        from .models import ReservingClassAlias
        from .serializers import ReservingClassAliasSerializer

        qs = ReservingClassAlias.objects.filter(organization=self._org(request))
        return Response({"results": ReservingClassAliasSerializer(qs, many=True).data})

    def post(self, request):
        from .models import ReservingClassAlias
        from .serializers import ReservingClassAliasSerializer

        org = self._org(request)
        ser = ReservingClassAliasSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        alias = ReservingClassAlias(
            organization=org,
            alias=ser.validated_data["alias"],
            canonical=ser.validated_data["canonical"],
            note=ser.validated_data.get("note", ""),
            created_by=request.user if request.user.is_authenticated else None,
        )
        try:
            alias.full_clean(exclude=["alias_key"])
        except DjangoValidationError as exc:
            raise ValidationError({"detail": exc.messages}) from exc
        try:
            alias.save()
        except IntegrityError as exc:
            raise ValidationError(
                {"alias": f"'{alias.alias}' already has an alias in this organization."}
            ) from exc
        return Response(
            ReservingClassAliasSerializer(alias).data, status=status.HTTP_201_CREATED
        )


class ReservingClassAliasDetailView(APIView):
    """DELETE /api/class-aliases/<id>/ — remove one alias."""

    def get_permissions(self):
        return _alias_permissions(self.request.method)

    def delete(self, request, alias_id):
        from .models import ReservingClassAlias

        org = get_request_org(request)
        if org is None:
            raise PermissionDenied("Select an organization first.")
        alias = get_object_or_404(ReservingClassAlias, pk=alias_id, organization=org)
        alias.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
