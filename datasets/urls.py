from django.urls import path

from . import views

urlpatterns = [
    path("datasets/", views.DatasetListCreateView.as_view()),
    path("datasets/import-excel/", views.DatasetExcelImportView.as_view()),
    path("datasets/<uuid:pk>/", views.DatasetDetailView.as_view()),
    path("datasets/<uuid:pk>/lock/", views.DatasetLockView.as_view()),
    path("datasets/<uuid:pk>/clone/", views.DatasetCloneView.as_view()),
    path("datasets/<uuid:pk>/rows/", views.DatasetRowsView.as_view()),
    path("datasets/<uuid:pk>/rows/bulk/", views.DatasetRowsBulkView.as_view()),
    path(
        "datasets/<uuid:pk>/rows/replace/",
        views.DatasetRowsReplaceView.as_view(),
    ),
    path(
        "datasets/<uuid:pk>/rows/bulk-delete/",
        views.DatasetRowsBulkDeleteView.as_view(),
    ),
    path(
        "datasets/<uuid:pk>/rows/<int:row_id>/",
        views.DatasetRowDetailView.as_view(),
    ),
    path(
        "datasets/<uuid:pk>/snapshots/",
        views.DatasetSnapshotsView.as_view(),
    ),
]
