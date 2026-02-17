#!/usr/bin/env python3
"""
Export tasks for a given season (across all validators) into benchmark-ready JSON files.

Output format matches the benchmark task cache:
{
  "project_id": "...",
  "project_name": "...",
  "timestamp": "...",
  "tasks": [ ... ]
}
"""

import argparse
import asyncio
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable

sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import AsyncSessionLocal
from app.db.models import TaskORM, ValidatorRoundORM


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _project_display_name(project_id: str) -> str:
    if not project_id:
        return "unknown"
    return project_id.replace("_", " ").replace("-", " ").title()


def _task_to_benchmark_payload(task: TaskORM) -> Dict[str, Any]:
    return {
        "id": task.task_id,
        "is_web_real": bool(task.is_web_real),
        "web_project_id": task.web_project_id,
        "web_version": task.web_version,
        "url": task.url,
        "prompt": task.prompt,
        "original_prompt": task.prompt,
        "specifications": dict(task.specifications or {}),
        "tests": list(task.tests or []),
        "use_case": dict(task.use_case or {}),
        "should_record": False,
    }


async def _fetch_tasks_by_season(session: AsyncSession, season: int) -> Iterable[TaskORM]:
    stmt = (
        select(TaskORM)
        .join(
            ValidatorRoundORM,
            TaskORM.validator_round_id == ValidatorRoundORM.validator_round_id,
        )
        .where(ValidatorRoundORM.season_number == season)
        .order_by(TaskORM.task_id.asc())
    )
    result = await session.scalars(stmt)
    return result.all()


def _resolve_output_dir(base_dir: Path, season: int) -> Path:
    return base_dir / "iwap_task" / "season" / str(season)


async def export_tasks(
    season: int,
    base_out_dir: Path,
    project_filter: set[str] | None = None,
    limit_per_project: int | None = None,
) -> None:
    out_dir = _resolve_output_dir(base_out_dir, season)
    out_dir.mkdir(parents=True, exist_ok=True)
    async with AsyncSessionLocal() as session:
        tasks = await _fetch_tasks_by_season(session, season)

    grouped: Dict[str, list[Dict[str, Any]]] = defaultdict(list)
    for task in tasks:
        project_id = task.web_project_id or "unknown"
        if project_filter and project_id not in project_filter:
            continue
        grouped[project_id].append(_task_to_benchmark_payload(task))

    if not grouped:
        print(f"No tasks found for season {season}.")
        return

    timestamp = _now_iso()
    all_tasks: list[Dict[str, Any]] = []
    for project_id, project_tasks in grouped.items():
        if limit_per_project is not None:
            project_tasks = project_tasks[:limit_per_project]

        all_tasks.extend(project_tasks)

        payload = {
            "project_id": project_id,
            "project_name": _project_display_name(project_id),
            "timestamp": timestamp,
            "tasks": project_tasks,
        }

        filename = out_dir / f"{project_id}_tasks.json"
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        print(f"✅ Wrote {len(project_tasks)} tasks to {filename}")

    global_payload = {
        "season": season,
        "project_id": "all",
        "project_name": "All Projects",
        "timestamp": timestamp,
        "tasks": all_tasks,
    }
    global_file = out_dir / "all_tasks.json"
    with open(global_file, "w", encoding="utf-8") as f:
        json.dump(global_payload, f, indent=2)
    print(f"✅ Wrote {len(all_tasks)} tasks to {global_file}")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export tasks by season.")
    parser.add_argument("--season", type=int, required=True, help="Season number.")
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path("output"),
        help="Base output directory (season folder will be created inside).",
    )
    parser.add_argument(
        "--project",
        action="append",
        dest="projects",
        help="Optional project_id filter (can be repeated).",
    )
    parser.add_argument(
        "--limit-per-project",
        type=int,
        default=None,
        help="Optional max tasks per project.",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    project_filter = set(args.projects) if args.projects else None
    asyncio.run(
        export_tasks(
            season=args.season,
            base_out_dir=args.out_dir,
            project_filter=project_filter,
            limit_per_project=args.limit_per_project,
        )
    )


if __name__ == "__main__":
    main()
