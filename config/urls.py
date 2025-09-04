from django.urls import path, include

urlpatterns = [
    path("", include("apps.task.urls")),
    path("", include("apps.metric.urls")),
    path("validator-runs/", include("apps.validator_runs.urls")),
]
