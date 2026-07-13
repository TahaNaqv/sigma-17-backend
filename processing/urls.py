from django.urls import path

from . import views

urlpatterns = [
    path("processing/jobs/", views.ProcessingJobListView.as_view()),
    path("processing/stats/", views.ProcessingStatsView.as_view()),
    path("processing/source-candidates/", views.SourceCandidatesView.as_view()),
    path("processing/job-drafts/", views.JobDraftView.as_view()),
    path(
        "module1/combined-summary/uw-preview/",
        views.Module1CombinedSummaryUwPreviewView.as_view(),
    ),
    path("module1/jobs/", views.Module1JobListView.as_view()),
    path("module1/jobs/summary/", views.Module1SummaryJobView.as_view()),
    path("module1/jobs/policy-upr/", views.Module1PolicyUprJobView.as_view()),
    path("module1/jobs/update-reserve/", views.Module1UpdateReserveJobView.as_view()),
    path("module1/jobs/uw-parameters/", views.Module1UwParametersJobView.as_view()),
    path("module1/jobs/<uuid:pk>/", views.Module1JobDetailView.as_view()),
    path("module1/jobs/<uuid:pk>/download/", views.Module1JobDownloadView.as_view()),
    path("module1/jobs/<uuid:pk>/output/files/", views.Module1JobOutputFilesView.as_view()),
    path("module1/jobs/<uuid:pk>/output/sheets/", views.Module1JobOutputSheetsView.as_view()),
    path("module1/jobs/<uuid:pk>/output/rows/", views.Module1JobOutputRowsView.as_view()),
    path(
        "module1/jobs/<uuid:pk>/reserve-workbooks/",
        views.Module1ReserveWorkbooksView.as_view(),
    ),
    path(
        "module1/jobs/<uuid:pk>/reserve-workbooks/<path:filename>/cdf/",
        views.Module1ReserveWorkbookCdfView.as_view(),
    ),
    path(
        "module1/jobs/<uuid:pk>/reserve-workbooks/<path:filename>/reserve-summary/",
        views.Module1ReserveWorkbookSummaryView.as_view(),
    ),
    path("module2/jobs/", views.Module2JobListView.as_view()),
    path("module2/jobs/allocate/", views.Module2AllocateJobView.as_view()),
    path("module2/jobs/process/", views.Module2ProcessJobView.as_view()),
    path("module2/jobs/movement/", views.Module2MovementJobView.as_view()),
    path("module2/jobs/<uuid:pk>/ulr/", views.Module2JobUlrRowsView.as_view()),
    path("module2/jobs/<uuid:pk>/", views.Module2JobDetailView.as_view()),
    path("module2/jobs/<uuid:pk>/download/", views.Module2JobDownloadView.as_view()),
    path("module2/jobs/<uuid:pk>/output/files/", views.Module2JobOutputFilesView.as_view()),
    path("module2/jobs/<uuid:pk>/output/sheets/", views.Module2JobOutputSheetsView.as_view()),
    path("module2/jobs/<uuid:pk>/output/rows/", views.Module2JobOutputRowsView.as_view()),
]
