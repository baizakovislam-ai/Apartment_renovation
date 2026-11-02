from django.contrib import admin
from django.urls import include, path

from apps.quality.views import remark_photo_file
from apps.repairs.views import photo_file, shared_photo, shared_report

urlpatterns = [
    path('accounts/', include('apps.accounts.urls')),
    path("admin/", admin.site.urls),
    path("api/auth/", include("rest_framework.urls")),
    path("api/", include("apps.projects.urls")),
    path("api/", include("apps.repairs.urls")),
    path("api/", include("apps.quality.urls")),
    path("api/", include("apps.estimates.urls")),
    path("api/", include("apps.pricing.urls")),
    path("api/", include("apps.reports.urls")),
    path("files/photos/<int:pk>/", photo_file, name="photo-file"),
    path("share/reports/<str:token>/", shared_report, name="shared-report"),
    path("share/reports/<str:token>/photos/<int:pk>/", shared_photo, name="shared-photo"),
    path("files/remark-photos/<int:pk>/", remark_photo_file, name="remark-photo-file"),
    path("", include("apps.workspace.urls")),
]
