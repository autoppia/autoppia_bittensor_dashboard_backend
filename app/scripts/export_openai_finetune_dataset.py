from __future__ import annotations

"""
Export a JSONL dataset for OpenAI chat fine-tuning from the dashboard backend DB.

Each JSONL line contains a chat sample where the user provides:
  - URL
  - Task prompt

and the assistant returns a JSON array of simplified actions:
  [{"type": ..., "selector": ..., "value": ...}, ...]

The script uses the existing async SQLAlchemy setup and models, reading
DATABASE_URL and other settings from the backend's .env via app.config.settings.

Usage:
  cd autoppia_bittensor_dashboard_backend
  python -m app.scripts.export_openai_finetune_dataset \
    --output openai_finetune_dataset.jsonl \
    --min-score 0.0 \
    --max-actions 50 \
    --batch-size 1000

Notes:
  - By default, includes all tasks that have at least one solution with actions.
  - For quality, you can set --min-score (e.g. 0.5) to only export solutions
    whose best evaluation score per task meets the threshold.
  - The output format follows OpenAI chat fine-tuning requirements: each line
    is a JSON object with a "messages" array of role/content entries.
  - We keep assistant content to a pure JSON array (no prose), which is what
    you want the model to learn to return.
"""

import argparse
import asyncio
import json
from typing import Any, Dict, Iterable, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload, defer

from app.config import settings
from app.db.session import AsyncSessionLocal
from app.db.models import TaskORM, TaskSolutionORM, EvaluationORM


SYSTEM_PROMPT_DEFAULT = (
    "You are a web automation planner. Given a target URL and a natural-language task, "
    "return only a JSON array describing the minimal sequence of actions to complete the task. "
    "Use the schema: [{\"type\": \"navigate|click|input|type|search|extract|submit|open_tab|close_tab|wait|scroll|screenshot|other\", "
    "\"selector\": string|null, \"value\": string|null}]. Do not include timestamps or explanations; "
    "respond with the JSON array only."
)


def _normalize_action_type(raw_type: Optional[str]) -> str:
    if not raw_type:
        return "other"
    value = str(raw_type).strip().lower()
    value = value.replace("action", "").replace("-", "_")

    alias_map = {
        # Navigation
        "navigate": "navigate",
        "navigation": "navigate",
        "goto": "navigate",
        "visit": "navigate",
        "load": "navigate",
        # Clicks / pointer
        "click": "click",
        "doubleclick": "click",
        "rightclick": "click",
        "middleclick": "click",
        "tripleclick": "click",
        "mousedown": "click",
        "mouseup": "click",
        "mousemove": "click",
        "hover": "click",
        "tap": "click",
        "press": "click",
        "select": "click",
        # Input/typing (use a single semantic label)
        "type": "input",
        "input": "input",
        "fill": "input",
        "type_text": "input",
        "enter": "input",
        "write": "input",
        "text": "input",
        "sendkeysiwa": "input",
        "holdkey": "input",
        # Search
        "search": "search",
        "find": "search",
        "lookup": "search",
        # Extract/scrape
        "extract": "extract",
        "scrape": "extract",
        "get": "extract",
        "read": "extract",
        "parse": "extract",
        "getdropdownoptions": "extract",
        "assert": "extract",
        # Submit
        "submit": "submit",
        "form_submit": "submit",
        "send": "submit",
        "post": "submit",
        "selectdropdownoption": "submit",
        # Tabs
        "open_tab": "open_tab",
        "open_new_tab": "open_tab",
        "new_tab": "open_tab",
        "close_tab": "close_tab",
        "close_current_tab": "close_tab",
        "close": "close_tab",
        # Wait
        "wait": "wait",
        "pause": "wait",
        "sleep": "wait",
        "delay": "wait",
        "idle": "wait",
        # Scroll
        "scroll": "scroll",
        "scroll_up": "scroll",
        "scroll_down": "scroll",
        "scroll_to": "scroll",
        # Screenshot
        "screenshot": "screenshot",
        "capture": "screenshot",
        "snap": "screenshot",
        "photo": "screenshot",
        # Other
        "draganddrop": "click",
        "leftclickdrag": "click",
        "undefined": "other",
    }
    return alias_map.get(value, value if value in {
        "navigate", "click", "input", "type", "search", "extract", "submit",
        "open_tab", "close_tab", "wait", "scroll", "screenshot", "other"
    } else "other")


def _extract_action_fields(action: Any) -> Tuple[str, Optional[str], Optional[str]]:
    """Return (type, selector, value) from a raw action object or dict."""
    # Support dicts or model-like objects
    action_dict: Dict[str, Any]
    if isinstance(action, dict):
        action_dict = action
    elif hasattr(action, "model_dump"):
        try:
            action_dict = action.model_dump()  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            action_dict = {}
    elif hasattr(action, "dict"):
        try:
            action_dict = action.dict()  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            action_dict = {}
    else:
        action_dict = {}

    attributes = action_dict.get("attributes") or {}
    if not isinstance(attributes, dict):
        attributes = {}

    raw_type = action_dict.get("type") or attributes.get("type")
    act_type = _normalize_action_type(raw_type)

    # Selector may be nested
    selector: Optional[str] = None
    sel_obj = action_dict.get("selector")
    if isinstance(sel_obj, dict):
        selector = sel_obj.get("value") or str(sel_obj)
    elif sel_obj is not None:
        selector = str(sel_obj)
    if not selector and isinstance(attributes.get("selector"), (str, dict)):
        sel_attr = attributes.get("selector")
        if isinstance(sel_attr, dict):
            selector = sel_attr.get("value") or str(sel_attr)
        else:
            selector = str(sel_attr)

    # Value preference: url -> value -> text -> attribute variants
    value: Optional[str] = (
        action_dict.get("url")
        or action_dict.get("value")
        or action_dict.get("text")
        or attributes.get("url")
        or attributes.get("value")
        or attributes.get("text")
        or attributes.get("label")
        or attributes.get("field")
        or attributes.get("for")
    )
    if value is not None:
        value = str(value)

    return act_type, selector, value


def _select_best_evaluation(task_row: TaskORM) -> Optional[EvaluationORM]:
    evaluations: List[EvaluationORM] = list(task_row.evaluations or [])
    if not evaluations:
        return None
    # Prefer the highest final_score; tie-breaker by earliest id ascending
    evaluations.sort(key=lambda e: (-(e.final_score or 0.0), e.id or 0))
    return evaluations[0]


def _matching_solution(task_row: TaskORM, solution_id: Optional[str]) -> Optional[TaskSolutionORM]:
    solutions: List[TaskSolutionORM] = list(task_row.task_solutions or [])
    if not solutions:
        return None
    if solution_id:
        for s in solutions:
            if s.solution_id == solution_id:
                return s
    # Fallback: first solution row
    return solutions[0]


async def _iter_task_batches(session: AsyncSession, batch_size: int) -> Iterable[List[TaskORM]]:
    offset = 0
    while True:
        stmt = (
            select(TaskORM)
            .options(
                selectinload(TaskORM.task_solutions),
                selectinload(TaskORM.evaluations).options(
                    defer(EvaluationORM.feedback),
                    defer(EvaluationORM.gif_recording),
                    defer(EvaluationORM.meta),
                ).selectinload(
                    EvaluationORM.execution_history_record
                ),
            )
            .order_by(TaskORM.id.asc())
            .offset(offset)
            .limit(batch_size)
        )
        result = await session.scalars(stmt)
        rows = result.all()
        if not rows:
            break
        yield rows
        offset += batch_size


def _build_chat_record(
    *,
    url: str,
    prompt: str,
    actions: List[Dict[str, Any]],
    system_prompt: str,
) -> Dict[str, Any]:
    user_content = f"URL: {url}\nTask: {prompt}"
    assistant_content = json.dumps(actions, ensure_ascii=False)
    return {
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
            {"role": "assistant", "content": assistant_content},
        ]
    }


async def export_dataset(
    output_path: str,
    *,
    min_score: float = 0.0,
    max_actions: Optional[int] = None,
    batch_size: int = 1000,
    system_prompt: str = SYSTEM_PROMPT_DEFAULT,
) -> int:
    """Export dataset and return number of examples written."""
    count = 0
    async with AsyncSessionLocal() as session:
        with open(output_path, "w", encoding="utf-8") as f:
            async for batch in _iter_task_batches(session, batch_size):
                for task_row in batch:
                    best_eval = _select_best_evaluation(task_row)
                    if best_eval and best_eval.final_score is not None:
                        if best_eval.final_score < float(min_score):
                            continue

                    solution_row = _matching_solution(task_row, getattr(best_eval, "task_solution_id", None))
                    if not solution_row:
                        continue

                    raw_actions = list(solution_row.actions or [])
                    if not raw_actions:
                        continue

                    simplified: List[Dict[str, Any]] = []
                    for raw in raw_actions:
                        a_type, selector, value = _extract_action_fields(raw)
                        entry: Dict[str, Any] = {"type": a_type}
                        if selector is not None:
                            entry["selector"] = selector
                        if value is not None:
                            entry["value"] = value
                        simplified.append(entry)
                        if max_actions and len(simplified) >= max_actions:
                            break

                    if not simplified:
                        continue

                    record = _build_chat_record(
                        url=task_row.url,
                        prompt=task_row.prompt,
                        actions=simplified,
                        system_prompt=system_prompt,
                    )
                    f.write(json.dumps(record, ensure_ascii=False) + "\n")
                    count += 1

    return count


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export OpenAI fine-tune chat dataset from DB")
    parser.add_argument(
        "--output",
        default="openai_finetune_dataset.jsonl",
        help="Path to output JSONL file",
    )
    parser.add_argument(
        "--min-score",
        type=float,
        default=0.0,
        help="Minimum evaluation final_score required to include a task (default: 0.0)",
    )
    parser.add_argument(
        "--max-actions",
        type=int,
        default=None,
        help="Max actions to include per example (default: no limit)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=1000,
        help="DB fetch batch size (default: 1000)",
    )
    parser.add_argument(
        "--system-prompt",
        type=str,
        default=SYSTEM_PROMPT_DEFAULT,
        help="Custom system prompt for the chat dataset",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    # Touch settings to ensure .env is loaded and DB URL normalized
    _ = settings.DATABASE_URL  # noqa: F841
    total = asyncio.run(
        export_dataset(
            output_path=args.output,
            min_score=float(args.min_score),
            max_actions=int(args.max_actions) if args.max_actions is not None else None,
            batch_size=int(args.batch_size),
            system_prompt=str(args.system_prompt),
        )
    )
    print(f"Wrote {total} training examples to {args.output}")


if __name__ == "__main__":
    main()
