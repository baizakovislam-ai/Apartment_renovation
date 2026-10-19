from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path('accounts/', include('apps.accounts.urls')),
    path("admin/", admin.site.urls),
    path("api/auth/", include("rest_framework.urls")),
    path("api/", include("apps.projects.urls")),
    path("api/", include("apps.repairs.urls")),
    path("", include("apps.workspace.urls")),
]
