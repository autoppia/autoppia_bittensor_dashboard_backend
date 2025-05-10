from django.urls import path, include

urlpatterns = [
    path("", include("apps.task.urls")),
    path("", include("apps.metric.urls")),
]