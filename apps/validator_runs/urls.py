from django.urls import path, include
from rest_framework.routers import SimpleRouter
from .views import ValidatorRunViewSet

router = SimpleRouter()
router.register(r"", ValidatorRunViewSet, basename="validator-runs")

urlpatterns = [
    path("", include(router.urls)),
]
