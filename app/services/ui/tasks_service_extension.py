"""
Extension methods for TasksService to handle tasks with solutions endpoint.
"""

from typing import Any, Dict, List, Optional
from sqlalchemy import String, and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import (
    AgentEvaluationRunORM,
    EvaluationORM,
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


def _normalize_tests(raw_tests: Optional[List[Any]]) -> List[Dict[str, Any]]:
    """Ensure task tests are returned as plain dicts."""
    normalized: List[Dict[str, Any]] = []
    if not raw_tests:
        return normalized

    for item in raw_tests:
        if item is None:
            continue
        if isinstance(item, dict):
            normalized.append(item)
            continue
        if hasattr(item, "model_dump"):
            try:
                normalized.append(item.model_dump())
                continue
            except Exception:  # noqa: BLE001
                pass
        if hasattr(item, "dict"):
            try:
                normalized.append(item.dict())
                continue
            except Exception:  # noqa: BLE001
                pass
        normalized.append({"value": item})

    return normalized


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
    success: Optional[bool] = None,
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
    # Use EvaluationORM instead of EvaluationResultORM (more likely to have data)
    base_stmt = select(EvaluationORM).options(
        selectinload(EvaluationORM.task),
        selectinload(EvaluationORM.task_solution),
        selectinload(EvaluationORM.agent_run).selectinload(
            AgentEvaluationRunORM.validator_round
        ),
    )

    count_stmt = select(func.count()).select_from(EvaluationORM)

    filters = []

    # Filter by task_id
    if task_id:
        filters.append(EvaluationORM.task_id == task_id)

    # Filter by website/project
    # Note: This filter will be applied in Python after fetching results
    # because website is mapped from port numbers (8000=autocinema, 8001=autobooks, etc.)
    website_filter = website.lower() if website else None

    # Filter by use_case (use_case is a JSON dict, extract 'name' field)
    # Note: This filter will be applied in Python after fetching results
    # because use_case is a JSON field and filtering in SQL is complex
    use_case_filter = use_case.lower() if use_case else None

    # Filter by miner_uid
    if miner_uid is not None:
        base_stmt = base_stmt.join(
            AgentEvaluationRunORM,
            EvaluationORM.agent_run_id == AgentEvaluationRunORM.agent_run_id,
        )
        count_stmt = count_stmt.join(
            AgentEvaluationRunORM,
            EvaluationORM.agent_run_id == AgentEvaluationRunORM.agent_run_id,
        )
        filters.append(AgentEvaluationRunORM.miner_uid == miner_uid)

    # Filter by agent_id (miner hotkey)
    if agent_id:
        if AgentEvaluationRunORM not in [t for t in base_stmt.froms]:
            base_stmt = base_stmt.join(
                AgentEvaluationRunORM,
                EvaluationORM.agent_run_id == AgentEvaluationRunORM.agent_run_id,
            )
            count_stmt = count_stmt.join(
                AgentEvaluationRunORM,
                EvaluationORM.agent_run_id == AgentEvaluationRunORM.agent_run_id,
            )
        filters.append(
            func.lower(AgentEvaluationRunORM.miner_hotkey) == agent_id.lower()
        )

    # Filter by validator_id
    if validator_id:
        if AgentEvaluationRunORM not in [t for t in base_stmt.froms]:
            base_stmt = base_stmt.join(
                AgentEvaluationRunORM,
                EvaluationORM.agent_run_id == AgentEvaluationRunORM.agent_run_id,
            )
            count_stmt = count_stmt.join(
                AgentEvaluationRunORM,
                EvaluationORM.agent_run_id == AgentEvaluationRunORM.agent_run_id,
            )
        filters.append(
            func.lower(AgentEvaluationRunORM.validator_hotkey) == validator_id.lower()
        )

    # Filter by round_id
    if round_id is not None:
        if AgentEvaluationRunORM not in [t for t in base_stmt.froms]:
            base_stmt = base_stmt.join(
                AgentEvaluationRunORM,
                EvaluationORM.agent_run_id == AgentEvaluationRunORM.agent_run_id,
            )
            count_stmt = count_stmt.join(
                AgentEvaluationRunORM,
                EvaluationORM.agent_run_id == AgentEvaluationRunORM.agent_run_id,
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
        filters.append(EvaluationORM.final_score >= (min_score / 100.0))
    if max_score is not None:
        filters.append(EvaluationORM.final_score <= (max_score / 100.0))

    # Filter by status
    if status:
        status_lower = status.lower()
        if status_lower == "completed":
            filters.append(EvaluationORM.final_score >= 0.7)
        elif status_lower == "failed":
            filters.append(EvaluationORM.final_score < 0.7)
        elif status_lower == "pending":
            filters.append(EvaluationORM.final_score.is_(None))

    # Filter by success (true = score = 1.0, false = score < 1.0)
    if success is not None:
        if success:
            filters.append(EvaluationORM.final_score == 1.0)
        else:
            filters.append(EvaluationORM.final_score < 1.0)

    # Apply all filters
    for flt in filters:
        base_stmt = base_stmt.where(flt)
        count_stmt = count_stmt.where(flt)

    # Sorting
    sort_columns = {
        "created_at": EvaluationORM.created_at,
        "score": EvaluationORM.final_score,
        "duration": EvaluationORM.created_at,  # Fallback to created_at
    }

    order_expr = sort_columns.get(sort_by, EvaluationORM.created_at)
    if sort_order.lower() == "desc":
        order_clause = order_expr.desc()
    else:
        order_clause = order_expr.asc()

    base_stmt = base_stmt.order_by(order_clause)

    # Simple pagination: apply offset and limit directly
    if website_filter or use_case_filter:
        # For Python-side filters, we need to fetch more and filter after
        # Then apply pagination in Python
        fetch_limit = min(limit * 5, 500)
        fetch_offset = max(0, (page - 1) * limit)
        base_stmt = base_stmt.offset(fetch_offset).limit(fetch_limit)
    else:
        # Direct SQL pagination - simple: offset and limit
        base_stmt = base_stmt.offset(skip).limit(limit)

    # Execute queries
    result = await session.execute(base_stmt)
    evaluations = result.scalars().all()

    total = int(await session.scalar(count_stmt) or 0)

    # Build response
    tasks_with_solutions = []
    for eval_orm in evaluations:
        task_orm = eval_orm.task
        solution_orm = eval_orm.task_solution
        agent_run_orm = eval_orm.agent_run

        if not task_orm:
            continue

        # Extract website from url
        website_name = _map_website_port_to_name(task_orm.url)

        # Apply website filter if specified
        if website_filter and website_name.lower() != website_filter:
            continue

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
            "tests": _normalize_tests(task_orm.tests),
            "createdAt": (
                task_orm.created_at.isoformat() if task_orm.created_at else None
            ),
        }

        solution_data = None
        if solution_orm:
            solution_data = {
                "taskSolutionId": solution_orm.solution_id,
                "trajectory": [],
                "actions": solution_orm.actions or [],
                "createdAt": (
                    solution_orm.created_at.isoformat()
                    if solution_orm.created_at
                    else None
                ),
            }

        # Score is binary: 0 or 1 (stored as 0.0 or 1.0 in DB)
        final_score = eval_orm.final_score or 0.0
        evaluation_data = {
            "evaluationResultId": eval_orm.evaluation_id,  # Use evaluation_id instead of result_id
            "score": int(final_score),  # Convert to 0 or 1
            "passed": final_score >= 1.0,  # True if score = 1, False if score = 0
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

    # If website or use_case filters were applied in Python, update total and apply pagination
    if website_filter or use_case_filter:
        # We filtered in Python, so update total and apply pagination
        total = len(tasks_with_solutions)
        # Apply pagination after filtering
        start_idx = skip
        end_idx = skip + limit
        tasks_with_solutions = tasks_with_solutions[start_idx:end_idx]
    # else: total from DB count is already accurate - no need to filter duplicates

    return {
        "tasks": tasks_with_solutions,
        "total": total,
        "page": page,
        "limit": limit,
        "totalPages": (total + limit - 1) // limit if limit > 0 else 0,
    }
