from django.contrib.auth import get_user_model
from rest_framework import serializers

from .models import Permission, Role, UserProfile

User = get_user_model()


class PermissionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Permission
        fields = ["id", "name", "key", "module", "description"]


class RoleSerializer(serializers.ModelSerializer):
    permissionIds = serializers.SerializerMethodField()
    createdAt = serializers.DateTimeField(source="created_at", read_only=True)

    class Meta:
        model = Role
        fields = ["id", "name", "description", "permissionIds", "createdAt"]

    def get_permissionIds(self, obj):
        return list(obj.permissions.values_list("id", flat=True))

    def create(self, validated_data):
        permission_ids = self.initial_data.get("permissionIds", [])
        role = Role.objects.create(
            name=validated_data["name"],
            description=validated_data.get("description", ""),
        )
        if permission_ids:
            role.permissions.set(Permission.objects.filter(id__in=permission_ids))
        return role

    def update(self, instance, validated_data):
        instance.name = validated_data.get("name", instance.name)
        instance.description = validated_data.get("description", instance.description)
        instance.save()
        if "permissionIds" in self.initial_data:
            permission_ids = self.initial_data["permissionIds"]
            instance.permissions.set(Permission.objects.filter(id__in=permission_ids))
        return instance


class UserListSerializer(serializers.ModelSerializer):
    """RBAC user representation for list/detail (matches dashboard shape)."""
    name = serializers.SerializerMethodField()
    status = serializers.SerializerMethodField()
    roleIds = serializers.SerializerMethodField()
    createdAt = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ["id", "name", "email", "status", "roleIds", "createdAt"]

    def get_name(self, obj):
        parts = [obj.first_name, obj.last_name]
        return " ".join(p for p in parts if p).strip() or obj.username

    def get_status(self, obj):
        try:
            return obj.profile.status
        except UserProfile.DoesNotExist:
            return "active" if obj.is_active else "inactive"

    def get_roleIds(self, obj):
        try:
            return list(obj.profile.roles.values_list("id", flat=True))
        except UserProfile.DoesNotExist:
            return []

    def get_createdAt(self, obj):
        try:
            return obj.profile.created_at.isoformat()
        except (UserProfile.DoesNotExist, AttributeError):
            return obj.date_joined.isoformat()


class UserCreateUpdateSerializer(serializers.Serializer):
    """For create/update user (accepts name, email, password, status, roleIds)."""
    name = serializers.CharField(max_length=255, required=True)
    email = serializers.EmailField(required=True)
    password = serializers.CharField(write_only=True, required=False, min_length=8)
    status = serializers.ChoiceField(choices=["active", "inactive"], default="active")
    roleIds = serializers.ListField(child=serializers.IntegerField(), required=False, default=list)

    def create(self, validated_data):
        name = validated_data["name"]
        email = validated_data["email"]
        password = validated_data.get("password", "")
        status = validated_data.get("status", "active")
        role_ids = validated_data.get("roleIds", [])

        parts = name.strip().split(None, 1)
        first_name = parts[0] if parts else ""
        last_name = parts[1] if len(parts) > 1 else ""

        user = User.objects.create_user(
            username=email,
            email=email,
            password=password or User.objects.make_random_password(),
            first_name=first_name,
            last_name=last_name,
            is_active=(status == "active"),
        )
        profile = user.profile
        profile.status = status
        profile.save()
        if role_ids:
            profile.roles.set(Role.objects.filter(id__in=role_ids))
        return user

    def update(self, instance, validated_data):
        name = validated_data.get("name")
        if name is not None:
            parts = name.strip().split(None, 1)
            instance.first_name = parts[0] if parts else ""
            instance.last_name = parts[1] if len(parts) > 1 else ""
        if "email" in validated_data:
            instance.email = validated_data["email"]
            instance.username = validated_data["email"]
        if "password" in validated_data and validated_data["password"]:
            instance.set_password(validated_data["password"])
        status = validated_data.get("status")
        if status is not None:
            instance.is_active = status == "active"
            try:
                instance.profile.status = status
                instance.profile.save()
            except UserProfile.DoesNotExist:
                pass
        instance.save()
        if "roleIds" in validated_data:
            try:
                instance.profile.roles.set(Role.objects.filter(id__in=validated_data["roleIds"]))
            except UserProfile.DoesNotExist:
                pass
        return instance


class PermissionCreateUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Permission
        fields = ["id", "name", "key", "module", "description"]
