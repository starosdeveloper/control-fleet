from django.urls import path

from . import views

urlpatterns = [
    path("", views.dashboard_view, name="staff_dashboard"),
    path("export.xlsx", views.export_table_xlsx, name="staff_export_xlsx"),
    path("vehicle/<int:vehicle_id>/", views.vehicle_card, name="staff_vehicle_card"),
    path(
        "report/<int:report_id>/zip",
        views.export_report_zip,
        name="staff_report_zip",
    ),
    path(
        "reading/<int:reading_id>/decide",
        views.reading_decide,
        name="staff_reading_decide",
    ),
    path(
        "maintenance/<int:maint_id>/decide",
        views.maintenance_decide,
        name="staff_maintenance_decide",
    ),
    path("file/<int:attachment_id>/", views.staff_file, name="staff_file"),
    path(
        "file/<int:attachment_id>/thumb/",
        views.staff_file_thumb,
        name="staff_file_thumb",
    ),
]
