from django.urls import path

from . import views

urlpatterns = [
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
]
