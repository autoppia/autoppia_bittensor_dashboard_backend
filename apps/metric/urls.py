from django.urls import path, include
from rest_framework.routers import DefaultRouter
from apps.metric import views

router = DefaultRouter()
router.register(r"metrics", views.MetricViewSet, basename="metrics")

urlpatterns = [
    path("", include(router.urls)),
]
