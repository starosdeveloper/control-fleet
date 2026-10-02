from django.urls import path

from . import views

urlpatterns = [
    path("", views.driver_login, name="driver_login"),
    path("logout/", views.driver_logout, name="driver_logout"),
    path("cabinet/", views.driver_home, name="driver_home"),
]
