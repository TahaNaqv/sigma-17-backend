from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet

from .models import Permission, Role, UserProfile
from .permissions import HasPermission, get_user_permission_keys
from .serializers import (
    PermissionCreateUpdateSerializer,
    PermissionSerializer,
    RoleSerializer,
    UserCreateUpdateSerializer,
    UserListSerializer,
)

User = get_user_model()


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def auth_me(request):
    """Return current user with roles and permission keys."""
    user = request.user
    try:
        profile = user.profile
        role_ids = list(profile.roles.values_list("id", flat=True))
        user_status = profile.status
    except UserProfile.DoesNotExist:
        role_ids = []
        user_status = "active" if user.is_active else "inactive"

    parts = [user.first_name, user.last_name]
    name = " ".join(p for p in parts if p).strip() or user.username

    return Response({
        "user": {
            "id": user.id,
            "name": name,
            "email": user.email,
            "status": user_status,
            "roleIds": role_ids,
            "permissionKeys": list(get_user_permission_keys(user)),
        },
    })


class UserViewSet(ModelViewSet):
    queryset = User.objects.all().order_by("id")
    permission_classes = [IsAuthenticated]
    lookup_url_kwarg = "id"

    def get_serializer_class(self):
        if self.action in ("create", "update", "partial_update"):
            return UserCreateUpdateSerializer
        return UserListSerializer

    def get_permissions(self):
        base = [IsAuthenticated]
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
        serializer = UserCreateUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        return Response(UserListSerializer(user).data, status=status.HTTP_201_CREATED)

    def update(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = UserCreateUpdateSerializer(instance, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(UserListSerializer(instance).data)

    def perform_destroy(self, instance):
        instance.is_active = False
        instance.save()


class RoleViewSet(ModelViewSet):
    queryset = Role.objects.all().order_by("id")
    serializer_class = RoleSerializer
    permission_classes = [IsAuthenticated]
    lookup_url_kwarg = "id"

    def get_permissions(self):
        base = [IsAuthenticated]
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
    permission_classes = [IsAuthenticated, HasPermission(["permissions.manage"])]
    lookup_url_kwarg = "id"

    def get_serializer_class(self):
        if self.action in ("create", "update", "partial_update"):
            return PermissionCreateUpdateSerializer
        return PermissionSerializer
