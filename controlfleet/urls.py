from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    # Кабинет сотрудников (ТЗ §7): дашборд, таблица, карточка VIN, экспорт.
    path("staff/", include("staff.urls")),
    # Кабинет водителя занимает корень — основной публичный сценарий ТЗ.
    path("", include("drivers.urls")),
]
