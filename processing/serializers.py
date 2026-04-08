from rest_framework import serializers

from .models import Module1Job


class Module1JobSerializer(serializers.ModelSerializer):
    duration_seconds = serializers.SerializerMethodField()
    output_ready = serializers.SerializerMethodField()

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
        )
        read_only_fields = fields

    def get_duration_seconds(self, obj: Module1Job):
        if obj.started_at and obj.completed_at:
            return (obj.completed_at - obj.started_at).total_seconds()
        return None

    def get_output_ready(self, obj: Module1Job) -> bool:
        return obj.status == Module1Job.Status.SUCCESS and bool(obj.output_zip)


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
