from django.urls import path

from club_web import views

urlpatterns = [
    path("health", views.health, name="health"),
    path("api/<path:path>", views.api_proxy, name="api"),
    path("views/", views.fragment, {"path": ""}, name="home-fragment"),
    path("views/<path:path>", views.fragment, name="fragment"),
    path("assets/<path:path>", views.static_file, {"prefix": "assets"}, name="assets"),
    path("fonts/<path:path>", views.static_file, {"prefix": "fonts"}, name="fonts"),
    path("docs/<path:path>", views.static_file, {"prefix": "docs"}, name="docs"),
    path("", views.page, name="home"),
    path("<path:path>", views.page, name="page"),
]
