from django.contrib import admin
from django.urls import include, path

from apps.quality.views import remark_photo_file
from apps.repairs.views import photo_file

urlpatterns = [
    path('accounts/', include('apps.accounts.urls')),
    path("admin/", admin.site.urls),
    path("api/auth/", include("rest_framework.urls")),
    path("api/", include("apps.projects.urls")),
    path("api/", include("apps.repairs.urls")),
    path("api/", include("apps.quality.urls")),
    path("api/", include("apps.estimates.urls")),
    path("files/photos/<int:pk>/", photo_file, name="photo-file"),
    path("files/remark-photos/<int:pk>/", remark_photo_file, name="remark-photo-file"),
    path("", include("apps.workspace.urls")),
]
