#!/usr/bin/env python3
"""
Rellena total_tasks, failed_tasks, average_execution_time (y resto de stats) en runs
reutilizados que quedaron a 0/NULL porque su finish_round se procesó antes que el del
run origen.

Uso (con venv y .env/DATABASE_URL):
  python scripts/maintenance/backfill_reused_run_stats.py
  python scripts/maintenance/backfill_reused_run_stats.py --dry-run   # solo imprime qué se actualizaría
"""

import asyncio
import os
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
os.chdir(root)
sys.path.insert(0, str(root))

from sqlalchemy import or_, select  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

from app.db.models import AgentEvaluationRunORM  # noqa: E402
from app.db.session import async_session_maker  # noqa: E402


async def backfill(session: AsyncSession, dry_run: bool) -> int:
    # Runs que son reused, tienen source, y les faltan stats (total_tasks=0 o average_execution_time NULL)
    stmt = select(AgentEvaluationRunORM).where(
        AgentEvaluationRunORM.is_reused.is_(True),
        AgentEvaluationRunORM.reused_from_agent_run_id.isnot(None),
        or_(
            AgentEvaluationRunORM.total_tasks == 0,
            AgentEvaluationRunORM.average_execution_time.is_(None),
        ),
    )
    result = await session.scalars(stmt)
    reused_rows = list(result)
    if not reused_rows:
        return 0

    updated = 0
    for run_row in reused_rows:
        source_id = run_row.reused_from_agent_run_id
        stmt_src = select(AgentEvaluationRunORM).where(AgentEvaluationRunORM.agent_run_id == source_id)
        source = await session.scalar(stmt_src)
        if not source:
            continue
        has_src_stats = (getattr(source, "total_tasks", None) or 0) > 0 or getattr(source, "average_execution_time", None) is not None
        if not has_src_stats:
            continue
        if dry_run:
            print(f"Would backfill {run_row.agent_run_id} from {source_id}: total_tasks={source.total_tasks}, failed_tasks={source.failed_tasks}, avg_time={source.average_execution_time}")
        else:
            run_row.total_tasks = source.total_tasks or 0
            run_row.success_tasks = source.success_tasks or 0
            run_row.failed_tasks = source.failed_tasks or 0
            run_row.average_score = source.average_score
            run_row.average_execution_time = source.average_execution_time
            run_row.average_reward = source.average_reward
            if source.zero_reason and not run_row.zero_reason:
                run_row.zero_reason = source.zero_reason
        updated += 1

    if not dry_run and updated:
        await session.commit()
    return updated


def main():
    dry_run = "--dry-run" in sys.argv

    async def _run():
        async with async_session_maker() as session:
            n = await backfill(session, dry_run)
            print(f"Updated {n} reused run(s)." if not dry_run else f"Would update {n} reused run(s).")

    asyncio.run(_run())


if __name__ == "__main__":
    main()
