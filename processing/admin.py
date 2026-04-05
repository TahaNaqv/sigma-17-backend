from django.contrib import admin

from .models import Module1Job


@admin.register(Module1Job)
class Module1JobAdmin(admin.ModelAdmin):
    list_display = ("id", "job_type", "status", "user", "created_at", "completed_at")
    list_filter = ("job_type", "status")
    readonly_fields = ("id", "created_at", "started_at", "completed_at")
    search_fields = ("user__email", "id")
