from django.urls import path
from . import views
from team_management.import_export_views import CSVImportAPIView, CSVExportAPIView

urlpatterns = [
    path("team-sets/", views.TeamSetListCreateView.as_view(), name="teamset-list-create"),
    path("team-sets/<int:pk>/", views.TeamSetDetailView.as_view(), name="teamset-detail"),
    path("teams/", views.TeamListCreateView.as_view(), name="team-list-create"),
    path("teams/<int:pk>/", views.TeamDetailView.as_view(), name="team-detail"),
    path("assignments/", views.TeamAssignmentListCreateView.as_view(), name="assignment-list-create"),
    path("assignments/<int:pk>/", views.TeamAssignmentUpdateView.as_view(), name="assignment-update"),

    path("api/import-csv/", CSVImportAPIView.as_view(), name="api-import-csv"),
    path("api/export-csv/", CSVExportAPIView.as_view(), name="api-export-csv"),   # NEW
]