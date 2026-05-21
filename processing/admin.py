from django.contrib import admin, messages
from django.urls import reverse
from django.utils.html import format_html

from .models import Module1Job
from .services.retention import cascade_purge


@admin.register(Module1Job)
class Module1JobAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "job_type",
        "status",
        "user",
        "organization",
        "created_at",
        "completed_at",
        "retention_until",
        "legal_hold",
        "output_purged_at",
        "source_link",
    )
    list_filter = ("job_type", "status", "legal_hold", "output_purged_at")
    readonly_fields = (
        "id",
        "created_at",
        "started_at",
        "completed_at",
        "output_purged_at",
        "source_link",
        "descendants_link",
        "output_artifacts",
    )
    search_fields = ("user__email", "id", "source_job__id")
    actions = (
        "action_set_legal_hold",
        "action_clear_legal_hold",
        "action_cascade_purge",
    )

    fieldsets = (
        (None, {
            "fields": (
                "id", "job_type", "status",
                "user", "organization",
                "error_message",
            ),
        }),
        ("Timing", {
            "fields": ("created_at", "started_at", "completed_at"),
        }),
        ("Output", {
            "fields": (
                "output_zip", "output_artifacts",
                "retention_until", "legal_hold", "output_purged_at",
            ),
        }),
        ("Lineage", {
            "fields": ("source_job", "source_artifact", "source_link", "descendants_link"),
        }),
        ("Internals", {
            "fields": ("work_dir", "input_meta"),
            "classes": ("collapse",),
        }),
    )

    @admin.display(description="Source job")
    def source_link(self, obj: Module1Job):
        if not obj.source_job_id:
            return "—"
        url = reverse("admin:processing_module1job_change", args=[obj.source_job_id])
        return format_html('<a href="{}">{} ({})</a>', url, obj.source_job_id, obj.source_artifact or "")

    @admin.display(description="Descendants")
    def descendants_link(self, obj: Module1Job):
        if not obj.pk:
            return "—"
        children = list(obj.derived_jobs.all()[:25])
        if not children:
            return "None"
        links = []
        for child in children:
            url = reverse("admin:processing_module1job_change", args=[child.id])
            links.append(format_html('<a href="{}">{} ({})</a>', url, child.id, child.job_type))
        more = obj.derived_jobs.count() - len(children)
        if more > 0:
            links.append(format_html("<em>+ {} more</em>", more))
        return format_html("<br>".join(["{}"] * len(links)), *links)

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    @admin.action(description="Set legal hold (skip retention purge)")
    def action_set_legal_hold(self, request, queryset):
        n = queryset.update(legal_hold=True)
        self.message_user(
            request,
            f"Legal hold set on {n} job(s).",
            level=messages.SUCCESS,
        )

    @admin.action(description="Clear legal hold")
    def action_clear_legal_hold(self, request, queryset):
        n = queryset.update(legal_hold=False)
        self.message_user(
            request,
            f"Legal hold cleared on {n} job(s).",
            level=messages.SUCCESS,
        )

    @admin.action(description="Force purge job(s) and all descendants")
    def action_cascade_purge(self, request, queryset):
        total_rows = 0
        total_outputs = 0
        for root in queryset:
            try:
                result = cascade_purge(root, actor=request.user)
                total_rows += result["deleted_rows"]
                total_outputs += result["purged_outputs"]
            except PermissionError as exc:
                self.message_user(request, str(exc), level=messages.ERROR)
                return
            except Exception as exc:
                self.message_user(
                    request,
                    f"Cascade purge failed for {root.id}: {exc}",
                    level=messages.ERROR,
                )
                return
        self.message_user(
            request,
            f"Cascade purge complete: {total_rows} row(s) deleted, "
            f"{total_outputs} output file(s) removed.",
            level=messages.SUCCESS,
        )
