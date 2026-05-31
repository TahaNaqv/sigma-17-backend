"""HTTP endpoints for Dataset CRUD and per-row operations.

Endpoint shape (mounted under /api/):
  GET    /datasets/                         list datasets in active org
  POST   /datasets/                         create new draft dataset
  GET    /datasets/{id}/                    retrieve dataset metadata
  PATCH  /datasets/{id}/                    update name/description (draft only)
  DELETE /datasets/{id}/                    delete dataset + rows (draft only)
  POST   /datasets/{id}/lock/               lock dataset (idempotent)
  POST   /datasets/{id}/clone/              fork to a new draft dataset

  GET    /datasets/{id}/rows/               paginated row list
  POST   /datasets/{id}/rows/               append a single row
  PATCH  /datasets/{id}/rows/{row_id}/      edit a single row
  DELETE /datasets/{id}/rows/{row_id}/      delete a single row
  POST   /datasets/{id}/rows/bulk/          bulk create rows (paste/import)
  POST   /datasets/{id}/rows/replace/       replace-all (atomic)
  POST   /datasets/{id}/rows/bulk-delete/   delete by ids or all=true

  GET    /datasets/{id}/snapshots/          list snapshots (read-only)

Lock semantics: any mutating endpoint refuses if the dataset is locked.
Tenant isolation: all queries filter by request.user's active org.
"""

from django.db import IntegrityError, transaction
from django.db.models import Max
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404
from rest_framework import generics, status
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import HasPermission
from tenants.permissions import get_request_org

from .models import Dataset, DatasetSnapshot
from .pagination import DatasetPagination, DatasetRowPagination
from .permissions import DatasetObjectPermission, IsDatasetEditable
from .serializers import (
    BulkDeleteInputSerializer,
    BulkRowsInputSerializer,
    DatasetCreateSerializer,
    DatasetSerializer,
    DatasetSnapshotSerializer,
    DatasetUpdateSerializer,
    model_for_kind,
    serializer_for_kind,
)
from .services.excel_import import ExcelImportError, import_excel_to_dataset
from .services.templates import TEMPLATES, render_template

XLSX_CONTENT_TYPE = (
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _require_org(request):
    org = get_request_org(request)
    if org is None and not request.user.is_superuser:
        raise PermissionDenied("An active organization context is required.")
    return org


def _scope_datasets_qs(request):
    """Build a Dataset queryset filtered by the active organization.

    Mirrors `_scope_jobs_qs` in processing.views for consistency.
    Superusers see all orgs by default; non-superusers see only their
    active org. `?all_orgs=true` is allowed only for superusers.
    """
    qs = Dataset.objects.all()
    user = request.user
    org = get_request_org(request)
    all_orgs = (request.query_params.get("all_orgs") or "").lower() in (
        "1",
        "true",
        "yes",
    )
    if user.is_superuser:
        if all_orgs:
            return qs
        if org is not None:
            return qs.filter(organization=org)
        return qs
    if org is None:
        raise PermissionDenied("An active organization context is required.")
    return qs.filter(organization=org)


def _get_owned_dataset(request, pk: str) -> Dataset:
    qs = _scope_datasets_qs(request)
    dataset = get_object_or_404(qs, pk=pk)
    return dataset


def _ensure_editable(dataset: Dataset):
    if dataset.is_locked:
        raise ValidationError(
            {"detail": "This dataset is locked and cannot be modified."}
        )


def _next_row_index(dataset: Dataset, model) -> int:
    """Return one past the current max row_index for this dataset (0-based)."""
    agg = model.objects.filter(dataset=dataset).aggregate(m=Max("row_index"))
    current = agg["m"]
    return 0 if current is None else current + 1


# ---------------------------------------------------------------------------
# Dataset list + create
# ---------------------------------------------------------------------------


class DatasetListCreateView(generics.ListCreateAPIView):
    """GET / + POST / under `/api/datasets/`."""

    pagination_class = DatasetPagination

    def get_permissions(self):
        if self.request.method == "POST":
            return [IsAuthenticated(), HasPermission(["datasets.edit"])]
        return [IsAuthenticated(), HasPermission(["datasets.view"])]

    def get_serializer_class(self):
        return DatasetCreateSerializer if self.request.method == "POST" else DatasetSerializer

    def get_queryset(self):
        qs = _scope_datasets_qs(self.request).select_related(
            "created_by", "locked_by", "organization"
        )
        kind = self.request.query_params.get("kind")
        if kind:
            qs = qs.filter(kind=kind)
        status_q = self.request.query_params.get("status")
        if status_q:
            qs = qs.filter(status=status_q)
        return qs

    def create(self, request, *args, **kwargs):
        org = _require_org(request)
        if org is None:
            raise PermissionDenied("Select an organization before creating datasets.")
        in_serializer = DatasetCreateSerializer(data=request.data)
        in_serializer.is_valid(raise_exception=True)
        try:
            dataset = Dataset.objects.create(
                organization=org,
                created_by=request.user,
                source=Dataset.Source.MANUAL,
                **in_serializer.validated_data,
            )
        except IntegrityError as exc:
            # Unique (organization, kind, name) collision is the only realistic
            # path here; surface as a 400 instead of letting it bubble to 500.
            raise ValidationError(
                {"name": "A dataset with this name and kind already exists."}
            ) from exc
        return Response(
            DatasetSerializer(dataset).data,
            status=status.HTTP_201_CREATED,
        )


# ---------------------------------------------------------------------------
# Dataset detail / update / delete
# ---------------------------------------------------------------------------


class DatasetDetailView(APIView):
    """GET, PATCH, DELETE for `/api/datasets/{id}/`."""

    permission_classes = [IsAuthenticated, DatasetObjectPermission]

    def _check_perm(self, request, keys):
        # We re-use HasPermission directly for symmetry with the rest of the
        # codebase rather than overriding get_permissions per HTTP method.
        if not HasPermission(keys).has_permission(request, self):
            raise PermissionDenied()

    def get(self, request, pk):
        self._check_perm(request, ["datasets.view"])
        dataset = _get_owned_dataset(request, pk)
        self.check_object_permissions(request, dataset)
        return Response(DatasetSerializer(dataset).data)

    def patch(self, request, pk):
        self._check_perm(request, ["datasets.edit"])
        dataset = _get_owned_dataset(request, pk)
        self.check_object_permissions(request, dataset)
        _ensure_editable(dataset)
        ser = DatasetUpdateSerializer(dataset, data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        ser.save()
        return Response(DatasetSerializer(dataset).data)

    def delete(self, request, pk):
        self._check_perm(request, ["datasets.delete"])
        dataset = _get_owned_dataset(request, pk)
        self.check_object_permissions(request, dataset)
        # We disallow deletion of locked datasets — once they've been
        # consumed by a job (which is what locks them), the user should
        # explicitly purge the job too. Snapshots PROTECT against ORM
        # cascade anyway; this is the friendlier early-fail.
        if dataset.is_locked:
            raise ValidationError(
                {"detail": "Locked datasets cannot be deleted. Fork or contact an admin."}
            )
        dataset.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class DatasetLockView(APIView):
    """POST /api/datasets/{id}/lock/ — idempotent."""

    permission_classes = [IsAuthenticated, DatasetObjectPermission]

    def post(self, request, pk):
        if not HasPermission(["datasets.lock"]).has_permission(request, self):
            raise PermissionDenied()
        dataset = _get_owned_dataset(request, pk)
        self.check_object_permissions(request, dataset)
        dataset.lock(by_user=request.user)
        return Response(DatasetSerializer(dataset).data)


class DatasetCloneView(APIView):
    """POST /api/datasets/{id}/clone/ — fork to a fresh draft.

    Body: {"name": "..."}. Copies all rows over. The new dataset starts
    as DRAFT and tracks its origin via `forked_from`.
    """

    permission_classes = [IsAuthenticated, DatasetObjectPermission]

    @transaction.atomic
    def post(self, request, pk):
        if not HasPermission(["datasets.edit"]).has_permission(request, self):
            raise PermissionDenied()
        source = _get_owned_dataset(request, pk)
        self.check_object_permissions(request, source)

        new_name = (request.data.get("name") or "").strip()
        if not new_name:
            raise ValidationError({"name": "Name is required for clone."})

        try:
            clone = Dataset.objects.create(
                organization=source.organization,
                kind=source.kind,
                name=new_name,
                description=source.description,
                source=Dataset.Source.MANUAL,
                created_by=request.user,
                forked_from=source,
            )
        except IntegrityError as exc:
            raise ValidationError(
                {"name": "A dataset with this name and kind already exists."}
            ) from exc

        model = model_for_kind(source.kind)
        # Copy rows. We re-instantiate so id and dataset FK are fresh.
        row_field_names = [
            f.name for f in model._meta.fields
            if f.name not in ("id", "dataset")
        ]
        bulk = []
        for row in model.objects.filter(dataset=source).order_by("row_index", "id"):
            kwargs = {name: getattr(row, name) for name in row_field_names}
            bulk.append(model(dataset=clone, **kwargs))
        if bulk:
            model.objects.bulk_create(bulk, batch_size=2000)
        clone.refresh_row_count()
        return Response(DatasetSerializer(clone).data, status=status.HTTP_201_CREATED)


# ---------------------------------------------------------------------------
# Row endpoints — kind-dispatched
# ---------------------------------------------------------------------------


class DatasetRowsView(APIView):
    """GET (list) and POST (append single row) under `/datasets/{id}/rows/`."""

    permission_classes = [IsAuthenticated, DatasetObjectPermission]
    pagination_class = DatasetRowPagination

    def get(self, request, pk):
        if not HasPermission(["datasets.view"]).has_permission(request, self):
            raise PermissionDenied()
        dataset = _get_owned_dataset(request, pk)
        self.check_object_permissions(request, dataset)
        model = model_for_kind(dataset.kind)
        serializer_cls = serializer_for_kind(dataset.kind)
        qs = model.objects.filter(dataset=dataset).order_by("row_index", "id")
        paginator = self.pagination_class()
        page = paginator.paginate_queryset(qs, request, view=self)
        data = serializer_cls(page, many=True).data
        return paginator.get_paginated_response(data)

    @transaction.atomic
    def post(self, request, pk):
        if not HasPermission(["datasets.edit"]).has_permission(request, self):
            raise PermissionDenied()
        dataset = _get_owned_dataset(request, pk)
        self.check_object_permissions(request, dataset)
        _ensure_editable(dataset)
        model = model_for_kind(dataset.kind)
        serializer_cls = serializer_for_kind(dataset.kind)
        payload = dict(request.data)
        payload.setdefault("row_index", _next_row_index(dataset, model))
        ser = serializer_cls(data=payload)
        ser.is_valid(raise_exception=True)
        row = ser.save(dataset=dataset)
        dataset.refresh_row_count()
        return Response(
            serializer_cls(row).data, status=status.HTTP_201_CREATED
        )


class DatasetRowDetailView(APIView):
    """PATCH and DELETE single row under `/datasets/{id}/rows/{row_id}/`."""

    permission_classes = [IsAuthenticated, DatasetObjectPermission]

    def patch(self, request, pk, row_id):
        if not HasPermission(["datasets.edit"]).has_permission(request, self):
            raise PermissionDenied()
        dataset = _get_owned_dataset(request, pk)
        self.check_object_permissions(request, dataset)
        _ensure_editable(dataset)
        model = model_for_kind(dataset.kind)
        serializer_cls = serializer_for_kind(dataset.kind)
        row = get_object_or_404(model, pk=row_id, dataset=dataset)
        ser = serializer_cls(row, data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        ser.save()
        return Response(serializer_cls(row).data)

    @transaction.atomic
    def delete(self, request, pk, row_id):
        if not HasPermission(["datasets.edit"]).has_permission(request, self):
            raise PermissionDenied()
        dataset = _get_owned_dataset(request, pk)
        self.check_object_permissions(request, dataset)
        _ensure_editable(dataset)
        model = model_for_kind(dataset.kind)
        row = get_object_or_404(model, pk=row_id, dataset=dataset)
        row.delete()
        dataset.refresh_row_count()
        return Response(status=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Bulk row endpoints — the primary insertion path for paste/import flows.
# ---------------------------------------------------------------------------


class DatasetRowsBulkView(APIView):
    """POST /datasets/{id}/rows/bulk/  — append a list of rows atomically."""

    permission_classes = [IsAuthenticated, DatasetObjectPermission]

    @transaction.atomic
    def post(self, request, pk):
        if not HasPermission(["datasets.edit"]).has_permission(request, self):
            raise PermissionDenied()
        dataset = _get_owned_dataset(request, pk)
        self.check_object_permissions(request, dataset)
        _ensure_editable(dataset)

        wrapper = BulkRowsInputSerializer(data=request.data)
        wrapper.is_valid(raise_exception=True)
        raw_rows = wrapper.validated_data["rows"]

        model = model_for_kind(dataset.kind)
        serializer_cls = serializer_for_kind(dataset.kind)
        start_index = _next_row_index(dataset, model)

        # Validate every row through the serializer before any insert.
        # This gives the client a useful per-row error map for paste UX.
        validated_rows = []
        errors = []
        for offset, raw in enumerate(raw_rows):
            row_data = dict(raw)
            row_data.setdefault("row_index", start_index + offset)
            ser = serializer_cls(data=row_data)
            if not ser.is_valid():
                errors.append({"index": offset, "errors": ser.errors})
                continue
            validated_rows.append(ser.validated_data)

        if errors:
            raise ValidationError({"rows": errors})

        # bulk_create with the model fields. We don't go through the
        # serializer's .save() so we can use bulk_create instead of one
        # INSERT per row.
        new_instances = [model(dataset=dataset, **data) for data in validated_rows]
        created = model.objects.bulk_create(new_instances, batch_size=2000)
        dataset.refresh_row_count()
        return Response(
            {
                "created": len(created),
                "first_row_index": start_index,
            },
            status=status.HTTP_201_CREATED,
        )


class DatasetRowsReplaceView(APIView):
    """POST /datasets/{id}/rows/replace/  — atomic delete-all + bulk insert.

    The same shape as bulk insert, but the dataset's existing rows are
    cleared first. Useful for "re-import" flows where the user has a
    fresh canonical list and wants the dataset to reflect it exactly.
    """

    permission_classes = [IsAuthenticated, DatasetObjectPermission]

    @transaction.atomic
    def post(self, request, pk):
        if not HasPermission(["datasets.edit"]).has_permission(request, self):
            raise PermissionDenied()
        dataset = _get_owned_dataset(request, pk)
        self.check_object_permissions(request, dataset)
        _ensure_editable(dataset)

        wrapper = BulkRowsInputSerializer(data=request.data)
        wrapper.is_valid(raise_exception=True)
        raw_rows = wrapper.validated_data["rows"]

        model = model_for_kind(dataset.kind)
        serializer_cls = serializer_for_kind(dataset.kind)

        validated_rows = []
        errors = []
        for offset, raw in enumerate(raw_rows):
            row_data = dict(raw)
            row_data.setdefault("row_index", offset)
            ser = serializer_cls(data=row_data)
            if not ser.is_valid():
                errors.append({"index": offset, "errors": ser.errors})
                continue
            validated_rows.append(ser.validated_data)

        if errors:
            raise ValidationError({"rows": errors})

        model.objects.filter(dataset=dataset).delete()
        new_instances = [model(dataset=dataset, **data) for data in validated_rows]
        created = model.objects.bulk_create(new_instances, batch_size=2000)
        dataset.refresh_row_count()
        return Response(
            {"replaced_with": len(created)},
            status=status.HTTP_200_OK,
        )


class DatasetRowsBulkDeleteView(APIView):
    """POST /datasets/{id}/rows/bulk-delete/ — delete by ids or all=true."""

    permission_classes = [IsAuthenticated, DatasetObjectPermission]

    @transaction.atomic
    def post(self, request, pk):
        if not HasPermission(["datasets.edit"]).has_permission(request, self):
            raise PermissionDenied()
        dataset = _get_owned_dataset(request, pk)
        self.check_object_permissions(request, dataset)
        _ensure_editable(dataset)

        ser = BulkDeleteInputSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data

        model = model_for_kind(dataset.kind)
        qs = model.objects.filter(dataset=dataset)
        if not data.get("all"):
            qs = qs.filter(id__in=data["row_ids"])
        deleted, _ = qs.delete()
        dataset.refresh_row_count()
        return Response({"deleted": deleted}, status=status.HTTP_200_OK)


# ---------------------------------------------------------------------------
# Snapshots — read-only.
# ---------------------------------------------------------------------------


class DatasetExcelImportView(APIView):
    """POST /api/datasets/import-excel/ — turn an uploaded .xlsx into a Dataset.

    Multipart fields: `file` (xlsx), `kind` (premium|claims_paid|claims_os),
    `name`, optional `description`. The resulting Dataset is in DRAFT status,
    `source = excel_import`, and contains one row per Excel row.
    """

    parser_classes = [MultiPartParser, FormParser]

    def get_permissions(self):
        return [IsAuthenticated(), HasPermission(["datasets.edit"])]

    def post(self, request):
        org = _require_org(request)
        if org is None:
            raise PermissionDenied("Select an organization before importing.")

        f = request.FILES.get("file")
        kind = (request.POST.get("kind") or "").strip()
        name = (request.POST.get("name") or "").strip()
        description = (request.POST.get("description") or "").strip()

        if not f:
            raise ValidationError({"file": "An .xlsx file is required."})
        if not f.name.lower().endswith(".xlsx"):
            raise ValidationError({"file": "Only .xlsx files are supported."})
        if not kind:
            raise ValidationError({"kind": "Field is required."})
        if not name:
            raise ValidationError({"name": "Field is required."})
        if kind not in dict(Dataset.Kind.choices):
            raise ValidationError({"kind": f"Unknown kind '{kind}'."})

        try:
            dataset = import_excel_to_dataset(
                organization=org,
                kind=kind,
                name=name,
                description=description,
                file_bytes=f.read(),
                created_by=request.user,
            )
        except ExcelImportError as exc:
            payload = exc.args[0] if exc.args else "Excel import failed."
            if isinstance(payload, dict):
                raise ValidationError(payload) from exc
            raise ValidationError({"detail": str(payload)}) from exc
        except IntegrityError as exc:
            raise ValidationError(
                {"name": "A dataset with this name and kind already exists."}
            ) from exc

        return Response(
            DatasetSerializer(dataset).data, status=status.HTTP_201_CREATED
        )


class DatasetSnapshotsView(generics.ListAPIView):
    """GET /datasets/{id}/snapshots/ — the audit log of "what this dataset
    looked like when each consumer job ran"."""

    serializer_class = DatasetSnapshotSerializer
    pagination_class = DatasetPagination
    permission_classes = [IsAuthenticated, DatasetObjectPermission]

    def get_queryset(self):
        if not HasPermission(["datasets.view"]).has_permission(self.request, self):
            raise PermissionDenied()
        dataset = _get_owned_dataset(self.request, self.kwargs["pk"])
        self.check_object_permissions(self.request, dataset)
        return DatasetSnapshot.objects.filter(dataset=dataset).order_by("-created_at")


class DatasetTemplateDownloadView(APIView):
    """GET /api/datasets/templates/{key}/ — download an empty .xlsx upload
    template for `key` (a dataset kind, the composite `previous_period`, or
    `reserve`). Templates carry no tenant data, so any authenticated user
    may fetch them."""

    permission_classes = [IsAuthenticated]

    def get(self, request, key):
        tpl = TEMPLATES.get(key)
        if tpl is None:
            raise Http404("Unknown template")
        content = render_template(key)
        response = HttpResponse(content, content_type=XLSX_CONTENT_TYPE)
        response["Content-Disposition"] = f'attachment; filename="{tpl.filename}"'
        return response
