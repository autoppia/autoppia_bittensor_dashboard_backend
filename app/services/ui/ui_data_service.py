from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.ui.evaluations_domain_service import EvaluationsDomainServiceMixin
from app.services.ui.tasks_domain_service import TasksDomainServiceMixin
from app.services.ui.ui_agents_runs_service_mixin import UIAgentsRunsServiceMixin
from app.services.ui.ui_overview_service_mixin import UIOverviewServiceMixin
from app.services.ui.ui_rounds_service_mixin import UIRoundsServiceMixin


class UIDataService(
    UIRoundsServiceMixin,
    UIAgentsRunsServiceMixin,
    UIOverviewServiceMixin,
    TasksDomainServiceMixin,
    EvaluationsDomainServiceMixin,
):
    """Simple read service using ONLY new DB schema tables."""

    def __init__(self, session: AsyncSession):
        self.session = session
