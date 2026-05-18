"""RBAC permission helpers and DRF permission class.

In the multi-tenant model, roles are scoped to an organization via
tenants.Membership. These helpers resolve the user's active organization
from the request (via X-Organization-Id header or UserProfile.active_organization)
and look up roles/permissions within that org.

Global Super Admin = user.is_superuser. It bypasses all org scoping.
"""

from rest_framework.permissions import BasePermission

from tenants.permissions import (
    get_request_org,
    user_has_any_permission_in_org,
    user_has_role_in_org,
    user_permission_keys_in_org,
)


def user_has_role(user, role_name, *, org=None, request=None):
    """Backwards-compatible wrapper.

    Resolution order for the org context:
      1. explicit `org` kwarg
      2. `request` kwarg -> get_request_org(request)
      3. user.profile.active_organization (lookup falls back via get_request_org if request provided)
    """
    if not user or not user.is_authenticated:
        return False
    if role_name == "Super Admin":
        return bool(user.is_superuser)
    if org is None and request is not None:
        org = get_request_org(request)
    if org is None:
        profile = getattr(user, "profile", None)
        org = profile.active_organization if profile else None
    if org is None:
        return False
    return user_has_role_in_org(user, role_name, org)


def user_has_any_permission(user, keys, *, org=None, request=None):
    if not user or not user.is_authenticated:
        return False
    if not keys:
        return True
    if user.is_superuser:
        return True
    if org is None and request is not None:
        org = get_request_org(request)
    if org is None:
        profile = getattr(user, "profile", None)
        org = profile.active_organization if profile else None
    if org is None:
        return False
    return user_has_any_permission_in_org(user, keys, org)


def get_user_permission_keys(user, *, org=None, request=None):
    if not user or not user.is_authenticated:
        return set()
    if user.is_superuser:
        from .models import Permission
        return set(Permission.objects.values_list("key", flat=True))
    if org is None and request is not None:
        org = get_request_org(request)
    if org is None:
        profile = getattr(user, "profile", None)
        org = profile.active_organization if profile else None
    if org is None:
        return set()
    return user_permission_keys_in_org(user, org)


class HasPermission(BasePermission):
    """DRF permission that checks if the user has any of the required keys
    inside their active organization. Superuser bypasses."""

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
        return f"HasPermission({self.required_keys})"
