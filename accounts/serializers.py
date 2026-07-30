from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils.crypto import get_random_string
from rest_framework import serializers

from tenants.models import Membership

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


def _role_ids_in_org(user, org):
    if org is None:
        return []
    m = Membership.objects.filter(user=user, organization=org).first()
    if not m:
        return []
    return list(m.roles.values_list("id", flat=True))


class UserListSerializer(serializers.ModelSerializer):
    """User representation, with roleIds scoped to the requesting org's membership."""
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
        org = (self.context or {}).get("organization")
        return _role_ids_in_org(obj, org)

    def get_createdAt(self, obj):
        try:
            return obj.profile.created_at.isoformat()
        except (UserProfile.DoesNotExist, AttributeError):
            return obj.date_joined.isoformat()


class UserCreateUpdateSerializer(serializers.Serializer):
    """Create/update a user.

    On create: creates the user AND creates a Membership in the requesting
    org with the given roleIds. On update: updates user fields and (if
    roleIds is provided) updates the membership in the requesting org.

    NOTE: This serializer requires an `organization` in serializer context.
    """
    name = serializers.CharField(max_length=255, required=True)
    email = serializers.EmailField(required=True)
    password = serializers.CharField(write_only=True, required=False, min_length=8)
    status = serializers.ChoiceField(choices=["active", "inactive"], default="active")
    roleIds = serializers.ListField(
        child=serializers.IntegerField(), required=False, default=list
    )

    def _get_org(self):
        return (self.context or {}).get("organization")

    def validate_email(self, value):
        """Reject duplicates here so the client gets a field error, not a 409.

        Email doubles as username, which is unique at the DB level; without
        this check the insert fails with an IntegrityError.
        """
        email = value.strip()
        clash = User.objects.filter(email__iexact=email) | User.objects.filter(
            username__iexact=email
        )
        if self.instance is not None:
            clash = clash.exclude(pk=self.instance.pk)
        if clash.exists():
            raise serializers.ValidationError(
                "A user with this email already exists."
            )
        return email

    @transaction.atomic
    def create(self, validated_data):
        org = self._get_org()
        if org is None:
            raise serializers.ValidationError(
                {"detail": "An active organization is required to create users."}
            )
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
            password=password or get_random_string(16),
            first_name=first_name,
            last_name=last_name,
            is_active=(status == "active"),
        )
        profile = user.profile
        profile.status = status
        if not profile.active_organization_id:
            profile.active_organization = org
        profile.save()

        membership = Membership.objects.create(
            user=user, organization=org, status="active"
        )
        if role_ids:
            membership.roles.set(Role.objects.filter(id__in=role_ids))
        return user

    @transaction.atomic
    def update(self, instance, validated_data):
        org = self._get_org()
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

        if "roleIds" in validated_data and org is not None:
            membership, _ = Membership.objects.get_or_create(
                user=instance, organization=org, defaults={"status": "active"}
            )
            membership.roles.set(
                Role.objects.filter(id__in=validated_data["roleIds"])
            )
        return instance


class PermissionCreateUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Permission
        fields = ["id", "name", "key", "module", "description"]


class ProfileSerializer(serializers.Serializer):
    """Read/write profile (name, email) for current user."""
    name = serializers.CharField(max_length=255, required=False)
    email = serializers.EmailField(required=False)

    def to_representation(self, instance):
        parts = [instance.first_name, instance.last_name]
        name = " ".join(p for p in parts if p).strip() or instance.username
        return {"name": name, "email": instance.email}

    def update(self, instance, validated_data):
        name = validated_data.get("name")
        if name is not None:
            parts = name.strip().split(None, 1)
            instance.first_name = parts[0] if parts else ""
            instance.last_name = parts[1] if len(parts) > 1 else ""
        if "email" in validated_data:
            instance.email = validated_data["email"]
            instance.username = validated_data["email"]
        instance.save()
        return instance


class ChangePasswordSerializer(serializers.Serializer):
    currentPassword = serializers.CharField(write_only=True)
    newPassword = serializers.CharField(write_only=True, min_length=8)

    def validate_currentPassword(self, value):
        user = self.context["request"].user
        if not user.check_password(value):
            raise serializers.ValidationError("Current password is incorrect.")
        return value
