from django.contrib.auth import get_user_model
from rest_framework import serializers

from accounts.models import Role

from .models import Membership, Organization

User = get_user_model()


class OrganizationSerializer(serializers.ModelSerializer):
    createdAt = serializers.DateTimeField(source="created_at", read_only=True)
    memberCount = serializers.SerializerMethodField()
    isActive = serializers.BooleanField(source="is_active", required=False)
    defaultOutputRetentionDays = serializers.IntegerField(
        source="default_output_retention_days",
        required=False,
        allow_null=True,
        min_value=1,
    )

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
            "defaultOutputRetentionDays",
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


# ---------------------------------------------------------------------------
# Sensitivity scenario sets
# ---------------------------------------------------------------------------

from module2_engine.scenarios import LEVER_UNITS, LEVERS  # noqa: E402

from .models import Scenario, ScenarioSet  # noqa: E402


class ScenarioSerializer(serializers.ModelSerializer):
    scopeClasses = serializers.JSONField(source="scope_classes", required=False)
    unit = serializers.SerializerMethodField()
    description = serializers.SerializerMethodField()

    class Meta:
        model = Scenario
        fields = ["id", "label", "lever", "magnitude", "scopeClasses", "order",
                  "unit", "description"]

    def get_unit(self, obj) -> str:
        return LEVER_UNITS.get(obj.lever, "")

    def get_description(self, obj) -> str:
        from module2_engine.scenarios import ScenarioShock
        return ScenarioShock(
            label=obj.label, lever=obj.lever, magnitude=float(obj.magnitude)
        ).describe()

    def validate_lever(self, value):
        if value not in LEVERS:
            raise serializers.ValidationError(f"Unknown lever; expected one of {LEVERS}.")
        return value

    def validate(self, attrs):
        lever = attrs.get("lever", getattr(self.instance, "lever", None))
        mag = attrs.get("magnitude", getattr(self.instance, "magnitude", None))
        if lever is None or mag is None:
            return attrs
        mag = float(mag)
        # Guard-rails matched to each lever's unit. These are the values that make
        # a shock nonsense rather than merely severe.
        if lever == "ra" and mag <= -1.0:
            raise serializers.ValidationError(
                {"magnitude": "A relative RA shock of -100% or worse removes the loading entirely."}
            )
        if lever == "discount" and abs(mag) > 10_000:
            raise serializers.ValidationError(
                {"magnitude": "Discount shocks are in basis points; +/-10,000bp is a 100% move."}
            )
        if lever == "ulr" and abs(mag) > 5.0:
            raise serializers.ValidationError(
                {"magnitude": "ULR shocks are absolute fractions; 5.0 is +500 percentage points."}
            )
        return attrs


class ScenarioSetSerializer(serializers.ModelSerializer):
    scenarios = ScenarioSerializer(many=True, required=False)
    isActive = serializers.BooleanField(source="is_active", read_only=True)
    createdAt = serializers.DateTimeField(source="created_at", read_only=True)
    createdBy = serializers.CharField(source="created_by.email", read_only=True, default=None)

    class Meta:
        model = ScenarioSet
        fields = ["id", "name", "description", "version", "isActive",
                  "createdAt", "createdBy", "scenarios"]
        read_only_fields = ["version"]

    def create(self, validated):
        scenarios = validated.pop("scenarios", [])
        obj = ScenarioSet.objects.create(**validated)
        Scenario.objects.bulk_create([
            Scenario(scenario_set=obj, order=i, **s) for i, s in enumerate(scenarios)
        ])
        return obj
