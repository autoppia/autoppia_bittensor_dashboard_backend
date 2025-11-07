"""
Extension methods for TasksService to handle tasks with solutions endpoint.
"""

from typing import Any, Dict, List, Optional
from sqlalchemy import String, and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import (
    AgentEvaluationRunORM,
    EvaluationResultORM,
    RoundORM,
    TaskORM,
    TaskSolutionORM,
)


def _map_website_port_to_name(url: Optional[str]) -> str:
    """Map localhost:PORT URLs to friendly website names."""
    if not url:
        return "unknown"

    from urllib.parse import urlparse

    PORT_TO_NAME = {
        "8000": "autocinema",
        "8001": "autobooks",
        "8002": "autozone",
        "8003": "autodining",
        "8004": "autocrm",
        "8005": "automail",
        "8006": "autodelivery",
        "8007": "autolodge",
        "8008": "autoconnect",
        "8009": "autowork",
        "8010": "autocalendar",
        "8011": "autolist",
        "8012": "autodrive",
        "8013": "autohealth",
        "8014": "autofinance",
    }

    try:
        parsed = urlparse(url if url.startswith("http") else f"http://{url}")
        port = str(parsed.port) if parsed.port else None
        if port and port in PORT_TO_NAME:
            return PORT_TO_NAME[port]
    except Exception:
        pass

    return "unknown"


async def get_tasks_with_solutions(
    session: AsyncSession,
    page: int = 1,
    limit: int = 50,
    task_id: Optional[str] = None,
    website: Optional[str] = None,
    use_case: Optional[str] = None,
    miner_uid: Optional[int] = None,
    agent_id: Optional[str] = None,
    validator_id: Optional[str] = None,
    round_id: Optional[int] = None,
    min_score: Optional[float] = None,
    max_score: Optional[float] = None,
    status: Optional[str] = None,
    sort_by: str = "created_at",
    sort_order: str = "desc",
) -> Dict[str, Any]:
    """
    Get tasks with their solutions, applying multiple filters.

    Returns:
        Dictionary with tasks, solutions, and pagination info
    """
    skip = max(0, (page - 1) * limit)

    # Build base query with eager loading
    base_stmt = select(EvaluationResultORM).options(
        selectinload(EvaluationResultORM.task),
        selectinload(EvaluationResultORM.task_solution),
        selectinload(EvaluationResultORM.agent_run).selectinload(
            AgentEvaluationRunORM.validator_round
        ),
    )

    count_stmt = select(func.count()).select_from(EvaluationResultORM)

    filters = []

    # Filter by task_id
    if task_id:
        filters.append(EvaluationResultORM.task_id == task_id)

    # Filter by website/project
    if website:
        website_lower = website.lower()
        # Join with TaskORM to filter by url
        base_stmt = base_stmt.join(
            TaskORM, EvaluationResultORM.task_id == TaskORM.task_id
        )
        count_stmt = count_stmt.join(
            TaskORM, EvaluationResultORM.task_id == TaskORM.task_id
        )
        filters.append(func.lower(TaskORM.url).like(f"%{website_lower}%"))

    # Filter by use_case (use_case is a JSON dict, extract 'name' field)
    # Note: This filter will be applied in Python after fetching results
    # because use_case is a JSON field and filtering in SQL is complex
    use_case_filter = use_case.lower() if use_case else None

    # Filter by miner_uid
    if miner_uid is not None:
        base_stmt = base_stmt.join(
            AgentEvaluationRunORM,
            EvaluationResultORM.agent_run_id == AgentEvaluationRunORM.agent_run_id,
        )
        count_stmt = count_stmt.join(
            AgentEvaluationRunORM,
            EvaluationResultORM.agent_run_id == AgentEvaluationRunORM.agent_run_id,
        )
        filters.append(AgentEvaluationRunORM.miner_uid == miner_uid)

    # Filter by agent_id (miner hotkey)
    if agent_id:
        if AgentEvaluationRunORM not in [t for t in base_stmt.froms]:
            base_stmt = base_stmt.join(
                AgentEvaluationRunORM,
                EvaluationResultORM.agent_run_id == AgentEvaluationRunORM.agent_run_id,
            )
            count_stmt = count_stmt.join(
                AgentEvaluationRunORM,
                EvaluationResultORM.agent_run_id == AgentEvaluationRunORM.agent_run_id,
            )
        filters.append(
            func.lower(AgentEvaluationRunORM.miner_hotkey) == agent_id.lower()
        )

    # Filter by validator_id
    if validator_id:
        if AgentEvaluationRunORM not in [t for t in base_stmt.froms]:
            base_stmt = base_stmt.join(
                AgentEvaluationRunORM,
                EvaluationResultORM.agent_run_id == AgentEvaluationRunORM.agent_run_id,
            )
            count_stmt = count_stmt.join(
                AgentEvaluationRunORM,
                EvaluationResultORM.agent_run_id == AgentEvaluationRunORM.agent_run_id,
            )
        filters.append(
            func.lower(AgentEvaluationRunORM.validator_hotkey) == validator_id.lower()
        )

    # Filter by round_id
    if round_id is not None:
        if AgentEvaluationRunORM not in [t for t in base_stmt.froms]:
            base_stmt = base_stmt.join(
                AgentEvaluationRunORM,
                EvaluationResultORM.agent_run_id == AgentEvaluationRunORM.agent_run_id,
            )
            count_stmt = count_stmt.join(
                AgentEvaluationRunORM,
                EvaluationResultORM.agent_run_id == AgentEvaluationRunORM.agent_run_id,
            )
        base_stmt = base_stmt.join(
            RoundORM,
            AgentEvaluationRunORM.validator_round_id == RoundORM.validator_round_id,
        )
        count_stmt = count_stmt.join(
            RoundORM,
            AgentEvaluationRunORM.validator_round_id == RoundORM.validator_round_id,
        )
        filters.append(RoundORM.round_number == round_id)

    # Filter by score range
    if min_score is not None:
        filters.append(EvaluationResultORM.final_score >= (min_score / 100.0))
    if max_score is not None:
        filters.append(EvaluationResultORM.final_score <= (max_score / 100.0))

    # Filter by status
    if status:
        status_lower = status.lower()
        if status_lower == "completed":
            filters.append(EvaluationResultORM.final_score >= 0.7)
        elif status_lower == "failed":
            filters.append(EvaluationResultORM.final_score < 0.7)
        elif status_lower == "pending":
            filters.append(EvaluationResultORM.final_score.is_(None))

    # Apply all filters
    for flt in filters:
        base_stmt = base_stmt.where(flt)
        count_stmt = count_stmt.where(flt)

    # Sorting
    sort_columns = {
        "created_at": EvaluationResultORM.created_at,
        "score": EvaluationResultORM.final_score,
        "duration": EvaluationResultORM.created_at,  # Fallback to created_at
    }

    order_expr = sort_columns.get(sort_by, EvaluationResultORM.created_at)
    if sort_order.lower() == "desc":
        order_clause = order_expr.desc()
    else:
        order_clause = order_expr.asc()

    base_stmt = base_stmt.order_by(order_clause)
    base_stmt = base_stmt.offset(skip).limit(limit)

    # Execute queries
    result = await session.execute(base_stmt)
    evaluation_results = result.scalars().all()

    total = int(await session.scalar(count_stmt) or 0)

    # Build response
    tasks_with_solutions = []
    for eval_result in evaluation_results:
        task_orm = eval_result.task
        solution_orm = eval_result.task_solution
        agent_run_orm = eval_result.agent_run

        if not task_orm:
            continue

        # Extract website from url
        website_name = _map_website_port_to_name(task_orm.url)

        # Extract use_case name from dict
        use_case_name = "unknown"
        if isinstance(task_orm.use_case, dict):
            use_case_name = task_orm.use_case.get("name", "unknown")
        elif isinstance(task_orm.use_case, str):
            use_case_name = task_orm.use_case

        # Apply use_case filter if specified
        if use_case_filter and use_case_name.lower() != use_case_filter:
            continue

        task_data = {
            "taskId": task_orm.task_id,
            "website": website_name,
            "useCase": use_case_name,
            "intent": task_orm.prompt or "",
            "startUrl": task_orm.url or "",
            "requiredUrl": None,  # Not available in TaskORM
            "createdAt": (
                task_orm.created_at.isoformat() if task_orm.created_at else None
            ),
        }

        solution_data = None
        if solution_orm:
            solution_data = {
                "taskSolutionId": solution_orm.solution_id,
                "trajectory": solution_orm.trajectory or [],
                "actions": solution_orm.actions or [],
                "createdAt": (
                    solution_orm.created_at.isoformat()
                    if solution_orm.created_at
                    else None
                ),
            }

        evaluation_data = {
            "evaluationResultId": eval_result.result_id,
            "score": round((eval_result.final_score or 0.0) * 100, 2),
            "passed": (eval_result.final_score or 0.0) >= 0.7,
        }

        agent_data = None
        if agent_run_orm:
            agent_data = {
                "agentRunId": agent_run_orm.agent_run_id,
                "minerUid": agent_run_orm.miner_uid,
                "minerHotkey": agent_run_orm.miner_hotkey,
                "validatorUid": agent_run_orm.validator_uid,
                "validatorHotkey": agent_run_orm.validator_hotkey,
            }

        tasks_with_solutions.append(
            {
                "task": task_data,
                "solution": solution_data,
                "evaluation": evaluation_data,
                "agentRun": agent_data,
            }
        )

    # If use_case filter was applied in Python, update total
    if use_case_filter:
        total = len(tasks_with_solutions)

    return {
        "tasks": tasks_with_solutions,
        "total": total,
        "page": page,
        "limit": limit,
        "totalPages": (total + limit - 1) // limit if limit > 0 else 0,
    }
