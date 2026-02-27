from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.validator.validator_storage_common import (
    DuplicateIdentifierError,
    PersistenceResult,
    RoundConflictError,
)
from app.services.validator.validator_storage_evaluations_mixin import ValidatorStorageEvaluationsMixin
from app.services.validator.validator_storage_helpers_mixin import ValidatorStorageHelpersMixin
from app.services.validator.validator_storage_rounds_mixin import ValidatorStorageRoundsMixin
from app.services.validator.validator_storage_summary_mixin import ValidatorStorageSummaryMixin


class ValidatorRoundPersistenceService(
    ValidatorStorageRoundsMixin,
    ValidatorStorageEvaluationsMixin,
    ValidatorStorageSummaryMixin,
    ValidatorStorageHelpersMixin,
):
    """Handle persisting validator round submissions into the SQL database."""

    def __init__(self, session: AsyncSession):
        self.session = session


__all__ = [
    "ValidatorRoundPersistenceService",
    "PersistenceResult",
    "RoundConflictError",
    "DuplicateIdentifierError",
]
