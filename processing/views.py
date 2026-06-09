import json
import os
from datetime import datetime

from django.conf import settings
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404
from rest_framework import generics, status
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import (
    HasPermission,
    user_has_any_permission,
    user_has_role,
)
from datasets.models import Dataset
from datasets.services.snapshots import create_snapshot
from tenants.permissions import get_request_org

from .models import Module1Job
from .output_preview import (
    list_preview_files,
    list_workbook_sheets,
    parse_page_args,
    read_sheet_page,
)
from .pagination import Module1JobPagination
from .serializers import (
    Module1JobSerializer,
    Module2ProcessRequestSerializer,
    Module2UlrRowSerializer,
    OutputFilesResponseSerializer,
    OutputSheetPageResponseSerializer,
    OutputSheetsResponseSerializer,
    SourceCandidateSerializer,
)
from .services.reserve_workbook import (
    list_reserve_workbooks,
    read_workbook_cdfs,
)
from .services.source_resolver import (
    ARTIFACT_COMBINED_SUMMARY,
    ARTIFACT_PRODUCERS,
    list_candidate_sources,
    read_artifact_bytes,
    resolve_source_job,
)
from module1_engine.uw_patch import build_default_payload_template_from_workbook

from .tasks import (
    run_module1_policy_upr_task,
    run_module1_summary_task,
    run_module1_update_reserve_task,
    run_module1_uw_parameters_task,
    run_module2_allocate_task,
    run_module2_movement_task,
    run_module2_process_task,
)
from .utils import (
    init_job_work_dir,
    init_module2_allocate_job_dirs,
    init_module2_process_job_dirs,
    init_update_reserve_job_dirs,
    init_uw_parameters_job_dirs,
    job_input_subdir,
    job_output_dir,
)

MODULE1_JOB_TYPES = {
    Module1Job.JobType.SUMMARY,
    Module1Job.JobType.POLICY_UPR,
    Module1Job.JobType.UPDATE_RESERVE,
    Module1Job.JobType.UW_PARAMETERS,
}
MODULE2_JOB_TYPES = {
    Module1Job.JobType.MODULE2_ALLOCATE,
    Module1Job.JobType.MODULE2_PROCESS,
    Module1Job.JobType.MODULE2_MOVEMENT,
}
ALL_JOB_TYPES = MODULE1_JOB_TYPES | MODULE2_JOB_TYPES


# ---------------------------------------------------------------------------
# Org / scope helpers
# ---------------------------------------------------------------------------


def _require_org(request):
    """Return the request's active organization or raise PermissionDenied.
    Superusers without an org context get None back (and can pass ?all_orgs=true)."""
    org = get_request_org(request)
    if org is None and not request.user.is_superuser:
        raise PermissionDenied("An active organization context is required.")
    return org


def _scope_jobs_qs(request, job_types):
    """Build a Module1Job queryset filtered by the active organization.

    Superusers see all orgs by default; non-superusers see only their active org.
    `?all_orgs=true` is allowed only for superusers.
    """
    qs = Module1Job.objects.filter(job_type__in=job_types)
    user = request.user
    org = get_request_org(request)
    all_orgs = (request.query_params.get("all_orgs") or "").lower() in ("1", "true", "yes")
    if user.is_superuser:
        if all_orgs:
            return qs
        if org is not None:
            return qs.filter(organization=org)
        return qs  # superuser with no active org -> all
    if org is None:
        raise PermissionDenied("An active organization context is required.")
    return qs.filter(organization=org)


def _parse_dd_mm_yyyy(label: str, value: str) -> str:
    if not value or not str(value).strip():
        raise ValidationError({label: "This field is required."})
    try:
        datetime.strptime(value.strip(), "%d-%m-%Y")
    except ValueError as exc:
        raise ValidationError({label: "Date must be dd-MM-yyyy."}) from exc
    return value.strip()


def _check_upload_budget(*groups):
    max_n = settings.MODULE1_MAX_UPLOAD_FILES
    max_bytes = settings.MODULE1_MAX_UPLOAD_MB * 1024 * 1024
    n = 0
    total = 0
    for g in groups:
        if not g:
            continue
        for f in g:
            if f is None:
                continue
            n += 1
            total += f.size
    if n > max_n:
        raise ValidationError({"detail": f"Too many files (max {max_n})."})
    if total > max_bytes:
        raise ValidationError(
            {"detail": f"Upload size exceeds {settings.MODULE1_MAX_UPLOAD_MB} MB."}
        )


def _save_xlsx_list(dest, files):
    dest.mkdir(parents=True, exist_ok=True)
    saved = []
    for f in files:
        base = os.path.basename(f.name)
        if base.startswith("~$"):
            continue
        if not base.lower().endswith(".xlsx"):
            raise ValidationError({"detail": f"Only .xlsx allowed: {base}"})
        path = dest / base
        with path.open("wb") as out:
            for chunk in f.chunks():
                out.write(chunk)
        saved.append({"name": base, "size": f.size})
    if not saved:
        raise ValidationError({"detail": "No valid .xlsx files after filtering."})
    return saved


# ---------------------------------------------------------------------------
# Chaining helpers shared across consumer endpoints
# ---------------------------------------------------------------------------


def _read_combined_summary_source(
    request,
    *,
    field_name: str = "source_job_id",
):
    """Resolve the chained Combined_Summary source from request body, or None.

    Returns the resolved Module1Job or None if the caller did not supply
    `source_job_id`. Raises ValidationError on any malformed/invalid id.
    """
    raw = request.POST.get(field_name) or request.data.get(field_name)
    if not raw:
        return None
    return resolve_source_job(
        request=request,
        source_job_id=raw,
        artifact=ARTIFACT_COMBINED_SUMMARY,
        field_name=field_name,
    )


def _require_exactly_one(file_obj, source_job, *, file_name, source_name):
    """Required-input endpoints: file XOR source_job, exactly one."""
    has_file = bool(file_obj)
    has_source = source_job is not None
    if has_file and has_source:
        raise ValidationError(
            {"detail": f"Provide either {file_name} or {source_name}, not both."}
        )
    if not has_file and not has_source:
        raise ValidationError(
            {"detail": f"Provide either {file_name} or {source_name}."}
        )


def _require_at_most_one(file_obj, source_job, *, file_name, source_name):
    """Optional-input endpoints: file or source_job or neither."""
    if file_obj and source_job is not None:
        raise ValidationError(
            {"detail": f"Provide either {file_name} or {source_name}, not both."}
        )


# ---------------------------------------------------------------------------
# Dataset-driven input helpers
# ---------------------------------------------------------------------------


def _parse_dataset_ids(raw: str, field_name: str) -> list[str]:
    """Parse a comma-separated UUID string into a list. Empty → []."""
    if not raw:
        return []
    items = [s.strip() for s in str(raw).split(",") if s.strip()]
    if not items:
        return []
    import uuid as _uuid

    out = []
    for s in items:
        try:
            out.append(str(_uuid.UUID(s)))
        except ValueError as exc:
            raise ValidationError(
                {field_name: f"'{s}' is not a valid UUID."}
            ) from exc
    return out


def _resolve_datasets(
    request,
    *,
    ids: list[str],
    expected_kind: str,
    field_name: str,
) -> list[Dataset]:
    """Resolve a list of dataset UUIDs to Dataset objects, scoped to the
    active org and required to match `expected_kind`. Raises on any miss."""
    if not ids:
        return []
    org = get_request_org(request)
    qs = Dataset.objects.filter(id__in=ids)
    if not request.user.is_superuser:
        if org is None:
            raise PermissionDenied("An active organization context is required.")
        qs = qs.filter(organization=org)
    elif org is not None:
        # Superuser with an active org: still scope to that org for safety.
        qs = qs.filter(organization=org)
    found = {str(d.id): d for d in qs}
    missing = [i for i in ids if i not in found]
    if missing:
        raise ValidationError(
            {field_name: f"Datasets not found in this organization: {missing}"}
        )
    wrong_kind = [
        i for i, d in found.items() if d.kind != expected_kind
    ]
    if wrong_kind:
        raise ValidationError(
            {field_name: f"Expected kind '{expected_kind}', got mismatched: {wrong_kind}"}
        )
    # Preserve user-supplied order.
    return [found[i] for i in ids]


def _snapshot_for_job(datasets: list[Dataset], job: Module1Job) -> list[str]:
    """Create a snapshot for each dataset, linked to the job. Returns
    the snapshot UUIDs (as strings) for storage in `input_meta`."""
    out = []
    for ds in datasets:
        snap = create_snapshot(dataset=ds, consumer_job=job)
        out.append(str(snap.id))
    return out


# ---------------------------------------------------------------------------
# Permission classes
# ---------------------------------------------------------------------------


class Module1JobObjectPermission(BasePermission):
    """Job-level ownership: same org AND (same user OR superuser)."""

    def has_object_permission(self, request, view, obj: Module1Job):
        if request.user.is_superuser:
            return True
        org = get_request_org(request)
        if org is None or obj.organization_id != org.id:
            return False
        return obj.user_id == request.user.id


class CanReadModule1Job(BasePermission):
    """Detail/download: need one of these permissions in the active org."""

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        if request.user.is_superuser:
            return True
        return user_has_any_permission(
            request.user,
            ["module1.run", "module2.run", "outputs.download", "runhistory.view"],
            request=request,
        )


# ---------------------------------------------------------------------------
# List / detail / download views
# ---------------------------------------------------------------------------


class Module1JobListView(generics.ListAPIView):
    serializer_class = Module1JobSerializer
    pagination_class = Module1JobPagination

    def get_permissions(self):
        return [IsAuthenticated(), HasPermission(["runhistory.view"])]

    def get_queryset(self):
        return _scope_jobs_qs(self.request, MODULE1_JOB_TYPES).select_related("source_job")


class ProcessingJobListView(generics.ListAPIView):
    serializer_class = Module1JobSerializer
    pagination_class = Module1JobPagination

    def get_permissions(self):
        return [IsAuthenticated(), HasPermission(["runhistory.view"])]

    def get_queryset(self):
        return _scope_jobs_qs(self.request, ALL_JOB_TYPES).select_related("source_job")


class Module1JobDetailView(generics.RetrieveAPIView):
    serializer_class = Module1JobSerializer
    permission_classes = [
        IsAuthenticated,
        CanReadModule1Job,
        Module1JobObjectPermission,
    ]
    lookup_field = "pk"

    def get_queryset(self):
        return _scope_jobs_qs(self.request, MODULE1_JOB_TYPES).select_related("source_job")


class Module1JobDownloadView(APIView):
    permission_classes = [IsAuthenticated, CanReadModule1Job]

    def get(self, request, pk):
        job = _get_accessible_job(request, pk)
        if not job.output_available:
            raise Http404()
        file_handle = job.output_zip.open("rb")
        return FileResponse(
            file_handle,
            as_attachment=True,
            filename=f"module1-{job.id}.zip",
        )


def _get_accessible_job(request, pk) -> Module1Job:
    qs = _scope_jobs_qs(request, MODULE1_JOB_TYPES)
    job = get_object_or_404(qs, pk=pk)
    if not request.user.is_superuser and job.user_id != request.user.id:
        raise Http404()
    return job


def _get_accessible_module2_job(request, pk) -> Module1Job:
    qs = _scope_jobs_qs(request, MODULE2_JOB_TYPES)
    job = get_object_or_404(qs, pk=pk)
    if not request.user.is_superuser and job.user_id != request.user.id:
        raise Http404()
    return job


def _open_job_output_zip(job: Module1Job):
    if not job.output_available:
        raise Http404()
    return job.output_zip.open("rb")


class Module1JobOutputFilesView(APIView):
    permission_classes = [IsAuthenticated, CanReadModule1Job]

    def get(self, request, pk):
        job = _get_accessible_job(request, pk)
        with _open_job_output_zip(job) as f:
            try:
                items = list_preview_files(f)
            except ValueError as exc:
                raise ValidationError({"detail": str(exc)}) from exc
        payload = {
            "job_id": str(job.id),
            "files": [{"path": x.path, "size": x.size, "kind": x.kind} for x in items],
        }
        return Response(OutputFilesResponseSerializer(payload).data)


class Module1JobOutputSheetsView(APIView):
    permission_classes = [IsAuthenticated, CanReadModule1Job]

    def get(self, request, pk):
        file_path = request.query_params.get("file")
        if not file_path:
            raise ValidationError({"file": "Query parameter is required."})
        job = _get_accessible_job(request, pk)
        with _open_job_output_zip(job) as f:
            try:
                sheets = list_workbook_sheets(f, file_path)
            except ValueError as exc:
                raise ValidationError({"detail": str(exc)}) from exc
        payload = {
            "file": file_path.strip(),
            "sheets": [{"name": s.name, "rows": s.rows, "columns": s.columns} for s in sheets],
        }
        return Response(OutputSheetsResponseSerializer(payload).data)


class Module1JobOutputRowsView(APIView):
    permission_classes = [IsAuthenticated, CanReadModule1Job]

    def get(self, request, pk):
        file_path = request.query_params.get("file")
        sheet_name = request.query_params.get("sheet")
        if not file_path:
            raise ValidationError({"file": "Query parameter is required."})
        if not sheet_name:
            raise ValidationError({"sheet": "Query parameter is required."})
        try:
            page, page_size = parse_page_args(
                request.query_params.get("page"),
                request.query_params.get("page_size"),
                default_page_size=settings.MODULE1_OUTPUT_PREVIEW_DEFAULT_PAGE_SIZE,
                max_page_size=settings.MODULE1_OUTPUT_PREVIEW_MAX_PAGE_SIZE,
            )
        except ValueError as exc:
            raise ValidationError({"detail": str(exc)}) from exc
        job = _get_accessible_job(request, pk)
        with _open_job_output_zip(job) as f:
            try:
                payload = read_sheet_page(
                    f,
                    file_path=file_path,
                    sheet_name=sheet_name,
                    page=page,
                    page_size=page_size,
                    max_cells=settings.MODULE1_OUTPUT_PREVIEW_MAX_CELLS,
                )
            except ValueError as exc:
                raise ValidationError({"detail": str(exc)}) from exc
        return Response(OutputSheetPageResponseSerializer(payload).data)


# ---------------------------------------------------------------------------
# Job creation
# ---------------------------------------------------------------------------


def _create_job(request, *, job_type, input_meta, source_job=None, source_artifact=""):
    """Helper that creates a Module1Job stamped with user, org, and optional source."""
    org = _require_org(request)
    if org is None:
        raise PermissionDenied("Select an organization before creating jobs.")
    return Module1Job.objects.create(
        user=request.user,
        organization=org,
        job_type=job_type,
        input_meta=input_meta,
        work_dir="",
        source_job=source_job,
        source_artifact=source_artifact or "",
    )


class Module1SummaryJobView(APIView):
    parser_classes = [MultiPartParser, FormParser]

    def get_permissions(self):
        return [IsAuthenticated(), HasPermission(["module1.run"])]

    def post(self, request):
        exp_start = _parse_dd_mm_yyyy("exp_start", request.POST.get("exp_start", ""))
        exp_end = _parse_dd_mm_yyyy("exp_end", request.POST.get("exp_end", ""))
        bop = _parse_dd_mm_yyyy("bop", request.POST.get("bop", ""))
        eop = _parse_dd_mm_yyyy("eop", request.POST.get("eop", ""))

        premium = request.FILES.getlist("premium")
        claims_paid = request.FILES.getlist("claims_paid")
        claims_os = request.FILES.getlist("claims_os")
        existing = request.FILES.get("existing_combined_summary")
        existing_source = _read_combined_summary_source(
            request, field_name="existing_combined_summary_source_id"
        )

        # Dataset-driven inputs — each kind may come from datasets instead
        # of files. The view requires exactly one of (files, dataset_ids)
        # per kind so users can mix-and-match across kinds (e.g. premium
        # from a dataset, claims still from Excel).
        premium_ds_ids = _parse_dataset_ids(
            request.POST.get("premium_dataset_ids", ""), "premium_dataset_ids"
        )
        claims_paid_ds_ids = _parse_dataset_ids(
            request.POST.get("claims_paid_dataset_ids", ""),
            "claims_paid_dataset_ids",
        )
        claims_os_ds_ids = _parse_dataset_ids(
            request.POST.get("claims_os_dataset_ids", ""),
            "claims_os_dataset_ids",
        )

        def _need_one(files, ds_ids, *, files_label, ds_label):
            if files and ds_ids:
                raise ValidationError(
                    {"detail": f"Provide {files_label} or {ds_label}, not both."}
                )
            if not files and not ds_ids:
                raise ValidationError(
                    {files_label: f"Provide {files_label} or {ds_label}."}
                )

        _need_one(premium, premium_ds_ids,
                  files_label="premium", ds_label="premium_dataset_ids")
        _need_one(claims_paid, claims_paid_ds_ids,
                  files_label="claims_paid", ds_label="claims_paid_dataset_ids")
        _need_one(claims_os, claims_os_ds_ids,
                  files_label="claims_os", ds_label="claims_os_dataset_ids")

        # Resolve datasets up-front so a bad id fails before the job is
        # created. Snapshots come after job creation (FK target).
        premium_datasets = _resolve_datasets(
            request, ids=premium_ds_ids,
            expected_kind=Dataset.Kind.PREMIUM,
            field_name="premium_dataset_ids",
        )
        claims_paid_datasets = _resolve_datasets(
            request, ids=claims_paid_ds_ids,
            expected_kind=Dataset.Kind.CLAIMS_PAID,
            field_name="claims_paid_dataset_ids",
        )
        claims_os_datasets = _resolve_datasets(
            request, ids=claims_os_ds_ids,
            expected_kind=Dataset.Kind.CLAIMS_OS,
            field_name="claims_os_dataset_ids",
        )

        _require_at_most_one(
            existing, existing_source,
            file_name="existing_combined_summary",
            source_name="existing_combined_summary_source_id",
        )

        extra = [existing] if existing else []
        upload_groups = [g for g in (premium, claims_paid, claims_os) if g]
        _check_upload_budget(*upload_groups, extra)

        job = _create_job(
            request,
            job_type=Module1Job.JobType.SUMMARY,
            input_meta={
                "exp_start": exp_start,
                "exp_end": exp_end,
                "bop": bop,
                "eop": eop,
            },
            source_job=existing_source,
            source_artifact=(ARTIFACT_COMBINED_SUMMARY if existing_source else ""),
        )
        rel = f"module1_jobs/{job.id}"
        job.work_dir = rel
        job.save(update_fields=["work_dir"])
        init_job_work_dir(job)

        # File-driven kinds get saved straight to staging (existing path).
        meta_files = {}
        if premium:
            meta_files["premium"] = _save_xlsx_list(
                job_input_subdir(job, "premium"), premium
            )
        if claims_paid:
            meta_files["claims_paid"] = _save_xlsx_list(
                job_input_subdir(job, "claims_paid"), claims_paid
            )
        if claims_os:
            meta_files["claims_os"] = _save_xlsx_list(
                job_input_subdir(job, "claims_os"), claims_os
            )

        # Dataset-driven kinds get snapshotted now so the engine reads
        # the row payload captured at submit time, not at task pickup.
        dataset_snapshots = {}
        if premium_datasets:
            dataset_snapshots["premium"] = _snapshot_for_job(premium_datasets, job)
        if claims_paid_datasets:
            dataset_snapshots["claims_paid"] = _snapshot_for_job(
                claims_paid_datasets, job
            )
        if claims_os_datasets:
            dataset_snapshots["claims_os"] = _snapshot_for_job(
                claims_os_datasets, job
            )

        if existing:
            if not existing.name.lower().endswith(".xlsx"):
                raise ValidationError(
                    {"existing_combined_summary": "Must be an .xlsx file."}
                )
            out = job_output_dir(job)
            dest = out / "Combined_Summary.xlsx"
            with dest.open("wb") as outf:
                for chunk in existing.chunks():
                    outf.write(chunk)
            meta_files["existing_combined_summary"] = {
                "name": existing.name,
                "size": existing.size,
            }

        job.input_meta = {
            **job.input_meta,
            "files": meta_files,
            "dataset_snapshots": dataset_snapshots,
        }
        job.save(update_fields=["input_meta"])

        run_module1_summary_task.delay(str(job.id))
        return Response(
            Module1JobSerializer(job).data, status=status.HTTP_202_ACCEPTED
        )


class Module1PolicyUprJobView(APIView):
    parser_classes = [MultiPartParser, FormParser]

    def get_permissions(self):
        return [IsAuthenticated(), HasPermission(["module1.run"])]

    def post(self, request):
        bop = _parse_dd_mm_yyyy("bop", request.POST.get("bop", ""))
        eop = _parse_dd_mm_yyyy("eop", request.POST.get("eop", ""))
        premium = request.FILES.getlist("premium")
        premium_ds_ids = _parse_dataset_ids(
            request.POST.get("premium_dataset_ids", ""), "premium_dataset_ids"
        )

        if premium and premium_ds_ids:
            raise ValidationError(
                {"detail": "Provide premium files or premium_dataset_ids, not both."}
            )
        if not premium and not premium_ds_ids:
            raise ValidationError(
                {"premium": "Provide premium files or premium_dataset_ids."}
            )

        premium_datasets = _resolve_datasets(
            request, ids=premium_ds_ids,
            expected_kind=Dataset.Kind.PREMIUM,
            field_name="premium_dataset_ids",
        )

        if premium:
            _check_upload_budget(premium)

        job = _create_job(
            request,
            job_type=Module1Job.JobType.POLICY_UPR,
            input_meta={"bop": bop, "eop": eop},
        )
        rel = f"module1_jobs/{job.id}"
        job.work_dir = rel
        job.save(update_fields=["work_dir"])
        init_job_work_dir(job)

        meta_files = {}
        dataset_snapshots = {}
        if premium:
            meta_files["premium"] = _save_xlsx_list(
                job_input_subdir(job, "premium"), premium
            )
        if premium_datasets:
            dataset_snapshots["premium"] = _snapshot_for_job(premium_datasets, job)

        job.input_meta = {
            **job.input_meta,
            "files": meta_files,
            "dataset_snapshots": dataset_snapshots,
        }
        job.save(update_fields=["input_meta"])

        run_module1_policy_upr_task.delay(str(job.id))
        return Response(
            Module1JobSerializer(job).data, status=status.HTTP_202_ACCEPTED
        )


class Module1UpdateReserveJobView(APIView):
    parser_classes = [MultiPartParser, FormParser]

    def get_permissions(self):
        return [IsAuthenticated(), HasPermission(["module1.run"])]

    def post(self, request):
        reserve_files = request.FILES.getlist("reserve")
        combined = request.FILES.get("combined_summary")
        combined_source = _read_combined_summary_source(request)

        # Excel-free path: when the user provides `cdf_overrides`, the
        # reserve workbooks come from `source_job_id`'s output ZIP and
        # we apply the overrides to the Selected CDF rows before running
        # the engine. Mutually-exclusive with file upload.
        cdf_overrides_raw = request.POST.get("cdf_overrides")
        cdf_overrides = None
        if cdf_overrides_raw:
            try:
                cdf_overrides = json.loads(cdf_overrides_raw)
            except json.JSONDecodeError as exc:
                raise ValidationError(
                    {"cdf_overrides": "Invalid JSON."}
                ) from exc
            if not isinstance(cdf_overrides, dict):
                raise ValidationError(
                    {"cdf_overrides": "Must be a JSON object."}
                )

        if cdf_overrides is not None:
            if reserve_files:
                raise ValidationError(
                    {"detail": "Provide reserve files or cdf_overrides, not both."}
                )
            if combined_source is None:
                raise ValidationError(
                    {"source_job_id": "Required when supplying cdf_overrides."}
                )
        else:
            if not reserve_files:
                raise ValidationError(
                    {"reserve": "Provide reserve files or cdf_overrides + source_job_id."}
                )

        _require_at_most_one(
            combined, combined_source,
            file_name="combined_summary",
            source_name="source_job_id",
        )

        extra = [combined] if combined else []
        if reserve_files:
            _check_upload_budget(reserve_files, extra)
        elif combined:
            _check_upload_budget(extra)

        job = _create_job(
            request,
            job_type=Module1Job.JobType.UPDATE_RESERVE,
            input_meta={},
            source_job=combined_source,
            source_artifact=(ARTIFACT_COMBINED_SUMMARY if combined_source else ""),
        )
        rel = f"module1_jobs/{job.id}"
        job.work_dir = rel
        job.save(update_fields=["work_dir"])
        staging = init_update_reserve_job_dirs(job)

        meta_files = {}
        if reserve_files:
            meta_files["reserve"] = _save_xlsx_list(staging, reserve_files)
        if combined:
            if not combined.name.lower().endswith(".xlsx"):
                raise ValidationError(
                    {"combined_summary": "Must be an .xlsx file."}
                )
            dest = staging / "Combined_Summary.xlsx"
            with dest.open("wb") as outf:
                for chunk in combined.chunks():
                    outf.write(chunk)
            meta_files["combined_summary"] = {
                "name": combined.name,
                "size": combined.size,
            }

        new_meta = {"files": meta_files}
        if cdf_overrides is not None:
            new_meta["cdf_overrides"] = cdf_overrides
        job.input_meta = new_meta
        job.save(update_fields=["input_meta"])

        run_module1_update_reserve_task.delay(str(job.id))
        return Response(
            Module1JobSerializer(job).data, status=status.HTTP_202_ACCEPTED
        )


# ---------------------------------------------------------------------------
# Reserve workbook inspection — powers the Excel-free CDF editor UI.
# ---------------------------------------------------------------------------


class Module1ReserveWorkbooksView(APIView):
    """GET /api/module1/jobs/{pk}/reserve-workbooks/

    Lists the parseable reserve workbooks in a Summary-style job's
    output ZIP. The caller uses this to populate a per-workbook CDF
    editor before submitting an Update Reserve job.
    """

    def get_permissions(self):
        return [IsAuthenticated(), HasPermission(["module1.run"])]

    def get(self, request, pk):
        job = _get_accessible_job(request, pk)
        if not job.output_available:
            raise Http404()
        try:
            workbooks = list_reserve_workbooks(job)
        except ValueError as exc:
            raise ValidationError({"detail": str(exc)}) from exc
        return Response({"job_id": str(job.id), "workbooks": workbooks})


class Module1ReserveWorkbookCdfView(APIView):
    """GET /api/module1/jobs/{pk}/reserve-workbooks/{filename}/cdf/

    Reads the Selected CDF row for both triangle sheets of one reserve
    workbook. The response shape is what the editor binds to directly.
    """

    def get_permissions(self):
        return [IsAuthenticated(), HasPermission(["module1.run"])]

    def get(self, request, pk, filename):
        job = _get_accessible_job(request, pk)
        if not job.output_available:
            raise Http404()
        try:
            payload = read_workbook_cdfs(job, filename)
        except ValueError as exc:
            raise ValidationError({"detail": str(exc)}) from exc
        return Response(payload)


class Module1CombinedSummaryUwPreviewView(APIView):
    """Parse Combined_Summary.xlsx and return default rows for UW parameter UI.

    Accepts XOR: `combined_summary` file or `source_job_id`.
    """

    parser_classes = [MultiPartParser, FormParser]

    def get_permissions(self):
        return [IsAuthenticated(), HasPermission(["module1.run"])]

    def post(self, request):
        f = request.FILES.get("combined_summary")
        source = _read_combined_summary_source(request)
        _require_exactly_one(
            f, source,
            file_name="combined_summary",
            source_name="source_job_id",
        )

        if f:
            if not f.name.lower().endswith(".xlsx"):
                raise ValidationError({"combined_summary": "Must be an .xlsx file."})
            _check_upload_budget([f])
            raw = f.read()
        else:
            raw = read_artifact_bytes(source_job=source, artifact=ARTIFACT_COMBINED_SUMMARY)

        try:
            tpl = build_default_payload_template_from_workbook(raw)
        except ValueError as exc:
            raise ValidationError({"detail": str(exc)}) from exc
        return Response(tpl)


class Module1UwParametersJobView(APIView):
    parser_classes = [MultiPartParser, FormParser]

    def get_permissions(self):
        return [IsAuthenticated(), HasPermission(["module1.run"])]

    def post(self, request):
        f = request.FILES.get("combined_summary")
        source = _read_combined_summary_source(request)
        _require_exactly_one(
            f, source,
            file_name="combined_summary",
            source_name="source_job_id",
        )

        raw_payload = request.POST.get("payload", "{}")
        try:
            payload = json.loads(raw_payload)
        except json.JSONDecodeError as exc:
            raise ValidationError({"payload": "Invalid JSON."}) from exc
        if not isinstance(payload, dict):
            raise ValidationError({"payload": "Payload must be a JSON object."})

        if f:
            if not f.name.lower().endswith(".xlsx"):
                raise ValidationError({"combined_summary": "Must be an .xlsx file."})
            _check_upload_budget([f])

        job = _create_job(
            request,
            job_type=Module1Job.JobType.UW_PARAMETERS,
            input_meta={"payload": payload},
            source_job=source,
            source_artifact=(ARTIFACT_COMBINED_SUMMARY if source else ""),
        )
        rel = f"module1_jobs/{job.id}"
        job.work_dir = rel
        job.save(update_fields=["work_dir"])
        combined_dir = init_uw_parameters_job_dirs(job)

        meta_files = {}
        if f:
            dest = combined_dir / "Combined_Summary.xlsx"
            with dest.open("wb") as outf:
                for chunk in f.chunks():
                    outf.write(chunk)
            meta_files["combined_summary"] = {"name": f.name, "size": f.size}

        job.input_meta = {**job.input_meta, "files": meta_files}
        job.save(update_fields=["input_meta"])

        run_module1_uw_parameters_task.delay(str(job.id))
        return Response(
            Module1JobSerializer(job).data, status=status.HTTP_202_ACCEPTED
        )


class Module2JobListView(generics.ListAPIView):
    serializer_class = Module1JobSerializer
    pagination_class = Module1JobPagination

    def get_permissions(self):
        return [IsAuthenticated(), HasPermission(["runhistory.view"])]

    def get_queryset(self):
        return _scope_jobs_qs(self.request, MODULE2_JOB_TYPES).select_related("source_job")


class Module2JobDetailView(generics.RetrieveAPIView):
    serializer_class = Module1JobSerializer
    permission_classes = [IsAuthenticated, CanReadModule1Job, Module1JobObjectPermission]
    lookup_field = "pk"

    def get_object(self):
        return _get_accessible_module2_job(self.request, self.kwargs["pk"])


class Module2JobDownloadView(APIView):
    permission_classes = [IsAuthenticated, CanReadModule1Job]

    def get(self, request, pk):
        job = _get_accessible_module2_job(request, pk)
        if not job.output_available:
            raise Http404()
        file_handle = job.output_zip.open("rb")
        return FileResponse(file_handle, as_attachment=True, filename=f"module2-{job.id}.zip")


class Module2JobOutputFilesView(APIView):
    permission_classes = [IsAuthenticated, CanReadModule1Job]

    def get(self, request, pk):
        job = _get_accessible_module2_job(request, pk)
        with _open_job_output_zip(job) as f:
            try:
                items = list_preview_files(f)
            except ValueError as exc:
                raise ValidationError({"detail": str(exc)}) from exc
        payload = {
            "job_id": str(job.id),
            "files": [{"path": x.path, "size": x.size, "kind": x.kind} for x in items],
        }
        return Response(OutputFilesResponseSerializer(payload).data)


class Module2JobOutputSheetsView(APIView):
    permission_classes = [IsAuthenticated, CanReadModule1Job]

    def get(self, request, pk):
        file_path = request.query_params.get("file")
        if not file_path:
            raise ValidationError({"file": "Query parameter is required."})
        job = _get_accessible_module2_job(request, pk)
        with _open_job_output_zip(job) as f:
            try:
                sheets = list_workbook_sheets(f, file_path)
            except ValueError as exc:
                raise ValidationError({"detail": str(exc)}) from exc
        payload = {
            "file": file_path.strip(),
            "sheets": [{"name": s.name, "rows": s.rows, "columns": s.columns} for s in sheets],
        }
        return Response(OutputSheetsResponseSerializer(payload).data)


class Module2JobOutputRowsView(APIView):
    permission_classes = [IsAuthenticated, CanReadModule1Job]

    def get(self, request, pk):
        file_path = request.query_params.get("file")
        sheet_name = request.query_params.get("sheet")
        if not file_path:
            raise ValidationError({"file": "Query parameter is required."})
        if not sheet_name:
            raise ValidationError({"sheet": "Query parameter is required."})
        try:
            page, page_size = parse_page_args(
                request.query_params.get("page"),
                request.query_params.get("page_size"),
                default_page_size=settings.MODULE1_OUTPUT_PREVIEW_DEFAULT_PAGE_SIZE,
                max_page_size=settings.MODULE1_OUTPUT_PREVIEW_MAX_PAGE_SIZE,
            )
        except ValueError as exc:
            raise ValidationError({"detail": str(exc)}) from exc
        job = _get_accessible_module2_job(request, pk)
        with _open_job_output_zip(job) as f:
            try:
                payload = read_sheet_page(
                    f,
                    file_path=file_path,
                    sheet_name=sheet_name,
                    page=page,
                    page_size=page_size,
                    max_cells=settings.MODULE1_OUTPUT_PREVIEW_MAX_CELLS,
                )
            except ValueError as exc:
                raise ValidationError({"detail": str(exc)}) from exc
        return Response(OutputSheetPageResponseSerializer(payload).data)


class Module2AllocateJobView(APIView):
    parser_classes = [MultiPartParser, FormParser]

    def get_permissions(self):
        return [IsAuthenticated(), HasPermission(["module2.run"])]

    def post(self, request):
        combined = request.FILES.get("combined_summary")
        source = _read_combined_summary_source(request)
        _require_exactly_one(
            combined, source,
            file_name="combined_summary",
            source_name="source_job_id",
        )

        if combined:
            if not combined.name.lower().endswith(".xlsx"):
                raise ValidationError({"combined_summary": "Must be an .xlsx file."})
            _check_upload_budget([combined])

        job = _create_job(
            request,
            job_type=Module1Job.JobType.MODULE2_ALLOCATE,
            input_meta={},
            source_job=source,
            source_artifact=(ARTIFACT_COMBINED_SUMMARY if source else ""),
        )
        job.work_dir = f"module1_jobs/{job.id}"
        job.save(update_fields=["work_dir"])
        combined_dir = init_module2_allocate_job_dirs(job)

        meta_files = {}
        if combined:
            dest = combined_dir / "Combined_Summary.xlsx"
            with dest.open("wb") as outf:
                for chunk in combined.chunks():
                    outf.write(chunk)
            meta_files["combined_summary"] = {"name": combined.name, "size": combined.size}

        job.input_meta = {"files": meta_files}
        job.save(update_fields=["input_meta"])

        run_module2_allocate_task.delay(str(job.id))
        return Response(Module1JobSerializer(job).data, status=status.HTTP_202_ACCEPTED)


class Module2JobUlrRowsView(APIView):
    def get_permissions(self):
        return [IsAuthenticated(), HasPermission(["module2.run"])]

    def get(self, request, pk):
        job = _get_accessible_module2_job(request, pk)
        if job.job_type != Module1Job.JobType.MODULE2_ALLOCATE:
            raise ValidationError({"detail": "ULR rows are available only for allocate jobs."})
        if job.status != Module1Job.Status.SUCCESS:
            raise ValidationError({"detail": "ULR rows are available after successful allocate."})
        rows = (job.input_meta or {}).get("ulr_rows") or []
        return Response({"job_id": str(job.id), "rows": Module2UlrRowSerializer(rows, many=True).data})


class Module2ProcessJobView(APIView):
    parser_classes = [MultiPartParser, FormParser]

    def get_permissions(self):
        return [IsAuthenticated(), HasPermission(["module2.run"])]

    def post(self, request):
        allocate_job_id = request.POST.get("allocate_job_id")
        accounting_period = request.POST.get("accounting_period")
        selected_ulr_raw = request.POST.get("selected_ulr", "[]")
        previous = request.FILES.get("previous_period")
        expense = request.FILES.get("expense_cf")

        # Excel-free inputs (Phase 6): any of the three workbook slots
        # may be supplied as datasets instead of file uploads.
        expense_cf_ds_id = (
            request.POST.get("expense_cf_dataset_id") or ""
        ).strip()
        previous_lic_ds_id = (
            request.POST.get("previous_period_lic_dataset_id") or ""
        ).strip()
        previous_upr_ds_id = (
            request.POST.get("previous_period_upr_dataset_id") or ""
        ).strip()

        # XOR per slot. Expense: one xlsx OR one Expense CF dataset.
        # Previous Period: one xlsx OR (LIC dataset AND UPR dataset).
        # File and dataset cannot both be provided for the same slot.
        if expense and expense_cf_ds_id:
            raise ValidationError(
                {"detail": "Provide expense_cf or expense_cf_dataset_id, not both."}
            )
        if not expense and not expense_cf_ds_id:
            raise ValidationError(
                {"expense_cf": "Provide expense_cf or expense_cf_dataset_id."}
            )
        previous_via_dataset = bool(previous_lic_ds_id or previous_upr_ds_id)
        if previous and previous_via_dataset:
            raise ValidationError({
                "detail": (
                    "Provide previous_period or previous_period_{lic,upr}_dataset_id, "
                    "not both."
                )
            })
        if not previous and not previous_via_dataset:
            raise ValidationError(
                {"previous_period": "Provide previous_period file or both LIC+UPR dataset ids."}
            )
        if previous_via_dataset and not (previous_lic_ds_id and previous_upr_ds_id):
            raise ValidationError({
                "detail": (
                    "Both previous_period_lic_dataset_id AND previous_period_upr_dataset_id "
                    "are required for the dataset path."
                )
            })

        # File checks (only when files are actually being submitted)
        if previous and not previous.name.lower().endswith(".xlsx"):
            raise ValidationError({"detail": "Only .xlsx files are supported."})
        if expense and not expense.name.lower().endswith(".xlsx"):
            raise ValidationError({"detail": "Only .xlsx files are supported."})

        try:
            selected_ulr = json.loads(selected_ulr_raw)
        except json.JSONDecodeError as exc:
            raise ValidationError({"selected_ulr": "Invalid JSON."}) from exc
        payload_serializer = Module2ProcessRequestSerializer(
            data={
                "allocate_job_id": allocate_job_id,
                "accounting_period": accounting_period,
                "selected_ulr": selected_ulr,
            }
        )
        payload_serializer.is_valid(raise_exception=True)
        payload = payload_serializer.validated_data

        # Same primitive as the chained allocate path: resolve the allocate
        # job as a Combined_Summary source. This collapses two formerly
        # separate code paths into one.
        allocate_job = resolve_source_job(
            request=request,
            source_job_id=payload["allocate_job_id"],
            artifact=ARTIFACT_COMBINED_SUMMARY,
            field_name="allocate_job_id",
        )
        if allocate_job.job_type != Module1Job.JobType.MODULE2_ALLOCATE:
            raise ValidationError({"allocate_job_id": "Must reference a module2 allocate job."})

        # Resolve dataset ids up-front so a bad id fails before job creation.
        expense_datasets = _resolve_datasets(
            request,
            ids=[expense_cf_ds_id] if expense_cf_ds_id else [],
            expected_kind=Dataset.Kind.EXPENSE_CF,
            field_name="expense_cf_dataset_id",
        )
        previous_lic_datasets = _resolve_datasets(
            request,
            ids=[previous_lic_ds_id] if previous_lic_ds_id else [],
            expected_kind=Dataset.Kind.PREVIOUS_PERIOD_LIC,
            field_name="previous_period_lic_dataset_id",
        )
        previous_upr_datasets = _resolve_datasets(
            request,
            ids=[previous_upr_ds_id] if previous_upr_ds_id else [],
            expected_kind=Dataset.Kind.PREVIOUS_PERIOD_UPR,
            field_name="previous_period_upr_dataset_id",
        )

        upload_files = [f for f in (previous, expense) if f]
        if upload_files:
            _check_upload_budget(upload_files)
        job = _create_job(
            request,
            job_type=Module1Job.JobType.MODULE2_PROCESS,
            input_meta={
                "allocate_job_id": str(allocate_job.id),
                "accounting_period": payload["accounting_period"],
                "selected_ulr": payload["selected_ulr"],
            },
            source_job=allocate_job,
            source_artifact=ARTIFACT_COMBINED_SUMMARY,
        )
        job.work_dir = f"module1_jobs/{job.id}"
        job.save(update_fields=["work_dir"])
        previous_dir, expense_dir = init_module2_process_job_dirs(job)

        meta_files = {}
        if previous:
            with (previous_dir / "Previous_Period.xlsx").open("wb") as outf:
                for chunk in previous.chunks():
                    outf.write(chunk)
            meta_files["previous_period"] = {"name": previous.name, "size": previous.size}
        if expense:
            with (expense_dir / "Expense_CF.xlsx").open("wb") as outf:
                for chunk in expense.chunks():
                    outf.write(chunk)
            meta_files["expense_cf"] = {"name": expense.name, "size": expense.size}

        dataset_snapshots = {}
        if expense_datasets:
            dataset_snapshots["expense_cf"] = _snapshot_for_job(expense_datasets, job)
        if previous_lic_datasets:
            dataset_snapshots["previous_period_lic"] = _snapshot_for_job(
                previous_lic_datasets, job
            )
        if previous_upr_datasets:
            dataset_snapshots["previous_period_upr"] = _snapshot_for_job(
                previous_upr_datasets, job
            )

        meta = job.input_meta or {}
        meta["files"] = meta_files
        meta["dataset_snapshots"] = dataset_snapshots
        job.input_meta = meta
        job.save(update_fields=["input_meta"])
        run_module2_process_task.delay(str(job.id))
        return Response(Module1JobSerializer(job).data, status=status.HTTP_202_ACCEPTED)


class Module2MovementJobView(APIView):
    """POST /api/module2/jobs/movement/ — IFRS 17 movement-analysis disclosure.

    Same inputs as the process job (chains off an allocate job for Combined_Summary,
    plus Previous Period + Expense CF via file upload or dataset), with two extra
    parameters: ``reporting_date`` (label for the closing column; opening is always
    01/01 — plan §3b) and an optional ``scope`` (reserving_classes / uwys) to limit
    which (class, UWY) tables are produced. Output previews via the existing
    module2 output endpoints.
    """

    parser_classes = [MultiPartParser, FormParser]

    def get_permissions(self):
        return [IsAuthenticated(), HasPermission(["module2.run"])]

    def post(self, request):
        allocate_job_id = request.POST.get("allocate_job_id")
        accounting_period = request.POST.get("accounting_period")
        selected_ulr_raw = request.POST.get("selected_ulr", "[]")
        reporting_date = (request.POST.get("reporting_date") or "").strip() or None
        previous = request.FILES.get("previous_period")
        expense = request.FILES.get("expense_cf")

        expense_cf_ds_id = (request.POST.get("expense_cf_dataset_id") or "").strip()
        previous_lic_ds_id = (request.POST.get("previous_period_lic_dataset_id") or "").strip()
        previous_upr_ds_id = (request.POST.get("previous_period_upr_dataset_id") or "").strip()

        # XOR per slot — identical contract to the process job.
        if expense and expense_cf_ds_id:
            raise ValidationError({"detail": "Provide expense_cf or expense_cf_dataset_id, not both."})
        if not expense and not expense_cf_ds_id:
            raise ValidationError({"expense_cf": "Provide expense_cf or expense_cf_dataset_id."})
        previous_via_dataset = bool(previous_lic_ds_id or previous_upr_ds_id)
        if previous and previous_via_dataset:
            raise ValidationError({"detail": "Provide previous_period or previous_period_{lic,upr}_dataset_id, not both."})
        if not previous and not previous_via_dataset:
            raise ValidationError({"previous_period": "Provide previous_period file or both LIC+UPR dataset ids."})
        if previous_via_dataset and not (previous_lic_ds_id and previous_upr_ds_id):
            raise ValidationError({"detail": "Both previous_period_lic_dataset_id AND previous_period_upr_dataset_id are required for the dataset path."})
        if previous and not previous.name.lower().endswith(".xlsx"):
            raise ValidationError({"detail": "Only .xlsx files are supported."})
        if expense and not expense.name.lower().endswith(".xlsx"):
            raise ValidationError({"detail": "Only .xlsx files are supported."})

        try:
            selected_ulr = json.loads(selected_ulr_raw)
        except json.JSONDecodeError as exc:
            raise ValidationError({"selected_ulr": "Invalid JSON."}) from exc

        # Optional scope: reserving_classes / uwys as JSON arrays.
        scope = {}
        for field, caster in (("reserving_classes", str), ("uwys", int)):
            raw = request.POST.get(field)
            if not raw:
                continue
            try:
                values = json.loads(raw)
                scope[field] = [caster(v) for v in values]
            except (json.JSONDecodeError, TypeError, ValueError) as exc:
                raise ValidationError({field: "Must be a JSON array."}) from exc

        payload_serializer = Module2ProcessRequestSerializer(
            data={
                "allocate_job_id": allocate_job_id,
                "accounting_period": accounting_period,
                "selected_ulr": selected_ulr,
            }
        )
        payload_serializer.is_valid(raise_exception=True)
        payload = payload_serializer.validated_data

        allocate_job = resolve_source_job(
            request=request,
            source_job_id=payload["allocate_job_id"],
            artifact=ARTIFACT_COMBINED_SUMMARY,
            field_name="allocate_job_id",
        )
        if allocate_job.job_type != Module1Job.JobType.MODULE2_ALLOCATE:
            raise ValidationError({"allocate_job_id": "Must reference a module2 allocate job."})

        expense_datasets = _resolve_datasets(
            request,
            ids=[expense_cf_ds_id] if expense_cf_ds_id else [],
            expected_kind=Dataset.Kind.EXPENSE_CF,
            field_name="expense_cf_dataset_id",
        )
        previous_lic_datasets = _resolve_datasets(
            request,
            ids=[previous_lic_ds_id] if previous_lic_ds_id else [],
            expected_kind=Dataset.Kind.PREVIOUS_PERIOD_LIC,
            field_name="previous_period_lic_dataset_id",
        )
        previous_upr_datasets = _resolve_datasets(
            request,
            ids=[previous_upr_ds_id] if previous_upr_ds_id else [],
            expected_kind=Dataset.Kind.PREVIOUS_PERIOD_UPR,
            field_name="previous_period_upr_dataset_id",
        )

        upload_files = [f for f in (previous, expense) if f]
        if upload_files:
            _check_upload_budget(upload_files)
        job = _create_job(
            request,
            job_type=Module1Job.JobType.MODULE2_MOVEMENT,
            input_meta={
                "allocate_job_id": str(allocate_job.id),
                "accounting_period": payload["accounting_period"],
                "selected_ulr": payload["selected_ulr"],
                "reporting_date": reporting_date,
                "scope": scope,
            },
            source_job=allocate_job,
            source_artifact=ARTIFACT_COMBINED_SUMMARY,
        )
        job.work_dir = f"module1_jobs/{job.id}"
        job.save(update_fields=["work_dir"])
        previous_dir, expense_dir = init_module2_process_job_dirs(job)

        meta_files = {}
        if previous:
            with (previous_dir / "Previous_Period.xlsx").open("wb") as outf:
                for chunk in previous.chunks():
                    outf.write(chunk)
            meta_files["previous_period"] = {"name": previous.name, "size": previous.size}
        if expense:
            with (expense_dir / "Expense_CF.xlsx").open("wb") as outf:
                for chunk in expense.chunks():
                    outf.write(chunk)
            meta_files["expense_cf"] = {"name": expense.name, "size": expense.size}

        dataset_snapshots = {}
        if expense_datasets:
            dataset_snapshots["expense_cf"] = _snapshot_for_job(expense_datasets, job)
        if previous_lic_datasets:
            dataset_snapshots["previous_period_lic"] = _snapshot_for_job(previous_lic_datasets, job)
        if previous_upr_datasets:
            dataset_snapshots["previous_period_upr"] = _snapshot_for_job(previous_upr_datasets, job)

        meta = job.input_meta or {}
        meta["files"] = meta_files
        meta["dataset_snapshots"] = dataset_snapshots
        job.input_meta = meta
        job.save(update_fields=["input_meta"])
        run_module2_movement_task.delay(str(job.id))
        return Response(Module1JobSerializer(job).data, status=status.HTTP_202_ACCEPTED)


# ---------------------------------------------------------------------------
# Source candidates (powers the picker UIs)
# ---------------------------------------------------------------------------


class SourceCandidatesView(APIView):
    """GET /api/processing/source-candidates/?artifact=Combined_Summary.xlsx
    &job_type=summary&page=1&page_size=20

    Anyone with either module1.run or module2.run can list candidates.
    """

    def get_permissions(self):
        return [IsAuthenticated()]

    def get(self, request):
        if not (
            request.user.is_superuser
            or user_has_any_permission(
                request.user, ["module1.run", "module2.run"], request=request
            )
        ):
            raise PermissionDenied()

        artifact = request.query_params.get("artifact") or ARTIFACT_COMBINED_SUMMARY
        if artifact not in ARTIFACT_PRODUCERS:
            raise ValidationError(
                {"artifact": f"Unsupported artifact. Allowed: {sorted(ARTIFACT_PRODUCERS)}"}
            )

        job_type = request.query_params.get("job_type") or None
        if job_type and job_type not in ARTIFACT_PRODUCERS[artifact]:
            raise ValidationError(
                {"job_type": f"Not a producer of {artifact}."}
            )

        try:
            page = int(request.query_params.get("page", "1"))
            page_size = int(request.query_params.get("page_size", "20"))
        except ValueError:
            raise ValidationError({"detail": "page and page_size must be integers."})

        qs, total = list_candidate_sources(
            request=request,
            artifact=artifact,
            job_type=job_type,
            page=page,
            page_size=page_size,
        )
        items = list(qs)
        data = SourceCandidateSerializer(items, many=True).data
        return Response({
            "count": total,
            "page": page,
            "page_size": page_size,
            "artifact": artifact,
            "results": data,
        })
