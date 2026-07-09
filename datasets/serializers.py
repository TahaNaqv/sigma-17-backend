"""DRF serializers for Dataset metadata and per-kind row tables.

Row schemas differ per `Dataset.Kind`. `serializer_for_kind()` is the
single dispatch point used by the views, so every endpoint that touches
rows looks up the right serializer here instead of branching on kind.
"""

from rest_framework import serializers

from .models import (
    ROW_MODEL_FOR_KIND,
    ClaimsOSRow,
    ClaimsPaidRow,
    Dataset,
    DatasetSnapshot,
    ExpenseCfRow,
    MovementOverrideRow,
    PremiumRow,
    PreviousPeriodLicRow,
    PreviousPeriodUprRow,
)


# ---------------------------------------------------------------------------
# Dataset metadata
# ---------------------------------------------------------------------------


class DatasetSerializer(serializers.ModelSerializer):
    """Read serializer for Dataset metadata. Used by list and retrieve."""

    is_locked = serializers.BooleanField(read_only=True)
    created_by_email = serializers.SerializerMethodField()
    locked_by_email = serializers.SerializerMethodField()

    class Meta:
        model = Dataset
        fields = (
            "id",
            "organization",
            "kind",
            "name",
            "description",
            "status",
            "source",
            "row_count",
            "is_locked",
            "created_by",
            "created_by_email",
            "created_at",
            "updated_at",
            "locked_at",
            "locked_by",
            "locked_by_email",
            "forked_from",
        )
        read_only_fields = (
            "id",
            "organization",
            "status",
            "row_count",
            "is_locked",
            "created_by",
            "created_by_email",
            "created_at",
            "updated_at",
            "locked_at",
            "locked_by",
            "locked_by_email",
            "forked_from",
        )

    def get_created_by_email(self, obj):
        return obj.created_by.email if obj.created_by_id else None

    def get_locked_by_email(self, obj):
        return obj.locked_by.email if obj.locked_by_id else None


class DatasetCreateSerializer(serializers.ModelSerializer):
    """Write serializer used by POST /api/datasets/."""

    class Meta:
        model = Dataset
        fields = ("kind", "name", "description")

    def validate_name(self, value):
        value = (value or "").strip()
        if not value:
            raise serializers.ValidationError("Name is required.")
        return value


class DatasetUpdateSerializer(serializers.ModelSerializer):
    """PATCH /api/datasets/{id}/ — only metadata, never rows."""

    class Meta:
        model = Dataset
        fields = ("name", "description")

    def validate_name(self, value):
        value = (value or "").strip()
        if not value:
            raise serializers.ValidationError("Name is required.")
        return value


# ---------------------------------------------------------------------------
# Per-kind row serializers — column sets mirror engine.py's expected schemas.
# ---------------------------------------------------------------------------


class _RowBaseSerializer(serializers.ModelSerializer):
    """Base for row serializers. `dataset` is set by the view, not the client."""

    class Meta:
        # Subclasses set their own `model` and add columns; `id` and
        # `row_index` are universal.
        fields = ("id", "row_index")
        read_only_fields = ("id",)


class PremiumRowSerializer(_RowBaseSerializer):
    class Meta(_RowBaseSerializer.Meta):
        model = PremiumRow
        fields = _RowBaseSerializer.Meta.fields + (
            "policy_number",
            "policy_start_date",
            "policy_end_date",
            "risk_start_date",
            "risk_end_date",
            "issue_date",
            "reserving_class",
            "policy_class",
            "product_type",
            "ri_treaty_type",
            "premium_amount",
            "commission_amount",
        )


class ClaimsPaidRowSerializer(_RowBaseSerializer):
    class Meta(_RowBaseSerializer.Meta):
        model = ClaimsPaidRow
        fields = _RowBaseSerializer.Meta.fields + (
            "amount_paid",
            "amount_recovered",
            "issue_date",
            "loss_date",
            "payment_date",
            "reserving_class",
            "policy_class",
            "ri_treaty_type",
            "head_of_damage",
        )


class ClaimsOSRowSerializer(_RowBaseSerializer):
    class Meta(_RowBaseSerializer.Meta):
        model = ClaimsOSRow
        fields = _RowBaseSerializer.Meta.fields + (
            "amount_outstanding",
            "issue_date",
            "loss_date",
            "as_at",
            "reserving_class",
            "policy_class",
            "ri_treaty_type",
            "head_of_damage",
        )


class ExpenseCfRowSerializer(_RowBaseSerializer):
    class Meta(_RowBaseSerializer.Meta):
        model = ExpenseCfRow
        fields = _RowBaseSerializer.Meta.fields + (
            "reserving_class",
            "uwy",
            "comm_payable_prev",
            "comm_payable_curr",
            "rec_gop_prev",
            "rec_gop_curr",
            "rec_provision_prev",
            "rec_provision_curr",
            "premium_received",
            "claims_paid",
            "insurance_acquisition_cash_flows",
            "other_cash_flows",
            "ri_premium_paid",
            "ri_claims_received",
            "ri_fixed_commission_received",
            "directly_attributable_expenses",
            "other_acquisition_cash_flows",
            "ri_rec_gop_prev",
            "ri_rec_gop_curr",
            "claim_pay_prev",
            "claim_pay_curr",
            "ri_payable_prev",
            "ri_payable_curr",
            "ri_rec_provision_prev",
            "ri_rec_provision_curr",
        )


class PreviousPeriodLicRowSerializer(_RowBaseSerializer):
    class Meta(_RowBaseSerializer.Meta):
        model = PreviousPeriodLicRow
        fields = _RowBaseSerializer.Meta.fields + (
            "reserving_class",
            "uwy",
            "accident_period",
            "gross_ri",
            "outstanding",
            "ss",
            "payment",
            "s_and_s",
            "ulae",
            "ra_os",
            "ra_ibnr",
            "discounting_impact",
        )


class PreviousPeriodUprRowSerializer(_RowBaseSerializer):
    class Meta(_RowBaseSerializer.Meta):
        model = PreviousPeriodUprRow
        fields = _RowBaseSerializer.Meta.fields + (
            "reserving_class",
            "uwy",
            "gross_upr",
            "dac",
            "ri_upr",
            "ucr",
        )


class MovementOverrideRowSerializer(_RowBaseSerializer):
    class Meta(_RowBaseSerializer.Meta):
        model = MovementOverrideRow
        fields = _RowBaseSerializer.Meta.fields + (
            "reserving_class",
            "uwy",
            "ri_loss_recovery_new_onerous",
            "ri_loss_recovery_reversal_amortization",
            "ri_loss_recovery_assumption_change",
            "ri_provision_nonperformance_change",
            "ri_finance_pnl",
            "ri_pdr_accrual_reserve_bop",
            "ri_methodology_diff_loss_recovery_bop",
            "ri_accrual_reserve_specify",
        )


ROW_SERIALIZER_FOR_KIND = {
    Dataset.Kind.PREMIUM: PremiumRowSerializer,
    Dataset.Kind.CLAIMS_PAID: ClaimsPaidRowSerializer,
    Dataset.Kind.CLAIMS_OS: ClaimsOSRowSerializer,
    Dataset.Kind.EXPENSE_CF: ExpenseCfRowSerializer,
    Dataset.Kind.PREVIOUS_PERIOD_LIC: PreviousPeriodLicRowSerializer,
    Dataset.Kind.PREVIOUS_PERIOD_UPR: PreviousPeriodUprRowSerializer,
    Dataset.Kind.MOVEMENT_OVERRIDE: MovementOverrideRowSerializer,
}


def serializer_for_kind(kind: str):
    """Return the row serializer class for a `Dataset.Kind`, or raise."""
    try:
        return ROW_SERIALIZER_FOR_KIND[kind]
    except KeyError:
        raise serializers.ValidationError(
            {"kind": f"No row serializer registered for kind '{kind}'."}
        )


def model_for_kind(kind: str):
    """Return the row model class for a `Dataset.Kind`, or raise."""
    try:
        return ROW_MODEL_FOR_KIND[kind]
    except KeyError:
        raise serializers.ValidationError(
            {"kind": f"No row model registered for kind '{kind}'."}
        )


# ---------------------------------------------------------------------------
# Bulk operations
# ---------------------------------------------------------------------------


class BulkRowsInputSerializer(serializers.Serializer):
    """Wrapper for POST /api/datasets/{id}/rows/bulk/ — a list of row dicts."""

    rows = serializers.ListField(
        child=serializers.DictField(),
        allow_empty=False,
        max_length=10000,  # one paste batch ceiling; clients can chunk if needed
    )


class BulkDeleteInputSerializer(serializers.Serializer):
    """DELETE-by-payload: either explicit ids or `all=true`."""

    row_ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1),
        required=False,
        allow_empty=False,
    )
    all = serializers.BooleanField(required=False, default=False)

    def validate(self, attrs):
        if not attrs.get("row_ids") and not attrs.get("all"):
            raise serializers.ValidationError(
                "Provide either `row_ids` or `all=true`."
            )
        if attrs.get("row_ids") and attrs.get("all"):
            raise serializers.ValidationError(
                "Provide `row_ids` or `all=true`, not both."
            )
        return attrs


# ---------------------------------------------------------------------------
# Snapshots (read-only — created server-side when a job runs)
# ---------------------------------------------------------------------------


class DatasetSnapshotSerializer(serializers.ModelSerializer):
    class Meta:
        model = DatasetSnapshot
        fields = (
            "id",
            "dataset",
            "consumer_job",
            "kind",
            "row_count",
            "created_at",
        )
        read_only_fields = fields
