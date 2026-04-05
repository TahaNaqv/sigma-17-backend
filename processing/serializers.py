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
