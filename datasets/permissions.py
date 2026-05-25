"""DRF permission classes specific to Datasets.

These layer on top of `accounts.permissions.HasPermission` to encode
dataset-specific invariants: edits are forbidden on locked datasets,
object-level access is org-scoped, etc.
"""

from rest_framework.permissions import BasePermission

from tenants.permissions import get_request_org


class DatasetObjectPermission(BasePermission):
    """Object-level: the dataset must belong to the caller's active org.

    Superusers bypass. Used in detail / mutation endpoints to prevent
    cross-tenant access via id guessing.
    """

    def has_object_permission(self, request, view, obj):
        if request.user.is_superuser:
            return True
        org = get_request_org(request)
        if org is None:
            return False
        return obj.organization_id == org.id


class IsDatasetEditable(BasePermission):
    """Reject mutating requests on locked datasets.

    Read-shaped methods (GET/HEAD/OPTIONS) always pass; mutating methods
    pass only if the dataset is still in DRAFT status.
    """

    message = "This dataset is locked and cannot be modified."

    SAFE_METHODS = ("GET", "HEAD", "OPTIONS")

    def has_object_permission(self, request, view, obj):
        if request.method in self.SAFE_METHODS:
            return True
        return not obj.is_locked
