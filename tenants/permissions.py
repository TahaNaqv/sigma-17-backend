"""Tenant-scoped permission helpers.

Resolution order for "active org" on a request:
  1. X-Organization-Id header (must match an active membership)
  2. user.profile.active_organization (must still have an active membership)

`get_request_org(request)` returns the resolved Organization or None.
`set_request_org` caches it on the request object.

Global Super Admin is `user.is_superuser` — it bypasses tenant scoping entirely.
"""

from rest_framework.permissions import BasePermission

from .models import Membership, Organization


_REQUEST_ORG_ATTR = "_resolved_organization"


def get_request_org(request):
    if hasattr(request, _REQUEST_ORG_ATTR):
        return getattr(request, _REQUEST_ORG_ATTR)
    org = _resolve_request_org(request)
    setattr(request, _REQUEST_ORG_ATTR, org)
    return org


def _resolve_request_org(request):
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return None
    is_super = bool(user.is_superuser)
    header_id = request.META.get("HTTP_X_ORGANIZATION_ID")
    if header_id:
        org_uuid = str(header_id).strip()
        if is_super:
            try:
                return Organization.objects.filter(
                    id=org_uuid, is_active=True
                ).first()
            except (ValueError, Organization.DoesNotExist):
                return None
        try:
            membership = (
                Membership.objects
                .select_related("organization")
                .filter(
                    user=user,
                    organization_id=org_uuid,
                    status="active",
                    organization__is_active=True,
                )
                .first()
            )
        except (ValueError, Organization.DoesNotExist):
            return None
        return membership.organization if membership else None
    profile = getattr(user, "profile", None)
    if not profile or not profile.active_organization_id:
        return None
    if is_super:
        # Superusers don't need a membership to operate in an org.
        org = profile.active_organization
        return org if org and org.is_active else None
    has_active = Membership.objects.filter(
        user=user,
        organization_id=profile.active_organization_id,
        status="active",
        organization__is_active=True,
    ).exists()
    return profile.active_organization if has_active else None


def user_roles_in_org(user, org):
    if not user or not user.is_authenticated or org is None:
        return []
    membership = (
        Membership.objects
        .filter(user=user, organization=org, status="active")
        .prefetch_related("roles")
        .first()
    )
    if not membership:
        return []
    return list(membership.roles.all())


def user_permission_keys_in_org(user, org):
    if user and user.is_superuser:
        from accounts.models import Permission
        return set(Permission.objects.values_list("key", flat=True))
    roles = user_roles_in_org(user, org)
    keys = set()
    for role in roles:
        keys.update(role.permissions.values_list("key", flat=True))
    return keys


def user_has_role_in_org(user, role_name, org):
    if role_name == "Super Admin":
        return bool(user and user.is_superuser)
    if not user or not user.is_authenticated or org is None:
        return False
    return Membership.objects.filter(
        user=user,
        organization=org,
        status="active",
        roles__name=role_name,
    ).exists()


def user_has_any_permission_in_org(user, keys, org):
    if not keys:
        return True
    if user and user.is_superuser:
        return True
    return bool(set(keys) & user_permission_keys_in_org(user, org))


class IsInOrgContext(BasePermission):
    """Authenticated user with a resolved active organization (or superuser)."""

    message = "An active organization context is required."

    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated:
            return False
        if user.is_superuser:
            # Superuser may still need an org for tenant-scoped data; we don't
            # block here, but views should call get_request_org() and act
            # accordingly.
            return True
        return get_request_org(request) is not None


class HasOrgPermission(BasePermission):
    """Org-scoped permission check. Superuser bypasses."""

    def __init__(self, required_keys):
        if isinstance(required_keys, str):
            required_keys = [required_keys]
        self.required_keys = list(required_keys)

    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated:
            return False
        if user.is_superuser:
            return True
        org = get_request_org(request)
        if org is None:
            return False
        return user_has_any_permission_in_org(user, self.required_keys, org)

    def __repr__(self):
        return f"HasOrgPermission({self.required_keys})"
