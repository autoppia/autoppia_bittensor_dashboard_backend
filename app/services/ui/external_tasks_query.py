"""
External tasks query helpers backed by the current UI data schema tables.
"""

from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import (
    AgentEvaluationRunORM,
    EvaluationORM,
    RoundORM,
    TaskORM,
)

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

NAME_TO_PORT = {v: k for k, v in PORT_TO_NAME.items()}


def _map_website_port_to_name(url: str | None) -> str:
    """Map localhost:PORT URLs to friendly website names."""
    if not url:
        return "unknown"

    from urllib.parse import urlparse

    try:
        parsed = urlparse(url if url.startswith("http") else f"http://{url}")
        port = str(parsed.port) if parsed.port else None
        if port and port in PORT_TO_NAME:
            return PORT_TO_NAME[port]
    except Exception:  # noqa: BLE001
        pass

    return "unknown"


def _map_website_name_to_port(website_name: str | None) -> str | None:
    """Map website name to port number for SQL filtering."""
    if not website_name:
        return None
    return NAME_TO_PORT.get(website_name.lower())


def _normalize_tests(raw_tests: list[Any] | None) -> list[dict[str, Any]]:
    """Ensure task tests are returned as plain dicts."""
    normalized: list[dict[str, Any]] = []
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
    task_id: str | None = None,
    website: str | None = None,
    use_case: str | None = None,
    web_version: str | None = None,
    miner_uid: int | None = None,
    agent_id: str | None = None,
    validator_id: str | None = None,
    round_id: int | None = None,
    min_score: float | None = None,
    max_score: float | None = None,
    status: str | None = None,
    success: bool | None = None,
    sort_by: str = "created_at",
    sort_order: str = "desc",
) -> dict[str, Any]:
    """
    Get tasks with their solutions, applying multiple filters.

    Returns:
        Dictionary with tasks, solutions, and pagination info
    """
    skip = max(0, (page - 1) * limit)

    # Build base query with eager loading
    base_stmt = select(EvaluationORM).options(
        selectinload(EvaluationORM.task),
        selectinload(EvaluationORM.task_solution),
        selectinload(EvaluationORM.agent_run).selectinload(AgentEvaluationRunORM.validator_round).selectinload(RoundORM.validator_snapshot),
    )

    count_stmt = select(func.count()).select_from(EvaluationORM)

    filters = []

    # Filter by task_id
    if task_id:
        filters.append(EvaluationORM.task_id == task_id)

    # Filter by web_version (can be done in SQL since it's a column)
    if web_version:
        # Join with TaskORM to filter by web_version
        # Check if TaskORM is already in the query to avoid duplicate joins
        if TaskORM not in [t for t in base_stmt.froms]:
            base_stmt = base_stmt.join(
                TaskORM,
                EvaluationORM.task_id == TaskORM.task_id,
            )
            count_stmt = count_stmt.join(
                TaskORM,
                EvaluationORM.task_id == TaskORM.task_id,
            )
        filters.append(TaskORM.web_version == web_version)

    # Filter by website/project - can be done in SQL by filtering URL by port
    website_filter = website.lower() if website else None
    website_port = _map_website_name_to_port(website_filter) if website_filter else None
    website_filtered_in_sql = False

    # Filter by use_case (use_case is a JSON dict, extract 'name' field)
    # We can filter directly in SQL using the index on use_case->>'name'
    use_case_filter = use_case.lower() if use_case else None
    use_case_filtered_in_sql = False

    # If we need to filter by website or use_case, join with TaskORM
    if website_port or use_case_filter:
        if TaskORM not in [t for t in base_stmt.froms]:
            base_stmt = base_stmt.join(
                TaskORM,
                EvaluationORM.task_id == TaskORM.task_id,
            )
            count_stmt = count_stmt.join(
                TaskORM,
                EvaluationORM.task_id == TaskORM.task_id,
            )

        # Filter by website port in SQL (more efficient than Python filtering)
        if website_port:
            # Filter URLs containing the port (e.g., ":8000" or "localhost:8000")
            filters.append(
                or_(
                    TaskORM.url.like(f"%:{website_port}%"),
                    TaskORM.url.like(f"%localhost:{website_port}%"),
                )
            )
            website_filtered_in_sql = True

        # Filter by use_case in SQL using the index
        if use_case_filter:
            filters.append(func.lower(TaskORM.use_case["name"].astext) == use_case_filter)
            use_case_filtered_in_sql = True

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
        filters.append(func.lower(AgentEvaluationRunORM.miner_hotkey) == agent_id.lower())

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
        filters.append(func.lower(AgentEvaluationRunORM.validator_hotkey) == validator_id.lower())

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
        filters.append(EvaluationORM.evaluation_score >= (min_score / 100.0))
    if max_score is not None:
        filters.append(EvaluationORM.evaluation_score <= (max_score / 100.0))

    # Filter by status
    if status:
        status_lower = status.lower()
        if status_lower == "completed":
            filters.append(EvaluationORM.evaluation_score >= 0.7)
        elif status_lower == "failed":
            filters.append(EvaluationORM.evaluation_score < 0.7)
        elif status_lower == "pending":
            filters.append(EvaluationORM.evaluation_score.is_(None))

    # Filter by success (true = score = 1.0, false = score < 1.0)
    if success is not None:
        if success:
            filters.append(EvaluationORM.evaluation_score == 1.0)
        else:
            filters.append(EvaluationORM.evaluation_score < 1.0)

    # Apply all filters
    for flt in filters:
        base_stmt = base_stmt.where(flt)
        count_stmt = count_stmt.where(flt)

    # Sorting
    sort_columns = {
        "created_at": EvaluationORM.created_at,
        "score": EvaluationORM.evaluation_score,
        "duration": EvaluationORM.created_at,  # Fallback to created_at
    }

    order_expr = sort_columns.get(sort_by, EvaluationORM.created_at)
    if sort_order.lower() == "desc":
        order_clause = order_expr.desc()
    else:
        order_clause = order_expr.asc()

    base_stmt = base_stmt.order_by(order_clause)

    # Simple pagination: apply offset and limit directly
    # If both website and use_case are filtered in SQL, we can use direct SQL pagination
    python_filters_needed = (website_filter and not website_filtered_in_sql) or (use_case_filter and not use_case_filtered_in_sql)
    if python_filters_needed:
        # For Python-side filters, we need to fetch more and filter after
        # Then apply pagination in Python
        # Increase fetch limit to ensure we get enough data when filtering
        fetch_multiplier = 20 if use_case_filter else 5
        fetch_limit = min(limit * fetch_multiplier, 2000)  # Increased from 500 to 2000
        fetch_offset = max(0, (page - 1) * limit)
        base_stmt = base_stmt.offset(fetch_offset).limit(fetch_limit)
    else:
        # Direct SQL pagination - simple: offset and limit
        # Both filters are in SQL, so we can paginate directly
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

        # Extract website from url (always needed for response, but only filter if not done in SQL)
        website_name = _map_website_port_to_name(task_orm.url)
        if website_filter and not website_filtered_in_sql:
            # Apply website filter if specified (only if not already filtered in SQL)
            if website_name.lower() != website_filter:
                continue

        # Extract use_case name from dict (always needed for response, but only filter if not done in SQL)
        use_case_name = "unknown"
        if isinstance(task_orm.use_case, dict):
            use_case_name = task_orm.use_case.get("name", "unknown")
        elif isinstance(task_orm.use_case, str):
            use_case_name = task_orm.use_case

        if use_case_filter and not use_case_filtered_in_sql:
            # Apply use_case filter if specified (only if not already filtered in SQL)
            if use_case_name.lower() != use_case_filter:
                continue

        task_data = {
            "taskId": task_orm.task_id,
            "website": website_name,
            "useCase": use_case_name,
            "prompt": task_orm.prompt or "",
            "startUrl": task_orm.url or "",
            "requiredUrl": None,  # Not available in TaskORM
            "webVersion": task_orm.web_version,
            "tests": _normalize_tests(task_orm.tests),
            "createdAt": (task_orm.created_at.isoformat() if task_orm.created_at else None),
        }

        solution_data = None
        if solution_orm:
            solution_data = {
                "taskSolutionId": solution_orm.solution_id,
                "trajectory": [],
                "actions": solution_orm.actions or [],
                "createdAt": (solution_orm.created_at.isoformat() if solution_orm.created_at else None),
            }

        # Score is binary: 0 or 1 (stored as 0.0 or 1.0 in DB)
        evaluation_score = eval_orm.evaluation_score or 0.0
        evaluation_data = {
            "evaluationResultId": eval_orm.evaluation_id,  # Use evaluation_id instead of result_id
            "score": int(evaluation_score),  # Convert to 0 or 1
            "passed": evaluation_score >= 1.0,  # True if score = 1, False if score = 0
        }

        agent_data = None
        if agent_run_orm:
            # Get validator info from validator_round.validator_snapshot
            validator_uid = None
            validator_hotkey = None
            if agent_run_orm.validator_round and agent_run_orm.validator_round.validator_snapshot:
                validator_uid = agent_run_orm.validator_round.validator_snapshot.validator_uid
                validator_hotkey = agent_run_orm.validator_round.validator_snapshot.validator_hotkey

            agent_data = {
                "agentRunId": agent_run_orm.agent_run_id,
                "minerUid": agent_run_orm.miner_uid,
                "minerHotkey": agent_run_orm.miner_hotkey,
                "validatorUid": validator_uid,
                "validatorHotkey": validator_hotkey,
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
    python_filters_applied = (website_filter and not website_filtered_in_sql) or (use_case_filter and not use_case_filtered_in_sql)
    if python_filters_applied:
        # We filtered in Python, so update total and apply pagination
        total = len(tasks_with_solutions)
        # Apply pagination after filtering
        start_idx = skip
        end_idx = skip + limit
        tasks_with_solutions = tasks_with_solutions[start_idx:end_idx]
    # else: total from DB count is already accurate - both filters were in SQL

    return {
        "tasks": tasks_with_solutions,
        "total": total,
        "page": page,
        "limit": limit,
        "totalPages": (total + limit - 1) // limit if limit > 0 else 0,
    }
