from django.contrib.auth import get_user_model
from rest_framework import serializers

from accounts.models import Role

from .models import Membership, Organization

User = get_user_model()


class OrganizationSerializer(serializers.ModelSerializer):
    createdAt = serializers.DateTimeField(source="created_at", read_only=True)
    memberCount = serializers.SerializerMethodField()
    isActive = serializers.BooleanField(source="is_active", required=False)

    class Meta:
        model = Organization
        fields = [
            "id",
            "name",
            "slug",
            "description",
            "isActive",
            "createdAt",
            "memberCount",
        ]
        read_only_fields = ["id", "slug", "createdAt", "memberCount"]

    def get_memberCount(self, obj):
        return obj.memberships.filter(status="active").count()


class MembershipUserSerializer(serializers.ModelSerializer):
    name = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ["id", "name", "email"]

    def get_name(self, obj):
        parts = [obj.first_name, obj.last_name]
        return " ".join(p for p in parts if p).strip() or obj.username


class MembershipSerializer(serializers.ModelSerializer):
    user = MembershipUserSerializer(read_only=True)
    userId = serializers.IntegerField(write_only=True, required=False)
    organizationId = serializers.UUIDField(source="organization_id", read_only=True)
    roleIds = serializers.SerializerMethodField()
    createdAt = serializers.DateTimeField(source="created_at", read_only=True)

    class Meta:
        model = Membership
        fields = [
            "id",
            "user",
            "userId",
            "organizationId",
            "roleIds",
            "status",
            "createdAt",
        ]
        read_only_fields = ["id", "user", "organizationId", "createdAt"]

    def get_roleIds(self, obj):
        return list(obj.roles.values_list("id", flat=True))


class MembershipCreateSerializer(serializers.Serializer):
    userId = serializers.IntegerField()
    roleIds = serializers.ListField(
        child=serializers.IntegerField(), required=False, default=list
    )
    status = serializers.ChoiceField(
        choices=["active", "suspended"], default="active"
    )


class MembershipUpdateSerializer(serializers.Serializer):
    roleIds = serializers.ListField(
        child=serializers.IntegerField(), required=False
    )
    status = serializers.ChoiceField(
        choices=["active", "suspended"], required=False
    )


class SwitchOrgSerializer(serializers.Serializer):
    organizationId = serializers.UUIDField()
