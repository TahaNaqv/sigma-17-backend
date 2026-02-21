"""RBAC permission helpers and DRF permission class."""

from rest_framework.permissions import BasePermission


def user_has_role(user, role_name):
    """Check if user has the given role via their profile."""
    if not user or not user.is_authenticated:
        return False
    if not hasattr(user, "profile"):
        return False
    return user.profile.roles.filter(name=role_name).exists()


def user_has_any_permission(user, keys):
    """Check if user has any of the given permission keys via their roles."""
    if not user or not user.is_authenticated:
        return False
    if not keys:
        return True
    if not hasattr(user, "profile"):
        return False
    user_keys = set()
    for role in user.profile.roles.all():
        user_keys.update(role.permissions.values_list("key", flat=True))
    return bool(set(keys) & user_keys)


def get_user_permission_keys(user):
    """Return set of permission keys the user has via their roles."""
    if not user or not user.is_authenticated:
        return set()
    if not hasattr(user, "profile"):
        return set()
    keys = set()
    for role in user.profile.roles.all():
        keys.update(role.permissions.values_list("key", flat=True))
    return keys


class HasPermission(BasePermission):
    """DRF permission that checks if user has any of the required permission keys.
    Super Admin role bypasses all checks."""

    def __init__(self, required_keys):
        if isinstance(required_keys, str):
            required_keys = [required_keys]
        self.required_keys = list(required_keys)

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        if user_has_role(request.user, "Super Admin"):
            return True
        return user_has_any_permission(request.user, self.required_keys)

    def __repr__(self):
        return f"HasPermission({self.required_keys})"
