"""API for notifications (FR19). Each user sees only their own notifications."""
from django.shortcuts import get_object_or_404
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from . import services
from .serializers import NotificationSerializer


class NotificationListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        items = services.for_user(request.user)
        if request.query_params.get("unread") in ("1", "true"):
            items = items.filter(read_at__isnull=True)
        if str(request.query_params.get("project", "")).isdigit():
            items = items.filter(project_id=request.query_params["project"])
        return Response({"unread": services.unread_count(request.user),
                         "results": NotificationSerializer(items[:100], many=True).data})


class NotificationReadView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        notification = services.mark_read(get_object_or_404(services.for_user(request.user), pk=pk))
        return Response(NotificationSerializer(notification).data)


class NotificationReadAllView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        return Response({"marked": services.mark_all_read(request.user), "unread": services.unread_count(request.user)})


class NotificationSettingsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response({"email_enabled": services.email_enabled(request.user)})

    def patch(self, request):
        value = request.data.get("email_enabled")
        services.set_email_enabled(request.user, value in (True, "true", "1", 1))
        return Response({"email_enabled": services.email_enabled(request.user)})
