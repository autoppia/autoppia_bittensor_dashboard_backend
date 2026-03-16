from __future__ import annotations

import asyncio
from textwrap import dedent

from sqlalchemy import text

from app.db.session import AsyncSessionLocal

OVER_COST_LIMIT = 0.05
OVER_COST_HIT_THRESHOLD = 10


EVALUATION_BACKFILL_SQL = text(
    dedent(
        """
        WITH eval_costs AS (
            SELECT
                e.evaluation_id,
                COALESCE(SUM(lu.cost), 0.0) AS task_cost
            FROM evaluations e
            LEFT JOIN evaluation_llm_usage lu ON lu.evaluation_id = e.evaluation_id
            GROUP BY e.evaluation_id
        )
        UPDATE evaluations e
        SET zero_reason = 'over_cost_limit',
            updated_at = NOW()
        FROM eval_costs c
        WHERE e.evaluation_id = c.evaluation_id
          AND COALESCE(e.reward, 0.0) <= 0.0
          AND COALESCE(e.zero_reason, '') IN ('', 'task_failed')
          AND c.task_cost > :over_cost_limit
        """
    )
)


RUN_BACKFILL_SQL = text(
    dedent(
        """
        WITH eval_costs AS (
            SELECT
                e.agent_run_id,
                COUNT(e.evaluation_id) AS tasks_attempted,
                COUNT(*) FILTER (WHERE COALESCE(e.evaluation_score, 0.0) <= 0.0) AS failed_tasks,
                COUNT(*) FILTER (
                    WHERE COALESCE((
                        SELECT SUM(lu.cost)
                        FROM evaluation_llm_usage lu
                        WHERE lu.evaluation_id = e.evaluation_id
                    ), 0.0) > :over_cost_limit
                ) AS over_cost_hits
            FROM evaluations e
            GROUP BY e.agent_run_id
        )
        UPDATE miner_evaluation_runs mer
        SET tasks_attempted = c.tasks_attempted,
            failed_tasks = c.failed_tasks,
            early_stop_reason = CASE
                WHEN c.tasks_attempted < COALESCE(mer.total_tasks, c.tasks_attempted)
                 AND c.over_cost_hits >= :over_cost_hit_threshold
                THEN 'over_cost_limit'
                ELSE mer.early_stop_reason
            END,
            early_stop_message = CASE
                WHEN c.tasks_attempted < COALESCE(mer.total_tasks, c.tasks_attempted)
                 AND c.over_cost_hits >= :over_cost_hit_threshold
                THEN 'Stopped early after '
                     || c.tasks_attempted
                     || '/'
                     || COALESCE(mer.total_tasks, c.tasks_attempted)
                     || ' tasks: '
                     || c.over_cost_hits
                     || ' tasks exceeded the per-task cost limit of $'
                     || to_char(:over_cost_limit::numeric, 'FM0.00')
                     || '.'
                ELSE mer.early_stop_message
            END,
            updated_at = NOW()
        FROM eval_costs c
        WHERE mer.agent_run_id = c.agent_run_id
          AND (
                mer.tasks_attempted IS DISTINCT FROM c.tasks_attempted
             OR mer.failed_tasks IS DISTINCT FROM c.failed_tasks
             OR (
                    c.tasks_attempted < COALESCE(mer.total_tasks, c.tasks_attempted)
                AND c.over_cost_hits >= :over_cost_hit_threshold
                AND (
                        COALESCE(mer.early_stop_reason, '') <> 'over_cost_limit'
                     OR COALESCE(mer.early_stop_message, '') = ''
                )
             )
          )
        """
    )
)


async def main() -> None:
    async with AsyncSessionLocal() as session:
        eval_result = await session.execute(
            EVALUATION_BACKFILL_SQL,
            {"over_cost_limit": OVER_COST_LIMIT},
        )
        run_result = await session.execute(
            RUN_BACKFILL_SQL,
            {
                "over_cost_limit": OVER_COST_LIMIT,
                "over_cost_hit_threshold": OVER_COST_HIT_THRESHOLD,
            },
        )
        await session.commit()

        print(
            {
                "evaluations_updated": eval_result.rowcount,
                "runs_updated": run_result.rowcount,
                "over_cost_limit": OVER_COST_LIMIT,
                "over_cost_hit_threshold": OVER_COST_HIT_THRESHOLD,
            }
        )


if __name__ == "__main__":
    asyncio.run(main())
