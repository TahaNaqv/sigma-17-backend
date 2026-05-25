from django.contrib import admin

from .models import (
    ClaimsOSRow,
    ClaimsPaidRow,
    Dataset,
    DatasetSnapshot,
    PremiumRow,
)


@admin.register(Dataset)
class DatasetAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "kind",
        "organization",
        "status",
        "source",
        "row_count",
        "created_at",
    )
    list_filter = ("kind", "status", "source")
    search_fields = ("name", "description")
    readonly_fields = (
        "id",
        "row_count",
        "created_at",
        "updated_at",
        "locked_at",
        "forked_from",
    )


class _RowAdminBase(admin.ModelAdmin):
    list_display = ("id", "dataset", "row_index", "reserving_class", "ri_treaty_type")
    list_filter = ("ri_treaty_type",)
    search_fields = ("reserving_class",)
    raw_id_fields = ("dataset",)


@admin.register(PremiumRow)
class PremiumRowAdmin(_RowAdminBase):
    pass


@admin.register(ClaimsPaidRow)
class ClaimsPaidRowAdmin(_RowAdminBase):
    pass


@admin.register(ClaimsOSRow)
class ClaimsOSRowAdmin(_RowAdminBase):
    pass


@admin.register(DatasetSnapshot)
class DatasetSnapshotAdmin(admin.ModelAdmin):
    list_display = ("id", "dataset", "consumer_job", "kind", "row_count", "created_at")
    list_filter = ("kind",)
    readonly_fields = ("id", "created_at", "rows_payload")
    raw_id_fields = ("dataset", "consumer_job")
