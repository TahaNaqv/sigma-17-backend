from rest_framework import serializers

from .models import Module1Job


class _JobSourceSerializer(serializers.Serializer):
    """Read-only lineage snapshot embedded in Module1JobSerializer."""

    job_id = serializers.UUIDField()
    job_type = serializers.CharField()
    artifact = serializers.CharField()
    job_completed_at = serializers.DateTimeField(allow_null=True)


class Module1JobSerializer(serializers.ModelSerializer):
    duration_seconds = serializers.SerializerMethodField()
    output_ready = serializers.SerializerMethodField()
    source = serializers.SerializerMethodField()

    class Meta:
        model = Module1Job
        fields = (
            "id",
            "job_type",
            "status",
            "error_message",
            "created_at",
            "started_at",
            "completed_at",
            "input_meta",
            "duration_seconds",
            "output_ready",
            "output_artifacts",
            "retention_until",
            "legal_hold",
            "output_purged_at",
            "source",
        )
        read_only_fields = fields

    def get_duration_seconds(self, obj: Module1Job):
        if obj.started_at and obj.completed_at:
            return (obj.completed_at - obj.started_at).total_seconds()
        return None

    def get_output_ready(self, obj: Module1Job) -> bool:
        return obj.output_available

    def get_source(self, obj: Module1Job):
        if not obj.source_job_id:
            return None
        src = obj.source_job
        return {
            "job_id": str(src.id),
            "job_type": src.job_type,
            "artifact": obj.source_artifact or "",
            "job_completed_at": src.completed_at,
        }


class SourceCandidateSerializer(serializers.Serializer):
    """Item shape returned by /api/processing/source-candidates/."""

    id = serializers.UUIDField()
    job_type = serializers.CharField()
    completed_at = serializers.DateTimeField()
    created_at = serializers.DateTimeField()
    output_artifacts = serializers.ListField(child=serializers.CharField())
    label = serializers.SerializerMethodField()

    def get_label(self, obj):
        # obj is a Module1Job instance here
        when = obj.completed_at or obj.created_at
        return f"{obj.get_job_type_display()} — {when.strftime('%Y-%m-%d %H:%M')}"


class OutputFileItemSerializer(serializers.Serializer):
    path = serializers.CharField()
    size = serializers.IntegerField(min_value=0)
    kind = serializers.ChoiceField(choices=["xlsx"])


class OutputFilesResponseSerializer(serializers.Serializer):
    job_id = serializers.UUIDField()
    files = OutputFileItemSerializer(many=True)


class OutputSheetMetaSerializer(serializers.Serializer):
    name = serializers.CharField()
    rows = serializers.IntegerField(min_value=0)
    columns = serializers.IntegerField(min_value=0)


class OutputSheetsResponseSerializer(serializers.Serializer):
    file = serializers.CharField()
    sheets = OutputSheetMetaSerializer(many=True)


class OutputSheetPageResponseSerializer(serializers.Serializer):
    file = serializers.CharField()
    sheet = serializers.CharField()
    page = serializers.IntegerField(min_value=1)
    page_size = serializers.IntegerField(min_value=1)
    total_rows = serializers.IntegerField(min_value=0)
    columns = serializers.ListField(child=serializers.CharField())
    rows = serializers.ListField(child=serializers.ListField())


class Module2UlrRowSerializer(serializers.Serializer):
    id = serializers.CharField()
    reserving_class = serializers.CharField()
    uwy = serializers.CharField()
    gwp = serializers.FloatField()
    upr = serializers.FloatField()
    gep = serializers.FloatField()
    paid_claims = serializers.FloatField()
    os = serializers.FloatField()
    ibnr = serializers.FloatField()
    incurred_claims = serializers.FloatField()
    ultimate_claims = serializers.FloatField()
    paid_lr = serializers.FloatField()
    inc_lr = serializers.FloatField()
    ult_lr = serializers.FloatField()
    commission_expense = serializers.FloatField()
    comm_ratio = serializers.FloatField()
    exp_ratio = serializers.FloatField()
    ri_percent = serializers.FloatField()
    ra_percent = serializers.FloatField()
    selected_ulr = serializers.FloatField(allow_null=True)
    combined_ratio = serializers.FloatField()


class Module2SelectedUlrInputSerializer(serializers.Serializer):
    reserving_class = serializers.CharField(max_length=255)
    uwy = serializers.CharField(max_length=32)
    selected_ulr = serializers.FloatField(min_value=0)


class Module2ProcessRequestSerializer(serializers.Serializer):
    allocate_job_id = serializers.UUIDField()
    accounting_period = serializers.IntegerField(min_value=1900, max_value=2200)
    selected_ulr = Module2SelectedUlrInputSerializer(
        many=True, required=False, default=list
    )
