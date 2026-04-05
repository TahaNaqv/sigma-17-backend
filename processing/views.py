import json
import os
from datetime import datetime

from django.conf import settings
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404
from rest_framework import generics, status
from rest_framework.exceptions import ValidationError
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import (
    HasPermission,
    user_has_any_permission,
    user_has_role,
)

from .models import Module1Job
from .pagination import Module1JobPagination
from .serializers import Module1JobSerializer
from module1_engine.uw_patch import build_default_payload_template_from_workbook

from .tasks import (
    run_module1_policy_upr_task,
    run_module1_summary_task,
    run_module1_update_reserve_task,
    run_module1_uw_parameters_task,
)
from .utils import (
    init_job_work_dir,
    init_update_reserve_job_dirs,
    init_uw_parameters_job_dirs,
    job_input_subdir,
    job_output_dir,
)


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


class Module1JobObjectPermission(BasePermission):
    def has_object_permission(self, request, view, obj: Module1Job):
        if user_has_role(request.user, "Super Admin"):
            return True
        return obj.user_id == request.user.id


class CanReadModule1Job(BasePermission):
    """Detail/download: need one of these permissions (plus object ownership)."""

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        if user_has_role(request.user, "Super Admin"):
            return True
        return user_has_any_permission(
            request.user,
            ["module1.run", "outputs.download", "runhistory.view"],
        )


class Module1JobListView(generics.ListAPIView):
    serializer_class = Module1JobSerializer
    pagination_class = Module1JobPagination

    def get_permissions(self):
        return [IsAuthenticated(), HasPermission(["runhistory.view"])]

    def get_queryset(self):
        if user_has_role(self.request.user, "Super Admin"):
            return Module1Job.objects.all()
        return Module1Job.objects.filter(user=self.request.user)


class Module1JobDetailView(generics.RetrieveAPIView):
    serializer_class = Module1JobSerializer
    permission_classes = [
        IsAuthenticated,
        CanReadModule1Job,
        Module1JobObjectPermission,
    ]
    lookup_field = "pk"

    def get_queryset(self):
        if user_has_role(self.request.user, "Super Admin"):
            return Module1Job.objects.all()
        return Module1Job.objects.filter(user=self.request.user)


class Module1JobDownloadView(APIView):
    """Ownership enforced via queryset (404 if not your job, unless Super Admin)."""

    permission_classes = [IsAuthenticated, CanReadModule1Job]

    def get(self, request, pk):
        if user_has_role(request.user, "Super Admin"):
            qs = Module1Job.objects.all()
        else:
            qs = Module1Job.objects.filter(user=request.user)
        job = get_object_or_404(qs, pk=pk)
        if job.status != Module1Job.Status.SUCCESS or not job.output_zip:
            raise Http404()
        file_handle = job.output_zip.open("rb")
        return FileResponse(
            file_handle,
            as_attachment=True,
            filename=f"module1-{job.id}.zip",
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

        if not premium:
            raise ValidationError({"premium": "At least one premium file is required."})
        if not claims_paid:
            raise ValidationError(
                {"claims_paid": "At least one claims paid file is required."}
            )
        if not claims_os:
            raise ValidationError({"claims_os": "At least one claims OS file is required."})

        extra = [existing] if existing else []
        _check_upload_budget(premium, claims_paid, claims_os, extra)

        job = Module1Job.objects.create(
            user=request.user,
            job_type=Module1Job.JobType.SUMMARY,
            input_meta={
                "exp_start": exp_start,
                "exp_end": exp_end,
                "bop": bop,
                "eop": eop,
            },
            work_dir="",
        )
        rel = f"module1_jobs/{job.id}"
        job.work_dir = rel
        job.save(update_fields=["work_dir"])
        init_job_work_dir(job)

        meta_files = {
            "premium": _save_xlsx_list(job_input_subdir(job, "premium"), premium),
            "claims_paid": _save_xlsx_list(
                job_input_subdir(job, "claims_paid"), claims_paid
            ),
            "claims_os": _save_xlsx_list(job_input_subdir(job, "claims_os"), claims_os),
        }

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

        job.input_meta = {**job.input_meta, "files": meta_files}
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
        if not premium:
            raise ValidationError({"premium": "At least one premium file is required."})
        _check_upload_budget(premium)

        job = Module1Job.objects.create(
            user=request.user,
            job_type=Module1Job.JobType.POLICY_UPR,
            input_meta={"bop": bop, "eop": eop},
            work_dir="",
        )
        rel = f"module1_jobs/{job.id}"
        job.work_dir = rel
        job.save(update_fields=["work_dir"])
        init_job_work_dir(job)

        meta_files = {
            "premium": _save_xlsx_list(job_input_subdir(job, "premium"), premium),
        }
        job.input_meta = {**job.input_meta, "files": meta_files}
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
        if not reserve_files:
            raise ValidationError({"reserve": "At least one reserve workbook is required."})
        extra = [combined] if combined else []
        _check_upload_budget(reserve_files, extra)

        job = Module1Job.objects.create(
            user=request.user,
            job_type=Module1Job.JobType.UPDATE_RESERVE,
            input_meta={},
            work_dir="",
        )
        rel = f"module1_jobs/{job.id}"
        job.work_dir = rel
        job.save(update_fields=["work_dir"])
        staging = init_update_reserve_job_dirs(job)

        meta_files = {
            "reserve": _save_xlsx_list(staging, reserve_files),
        }
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

        job.input_meta = {"files": meta_files}
        job.save(update_fields=["input_meta"])

        run_module1_update_reserve_task.delay(str(job.id))
        return Response(
            Module1JobSerializer(job).data, status=status.HTTP_202_ACCEPTED
        )


class Module1CombinedSummaryUwPreviewView(APIView):
    """Parse Combined_Summary.xlsx and return default rows for UW parameter UI."""

    parser_classes = [MultiPartParser, FormParser]

    def get_permissions(self):
        return [IsAuthenticated(), HasPermission(["module1.run"])]

    def post(self, request):
        f = request.FILES.get("combined_summary")
        if not f:
            raise ValidationError(
                {"combined_summary": "Upload Combined_Summary.xlsx."}
            )
        if not f.name.lower().endswith(".xlsx"):
            raise ValidationError({"combined_summary": "Must be an .xlsx file."})
        _check_upload_budget([f])
        raw = f.read()
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
        if not f:
            raise ValidationError(
                {"combined_summary": "Upload Combined_Summary.xlsx."}
            )
        if not f.name.lower().endswith(".xlsx"):
            raise ValidationError({"combined_summary": "Must be an .xlsx file."})
        raw_payload = request.POST.get("payload", "{}")
        try:
            payload = json.loads(raw_payload)
        except json.JSONDecodeError as exc:
            raise ValidationError({"payload": "Invalid JSON."}) from exc
        if not isinstance(payload, dict):
            raise ValidationError({"payload": "Payload must be a JSON object."})
        _check_upload_budget([f])

        job = Module1Job.objects.create(
            user=request.user,
            job_type=Module1Job.JobType.UW_PARAMETERS,
            input_meta={"payload": payload},
            work_dir="",
        )
        rel = f"module1_jobs/{job.id}"
        job.work_dir = rel
        job.save(update_fields=["work_dir"])
        combined_dir = init_uw_parameters_job_dirs(job)
        dest = combined_dir / "Combined_Summary.xlsx"
        with dest.open("wb") as outf:
            for chunk in f.chunks():
                outf.write(chunk)
        meta_files = {
            "combined_summary": {"name": f.name, "size": f.size},
        }
        job.input_meta = {**job.input_meta, "files": meta_files}
        job.save(update_fields=["input_meta"])

        run_module1_uw_parameters_task.delay(str(job.id))
        return Response(
            Module1JobSerializer(job).data, status=status.HTTP_202_ACCEPTED
        )
