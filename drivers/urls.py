from django.urls import path

from . import report_views, views

urlpatterns = [
    path("", views.driver_login, name="driver_login"),
    path("logout/", views.driver_logout, name="driver_logout"),
    path("cabinet/", views.driver_home, name="driver_home"),

    # Отправка месячного отчёта (четыре экрана, §5).
    path("report/", report_views.report_start, name="driver_report_start"),
    path("report/step1/", report_views.report_step1, name="driver_report_step1"),
    path("report/step2/", report_views.report_step2, name="driver_report_step2"),
    path("report/step3/", report_views.report_step3, name="driver_report_step3"),
    path("report/done/", report_views.report_done, name="driver_report_done"),

    # Закрытая раздача вложений водителю (только свой VIN, §9).
    path("file/<int:attachment_id>/", views.driver_file, name="driver_file"),
    path("file/<int:attachment_id>/thumb/", views.driver_file_thumb, name="driver_file_thumb"),
]
