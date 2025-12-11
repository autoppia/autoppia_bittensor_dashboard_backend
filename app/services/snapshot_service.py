"""
Service for materializing round snapshots and agent statistics.
"""
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.ui.rounds_service import RoundsService


class SnapshotService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.rounds_service = RoundsService(session)

