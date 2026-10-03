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
    # GPS-импорт (§8), только администратор.
    path("import/", views.import_page, name="staff_import"),
    path("import/template.xlsx", views.import_template, name="staff_import_template"),
    path("import/preview", views.import_preview, name="staff_import_preview"),
    path("import/apply", views.import_apply, name="staff_import_apply"),
    path("import/batches/", views.import_batches, name="staff_import_batches"),
    path("import/<int:batch_id>/errors.xlsx", views.import_errors, name="staff_import_errors"),
    path("import/<int:batch_id>/revert", views.batch_revert, name="staff_batch_revert"),

    # Подключение парка (§7), только администратор.
    path("import/fleet/", views.fleet_import_page, name="staff_fleet_import"),
    path("import/fleet/template.xlsx", views.fleet_import_template, name="staff_fleet_import_template"),
    path("import/fleet/preview", views.fleet_import_preview, name="staff_fleet_import_preview"),
    path("import/fleet/apply", views.fleet_import_apply, name="staff_fleet_import_apply"),

    path("file/<int:attachment_id>/", views.staff_file, name="staff_file"),
    path(
        "file/<int:attachment_id>/thumb/",
        views.staff_file_thumb,
        name="staff_file_thumb",
    ),
]
