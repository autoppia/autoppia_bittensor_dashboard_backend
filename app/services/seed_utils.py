from __future__ import annotations

import random
import re
import time
from dataclasses import dataclass
from typing import Any, Dict, List

import httpx
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.data import get_validator_metadata
from app.db.models import (
    AgentEvaluationRunORM,
    EvaluationResultORM,
    RoundORM,
    TaskORM,
    TaskSolutionORM,
)
from app.db.session import AsyncSessionLocal
from app.main import app as fastapi_app
from app.models.core import (
    Action,
    AgentEvaluationRun,
    EvaluationResult,
    MinerInfo,
    ValidatorRound,
    ValidatorRoundSubmissionRequest,
    Task,
    TaskSolution,
    TestResult,
    ValidatorInfo,
)
from app.services.validator_storage import (
    PersistenceResult,
    RoundConflictError,
    RoundPersistenceService,
)

MAX_SYNTHETIC_MINERS = 255


@dataclass
class SeededRun:
    agent_run: AgentEvaluationRun
    tasks: List[Task]
    task_solutions: Dict[str, TaskSolution]
    evaluations: Dict[str, EvaluationResult]


@dataclass(frozen=True)
class SeedTaskTemplate:
    website_name: str
    website_slug: str
    url: str
    use_case_label: str
    use_case_slug: str
    prompt: str
    success_criteria: str
    actions: List[Dict[str, Any]]


SEED_TASK_LIBRARY: List[SeedTaskTemplate] = [
    SeedTaskTemplate(
        website_name="Autoppia Cinema",
        website_slug="autocinema",
        url="http://autocinema.autoppia.com/films",
        use_case_label="Search Film",
        use_case_slug="search-film",
        prompt="Look for the film 'The Shawshank Redemption'.",
        success_criteria="Results include The Shawshank Redemption in the list.",
        actions=[
            {
                "type": "navigate",
                "url": "http://autocinema.autoppia.com/films",
                "status": "completed",
                "context": "films_catalog",
            },
            {
                "type": "type",
                "selector": "input[name='search']",
                "value": "The Shawshank Redemption",
                "field": "search",
            },
            {
                "type": "click",
                "selector": "button[type='submit']",
                "text": "Search",
                "role": "submit",
            },
            {
                "type": "wait",
                "for": "results",
                "durationSeconds": 1.8,
            },
            {
                "type": "screenshot",
                "path": "/screens/autocinema/search-results.png",
                "label": "Search results hero",
            },
        ],
    ),
    SeedTaskTemplate(
        website_name="Autoppia Cinema",
        website_slug="autocinema",
        url="http://autocinema.autoppia.com/films/inception",
        use_case_label="Add Comment",
        use_case_slug="add-comment",
        prompt="Navigate to a movie and add a comment about Inception.",
        success_criteria="Comment containing the word 'Inception' is visible in the thread.",
        actions=[
            {
                "type": "navigate",
                "url": "http://autocinema.autoppia.com/films/inception",
                "status": "completed",
                "context": "film_detail",
            },
            {
                "type": "scroll",
                "direction": "down",
                "amount": 600,
            },
            {
                "type": "type",
                "selector": "textarea[name='comment']",
                "value": "Inception is still a masterpiece!",
                "field": "comment",
            },
            {
                "type": "click",
                "selector": "button[type='submit']",
                "text": "Post Comment",
                "role": "submit",
            },
            {
                "type": "wait",
                "for": "comment_stream",
                "durationSeconds": 2.2,
            },
        ],
    ),
    SeedTaskTemplate(
        website_name="Autoppia Books",
        website_slug="autobooks",
        url="http://autobooks.autoppia.com/admin/books/new",
        use_case_label="Add Book",
        use_case_slug="add-book",
        prompt="Once logged in as 'reader1' with the password 'PASSWORD', add the book 'The Midnight Library' with a page_count under 320 pages.",
        success_criteria="The book 'The Midnight Library' exists in the catalog with page count under 320.",
        actions=[
            {
                "type": "navigate",
                "url": "http://autobooks.autoppia.com/admin/books/new",
                "status": "completed",
                "context": "admin_books",
            },
            {
                "type": "type",
                "selector": "input[name='title']",
                "value": "The Midnight Library",
                "field": "title",
            },
            {
                "type": "type",
                "selector": "input[name='author']",
                "value": "Matt Haig",
                "field": "author",
            },
            {
                "type": "type",
                "selector": "input[name='page_count']",
                "value": "304",
                "field": "page_count",
            },
            {
                "type": "click",
                "selector": "button[data-testid='save-book']",
                "text": "Save Book",
                "role": "submit",
            },
        ],
    ),
    SeedTaskTemplate(
        website_name="Autoppia Books",
        website_slug="autobooks",
        url="http://autobooks.autoppia.com/shop/checkout",
        use_case_label="Purchase Book",
        use_case_slug="purchase-book",
        prompt="Log in with username: buyer1 and password: buy123. Then proceed to buy a book in the 'Education' genre with more than 700 pages.",
        success_criteria="Checkout reflects a purchase in the Education genre with pages greater than 700.",
        actions=[
            {
                "type": "navigate",
                "url": "http://autobooks.autoppia.com/books/education",
                "status": "completed",
                "context": "category_listing",
            },
            {
                "type": "click",
                "selector": "button[data-testid='add-to-cart']",
                "text": "Add to Cart",
                "role": "add_to_cart",
            },
            {
                "type": "navigate",
                "url": "http://autobooks.autoppia.com/cart",
                "status": "completed",
                "context": "cart_overview",
            },
            {
                "type": "click",
                "selector": "a[href='/shop/checkout']",
                "text": "Checkout",
                "role": "checkout",
            },
            {
                "type": "wait",
                "for": "checkout_page",
                "durationSeconds": 1.4,
            },
        ],
    ),
    SeedTaskTemplate(
        website_name="Autozone",
        website_slug="autozone",
        url="http://autozone.autoppia.com/search",
        use_case_label="Search Product",
        use_case_slug="search-product",
        prompt="Find products matching 'Espresso Machine'.",
        success_criteria="Search results list an Espresso Machine product card.",
        actions=[
            {
                "type": "navigate",
                "url": "http://autozone.autoppia.com/search",
                "status": "completed",
                "context": "search_page",
            },
            {
                "type": "type",
                "selector": "input[name='query']",
                "value": "Espresso Machine",
                "field": "query",
            },
            {
                "type": "click",
                "selector": "button[type='submit']",
                "text": "Search",
                "role": "submit",
            },
            {
                "type": "wait",
                "for": "product_grid",
                "durationSeconds": 1.6,
            },
        ],
    ),
    SeedTaskTemplate(
        website_name="Autozone",
        website_slug="autozone",
        url="http://autozone.autoppia.com/products/espresso-machine",
        use_case_label="Add to Cart",
        use_case_slug="add-to-cart",
        prompt="Add Air Fryer to my cart.",
        success_criteria="Shopping cart contains the Air Fryer product with quantity 1 or more.",
        actions=[
            {
                "type": "navigate",
                "url": "http://autozone.autoppia.com/products/espresso-machine",
                "status": "completed",
                "context": "product_detail",
            },
            {
                "type": "click",
                "selector": "button[data-testid='add-to-cart']",
                "text": "Add to Cart",
                "role": "add_to_cart",
            },
            {
                "type": "wait",
                "for": "cart_sidebar",
                "durationSeconds": 1.0,
            },
            {
                "type": "screenshot",
                "path": "/screens/autozone/cart.png",
                "label": "Cart with Espresso Machine",
            },
        ],
    ),
    SeedTaskTemplate(
        website_name="AutoDining",
        website_slug="autodining",
        url="http://autodining.autoppia.com/restaurants/the-royal-dine",
        use_case_label="Book Restaurant",
        use_case_slug="book-restaurant",
        prompt="I'd like to book a table at the restaurant which name 'The Royal Dine' for 2 people on 2025-05-16 at 1:30 PM.",
        success_criteria="Reservation confirmation for The Royal Dine at 1:30 PM is displayed.",
        actions=[
            {
                "type": "navigate",
                "url": "http://autodining.autoppia.com/restaurants/the-royal-dine",
                "status": "completed",
                "context": "restaurant_detail",
            },
            {
                "type": "click",
                "selector": "button[data-testid='open-reservation']",
                "text": "Book Table",
                "role": "open_modal",
            },
            {
                "type": "type",
                "selector": "input[name='reservation_date']",
                "value": "2025-05-16",
                "field": "reservation_date",
            },
            {
                "type": "type",
                "selector": "input[name='reservation_time']",
                "value": "13:30",
                "field": "reservation_time",
            },
            {
                "type": "click",
                "selector": "button[data-testid='confirm-reservation']",
                "text": "Confirm Reservation",
                "role": "submit",
            },
        ],
    ),
    SeedTaskTemplate(
        website_name="AutoCRM",
        website_slug="autocrm",
        url="http://autocrm.autoppia.com/matters/new",
        use_case_label="Add New Matter",
        use_case_slug="add-new-matter",
        prompt="Create a matter named 'New Matter', with client 'Acme Co.' and status 'Active'.",
        success_criteria="Matter list shows 'New Matter' for client 'Acme Co.' with status Active.",
        actions=[
            {
                "type": "navigate",
                "url": "http://autocrm.autoppia.com/matters/new",
                "status": "completed",
                "context": "matter_form",
            },
            {
                "type": "type",
                "selector": "input[name='matter_name']",
                "value": "New Matter",
                "field": "matter_name",
            },
            {
                "type": "type",
                "selector": "input[name='client_name']",
                "value": "Acme Co.",
                "field": "client_name",
            },
            {
                "type": "click",
                "selector": "select[name='status'] option[value='active']",
                "text": "Active",
                "role": "select",
            },
            {
                "type": "click",
                "selector": "button[data-testid='save-matter']",
                "text": "Create Matter",
                "role": "submit",
            },
        ],
    ),
    SeedTaskTemplate(
        website_name="AutoDelivery",
        website_slug="autodelivery",
        url="http://autodelivery.autoppia.com/restaurants/pizza-palace",
        use_case_label="Add to Cart",
        use_case_slug="add-to-cart-delivery",
        prompt="Add when item equals 'Margherita Pizza' and size equals 'Large' to my cart.",
        success_criteria="Cart modal reflects Margherita Pizza (Large) with quantity 1 or more.",
        actions=[
            {
                "type": "navigate",
                "url": "http://autodelivery.autoppia.com/restaurants/pizza-palace",
                "status": "completed",
                "context": "restaurant_menu",
            },
            {
                "type": "click",
                "selector": "button[data-testid='add-margherita-large']",
                "text": "Add Margherita Pizza",
                "role": "open_modal",
            },
            {
                "type": "click",
                "selector": "button[data-testid='confirm-add-to-cart']",
                "text": "Add to Cart",
                "role": "submit",
            },
            {
                "type": "screenshot",
                "path": "/screens/autodelivery/cart.png",
                "label": "Delivery cart modal",
            },
        ],
    ),
    SeedTaskTemplate(
        website_name="AutoMail",
        website_slug="automail",
        url="http://automail.autoppia.com/dashboard/campaigns/new",
        use_case_label="Create Campaign",
        use_case_slug="create-campaign",
        prompt="Compose a new promotional email campaign for the product launch of Atlas.",
        success_criteria="Campaign draft titled 'Atlas Launch' appears in the dashboard.",
        actions=[
            {
                "type": "navigate",
                "url": "http://automail.autoppia.com/dashboard/campaigns/new",
                "status": "completed",
                "context": "campaign_builder",
            },
            {
                "type": "type",
                "selector": "input[name='campaign_title']",
                "value": "Atlas Launch",
                "field": "campaign_title",
            },
            {
                "type": "type",
                "selector": "textarea[name='email_body']",
                "value": "Discover the new Atlas productivity suite launch this week.",
                "field": "email_body",
            },
            {
                "type": "click",
                "selector": "button[data-testid='save-draft']",
                "text": "Save Draft",
                "role": "submit",
            },
        ],
    ),
]


def _template_for_index(index: int) -> SeedTaskTemplate:
    if not SEED_TASK_LIBRARY:
        raise RuntimeError("Seed task library is empty; unable to seed tasks.")
    return SEED_TASK_LIBRARY[index % len(SEED_TASK_LIBRARY)]


def _build_validator_info(validator_uid: int) -> ValidatorInfo:
    """Populate validator metadata using the static directory fallback."""
    metadata = get_validator_metadata(validator_uid)
    return ValidatorInfo(
        uid=validator_uid,
        hotkey=metadata.get("hotkey", f"validator_hotkey_{validator_uid}"),
        coldkey=metadata.get("coldkey"),
        stake=float(metadata.get("stake") or 0.0),
        vtrust=float(metadata.get("vtrust") or 0.0),
        name=metadata.get("name"),
        version=metadata.get("version"),
    )


def _build_seed_request(
    validator_round_id: str,
    validator_uid: int,
    num_tasks: int,
    num_miners: int,
    round_number: int,
) -> ValidatorRoundSubmissionRequest:
    if num_tasks <= 0:
        raise ValueError("num_tasks must be greater than zero")
    if num_miners <= 0:
        raise ValueError("num_miners must be greater than zero")
    if num_miners > MAX_SYNTHETIC_MINERS:
        raise ValueError(f"num_miners must be <= {MAX_SYNTHETIC_MINERS}")

    now = time.time()
    validator_info = _build_validator_info(validator_uid)

    miners: List[MinerInfo] = []
    agent_runs: List[AgentEvaluationRun] = []
    tasks: List[Task] = []
    task_solutions: List[TaskSolution] = []
    evaluation_results: List[EvaluationResult] = []

    weights: Dict[str, float] = {}
    base_weight = round(1.0 / num_miners, 4)
    run_scores: List[float] = []
    template_index = 0

    def _slug(value: str) -> str:
        text = value.lower()
        text = re.sub(r"[^a-z0-9]+", "-", text)
        return text.strip("-")

    for miner_index in range(num_miners):
        miner_uid = miner_index + 1
        miner = MinerInfo(
            uid=miner_uid,
            hotkey=f"miner_hotkey_{miner_uid}",
            coldkey=f"miner_coldkey_{miner_uid}",
            agent_name=f"Seed Miner {miner_index + 1}",
            agent_image="",
            github=f"https://github.com/autoppia/seed-miner-{miner_uid}",
            is_sota=False,
            description="Synthetic miner generated for validator seeding.",
        )
        miners.append(miner)
        weights[str(miner_uid)] = base_weight

        agent_run_id = f"{validator_round_id}_run_{miner_uid}"
        # Simulate varying miner quality with random scores.
        run_score = round(random.uniform(0.5, 0.98), 4)
        run_scores.append(run_score)

        run_task_ids: List[str] = []
        for task_index in range(num_tasks):
            task_id = f"{agent_run_id}_task_{task_index:03d}"
            run_task_ids.append(task_id)

            template = _template_for_index(template_index)
            template_index += 1

            task = Task(
                task_id=task_id,
                validator_round_id=validator_round_id,
                scope="local",
                is_web_real=False,
                web_project_id=None,
                url=template.url,
                prompt=template.prompt,
                html=f"<html><body>{template.website_name} task - {template.use_case_label}</body></html>",
                clean_html=f"<html><body>{template.website_name} task - {template.use_case_label}</body></html>",
                interactive_elements=None,
                screenshot=None,
                screenshot_description=None,
                specifications={"browser": "chromium", "website": template.website_slug},
                tests=[],
                milestones=None,
                relevant_data={
                    "validator_uid": validator_uid,
                    "miner_uid": miner_uid,
                    "website": template.website_slug,
                    "website_name": template.website_name,
                    "use_case": template.use_case_slug,
                },
                success_criteria=template.success_criteria,
                use_case={"name": template.use_case_label, "slug": template.use_case_slug},
                should_record=False,
            )
            tasks.append(task)

            action_models: List[Action] = []
            for action_payload in template.actions:
                attributes = {key: value for key, value in action_payload.items() if key != "type"}
                attributes.setdefault("status", "completed")
                action_models.append(Action(type=action_payload["type"], attributes=attributes))

            solution = TaskSolution(
                solution_id=f"{task_id}_solution",
                task_id=task_id,
                validator_round_id=validator_round_id,
                agent_run_id=agent_run_id,
                miner_uid=miner_uid,
                validator_uid=validator_uid,
                actions=action_models,
                web_agent_id=f"seed-agent-{miner_uid}",
                recording=None,
            )
            task_solutions.append(solution)

            execution_history = [
                f"{payload['type']} -> {payload.get('selector') or payload.get('url') or payload.get('text') or payload.get('label') or 'completed'}"
                for payload in template.actions
            ]

            evaluation = EvaluationResult(
                evaluation_id=f"{task_id}_evaluation",
                task_id=task_id,
                task_solution_id=solution.solution_id,
                validator_round_id=validator_round_id,
                agent_run_id=agent_run_id,
                miner_uid=miner_uid,
                validator_uid=validator_uid,
                final_score=run_score,
                test_results_matrix=[
                    [
                        TestResult(
                            success=True,
                            extra_data={
                                "task_id": task_id,
                                "website": template.website_slug,
                                "use_case": template.use_case_slug,
                            },
                        )
                    ]
                ],
                execution_history=execution_history,
                feedback=None,
                web_agent_id=solution.web_agent_id,
                raw_score=run_score,
                evaluation_time=5.0,
                stats=None,
                gif_recording=None,
            )
            evaluation_results.append(evaluation)

        agent_runs.append(
            AgentEvaluationRun(
                agent_run_id=agent_run_id,
                validator_round_id=validator_round_id,
                validator_uid=validator_uid,
                miner_uid=miner_uid,
                miner_info=miner,
                is_sota=False,
                version="1.0.0",
                task_ids=run_task_ids,
                started_at=now - 120 + miner_index * 2,
                ended_at=now - 60 + miner_index * 2,
                elapsed_sec=60.0,
                avg_eval_score=run_score,
                avg_execution_time=5.0,
                avg_reward=run_score,
                total_reward=run_score * num_tasks,
                n_tasks_total=num_tasks,
                n_tasks_completed=num_tasks,
                n_tasks_failed=0,
                rank=miner_index + 1,
                weight=weights.get(str(miner_uid), 0.0),
                metadata={"seeded": True},
            )
        )

    ranking_indices = sorted(range(len(run_scores)), key=lambda idx: run_scores[idx], reverse=True)
    rank_by_index = {idx: rank for rank, idx in enumerate(ranking_indices, start=1)}

    for idx, run in enumerate(agent_runs):
        run.rank = rank_by_index.get(idx, idx + 1)

    winners = [
        {
            "miner_uid": miners[idx].uid,
            "rank": rank_by_index.get(idx, idx + 1),
            "score": run_scores[idx],
        }
        for idx in ranking_indices[: min(num_miners, 3)]
    ]

    sota_agents: List[MinerInfo] = [
        MinerInfo(
            uid=-(index + 1),
            hotkey=f"sota_{name.lower()}",
            coldkey=f"sota_{name.lower()}_cold",
            agent_name=f"{name} Benchmark",
            agent_image="",
            github=f"https://github.com/autoppia/sota-{name.lower()}",
            is_sota=True,
            description=f"Synthetic {name} benchmark agent.",
            provider=name.lower(),
        )
        for index, name in enumerate(["OpenAI", "Anthropic", "Browser Use"])
    ]

    for agent in sota_agents:
        weights[str(agent.uid)] = 0.0

        agent_run_id = f"{validator_round_id}_sota_{_slug(agent.agent_name or agent.hotkey or str(agent.uid))}"
        benchmark_score = round(random.uniform(0.65, 0.99), 4)
        run_task_ids: List[str] = []

        for task_index in range(num_tasks):
            task_id = f"{agent_run_id}_task_{task_index:03d}"
            run_task_ids.append(task_id)

            template = _template_for_index(template_index)
            template_index += 1

            task = Task(
                task_id=task_id,
                validator_round_id=validator_round_id,
                scope="local",
                is_web_real=False,
                web_project_id=None,
                url=template.url,
                prompt=f"{template.prompt} (benchmark run)",
                html=f"<html><body>{template.website_name} benchmark task - {template.use_case_label}</body></html>",
                clean_html=f"<html><body>{template.website_name} benchmark task - {template.use_case_label}</body></html>",
                interactive_elements=None,
                screenshot=None,
                screenshot_description=None,
                specifications={"browser": "chromium", "website": template.website_slug},
                tests=[],
                milestones=None,
                relevant_data={
                    "validator_uid": validator_uid,
                    "benchmark": True,
                    "website": template.website_slug,
                    "website_name": template.website_name,
                    "use_case": template.use_case_slug,
                },
                success_criteria=template.success_criteria,
                use_case={"name": template.use_case_label, "slug": template.use_case_slug},
                should_record=False,
            )
            tasks.append(task)

            action_models: List[Action] = []
            for action_payload in template.actions:
                attributes = {key: value for key, value in action_payload.items() if key != "type"}
                attributes.setdefault("status", "completed")
                attributes.setdefault("benchmark", True)
                attributes.setdefault("source", agent.agent_name or "Benchmark")
                action_models.append(Action(type=action_payload["type"], attributes=attributes))

            solution = TaskSolution(
                solution_id=f"{task_id}_solution",
                task_id=task_id,
                validator_round_id=validator_round_id,
                agent_run_id=agent_run_id,
                miner_uid=agent.uid,
                validator_uid=validator_uid,
                actions=action_models,
                web_agent_id=f"seed-sota-{_slug(agent.agent_name or str(agent.uid))}",
                recording=None,
            )
            task_solutions.append(solution)

            execution_history = [
                f"{payload['type']} -> {payload.get('selector') or payload.get('url') or payload.get('text') or payload.get('label') or 'completed'}"
                for payload in template.actions
            ]

            evaluation = EvaluationResult(
                evaluation_id=f"{task_id}_evaluation",
                task_id=task_id,
                task_solution_id=solution.solution_id,
                validator_round_id=validator_round_id,
                agent_run_id=agent_run_id,
                miner_uid=agent.uid,
                validator_uid=validator_uid,
                final_score=benchmark_score,
                test_results_matrix=[
                    [
                        TestResult(
                            success=True,
                            extra_data={
                                "task_id": task_id,
                                "website": template.website_slug,
                                "use_case": template.use_case_slug,
                                "benchmark": True,
                            },
                        )
                    ]
                ],
                execution_history=execution_history,
                feedback=None,
                web_agent_id=solution.web_agent_id,
                raw_score=benchmark_score,
                evaluation_time=4.0,
                stats=None,
                gif_recording=None,
            )
            evaluation_results.append(evaluation)

        agent_runs.append(
            AgentEvaluationRun(
                agent_run_id=agent_run_id,
                validator_round_id=validator_round_id,
                validator_uid=validator_uid,
                miner_uid=agent.uid,
                miner_info=agent,
                is_sota=True,
                version="1.0.0",
                task_ids=run_task_ids,
                started_at=now - 150,
                ended_at=now - 120,
                elapsed_sec=30.0,
                avg_eval_score=benchmark_score,
                avg_execution_time=4.0,
                avg_reward=benchmark_score,
                total_reward=benchmark_score * num_tasks,
                n_tasks_total=num_tasks,
                n_tasks_completed=num_tasks,
                n_tasks_failed=0,
                rank=None,
                weight=0.0,
                metadata={"seeded": True, "benchmark": True},
            )
        )

    round_model = ValidatorRound(
        validator_round_id=validator_round_id,
        round_number=round_number,
        validators=[validator_info],
        validator_info=validator_info,
        start_block=10_000 + int(now) % 1_000,
        start_epoch=int(now) % 5_000,
        end_block=10_000 + int(now) % 1_000 + num_tasks * num_miners,
        end_epoch=int(now) % 5_000 + num_tasks,
        started_at=now - 180,
        ended_at=now,
        elapsed_sec=180.0,
        max_epochs=20,
        max_blocks=360,
        n_tasks=num_tasks,
        n_miners=num_miners,
        n_winners=len(winners) if winners else 1,
        miners=miners,
        sota_agents=sota_agents,
        winners=winners,
        winner_scores=[winner["score"] for winner in winners],
        weights=weights,
        average_score=round(sum(run_scores) / len(run_scores), 4),
        top_score=max(run_scores),
        status="completed",
    )

    return ValidatorRoundSubmissionRequest(
        round=round_model,
        agent_evaluation_runs=agent_runs,
        tasks=tasks,
        task_solutions=task_solutions,
        evaluation_results=evaluation_results,
    )


def _group_run_artifacts(payload: ValidatorRoundSubmissionRequest) -> List[SeededRun]:
    """Bundle the seeded entities per agent run for progressive ingestion."""
    solutions_by_task = {solution.task_id: solution for solution in payload.task_solutions}
    evaluations_by_task = {evaluation.task_id: evaluation for evaluation in payload.evaluation_results}

    runs: List[SeededRun] = []
    for agent_run in payload.agent_evaluation_runs:
        task_ids = set(agent_run.task_ids or [])
        run_tasks = [task for task in payload.tasks if task.task_id in task_ids]

        run_solutions = {
            task.task_id: solutions_by_task[task.task_id]
            for task in run_tasks
            if task.task_id in solutions_by_task
        }
        run_evaluations = {
            task.task_id: evaluations_by_task[task.task_id]
            for task in run_tasks
            if task.task_id in evaluations_by_task
        }

        runs.append(
            SeededRun(
                agent_run=agent_run,
                tasks=run_tasks,
                task_solutions=run_solutions,
                evaluations=run_evaluations,
            )
        )
    return runs


async def _determine_next_round_number() -> int:
    """
    Determine the next logical round number across all persisted rounds.

    Returns:
        Sequential round number starting at 1.
    """
    async with AsyncSessionLocal() as session:
        stmt = select(RoundORM).order_by(RoundORM.id.desc())
        result = await session.scalars(stmt)
        for round_row in result:
            number = RoundPersistenceService._extract_round_number(round_row.data)
            if number is not None:
                return number + 1
    return 1


async def _ensure_response(response: httpx.Response, context: str) -> dict:
    if response.status_code >= 400:
        raise RuntimeError(f"{context} failed with {response.status_code}: {response.text}")
    return response.json()


async def _guard_duplicate_round(validator_uid: int, round_number: int) -> None:
    async with AsyncSessionLocal() as session:
        service = RoundPersistenceService(session)
        try:
            await service.ensure_unique_round_number(validator_uid, round_number)
        except RoundConflictError as exc:
            raise RuntimeError(str(exc)) from exc


async def seed_validator_round(
    validator_round_id: str,
    validator_uid: int,
    num_tasks: int,
    num_miners: int,
    *,
    client: AsyncClient | None = None,
    round_number: int | None = None,
) -> PersistenceResult:
    """
    Seed the database with a synthetic validator round using the public ingestion endpoints.

    Returns:
        PersistenceResult describing the entities stored in the database.
    """
    if round_number is None:
        round_number = await _determine_next_round_number()

    payload = _build_seed_request(
        validator_round_id,
        validator_uid,
        num_tasks,
        num_miners,
        round_number,
    )

    await _guard_duplicate_round(validator_uid, round_number)

    runs = _group_run_artifacts(payload)

    owns_client = client is None
    if owns_client:
        transport = ASGITransport(app=fastapi_app)
        client = AsyncClient(transport=transport, base_url="http://seed-server")

    try:
        await _ensure_response(
            await client.post(
                "/api/v1/validator-rounds/start",
                json={
                    "validator_round_id": validator_round_id,
                    "round": payload.round.model_dump(mode="json", exclude_none=True),
                },
            ),
            "start_round",
        )

        await _ensure_response(
            await client.post(
                f"/api/v1/validator-rounds/{validator_round_id}/tasks",
                json={
                    "tasks": [
                        task.model_dump(mode="json", exclude_none=True) for task in payload.tasks
                    ],
                },
            ),
            "set_tasks",
        )

        for run in runs:
            agent_run_json = run.agent_run.model_dump(mode="json", exclude_none=True)
            await _ensure_response(
                await client.post(
                    f"/api/v1/validator-rounds/{validator_round_id}/agent-runs/start",
                    json={"agent_run": agent_run_json},
                ),
                f"start_agent_run {run.agent_run.agent_run_id}",
            )

            for task in run.tasks:
                solution = run.task_solutions[task.task_id]
                evaluation = run.evaluations[task.task_id]
                await _ensure_response(
                    await client.post(
                        f"/api/v1/validator-rounds/{validator_round_id}"
                        f"/agent-runs/{run.agent_run.agent_run_id}/evaluations",
                        json={
                            "task": task.model_dump(mode="json", exclude_none=True),
                            "task_solution": solution.nested_model_dump(mode="json", exclude_none=True),
                            "evaluation_result": evaluation.model_dump(mode="json", exclude_none=True),
                        },
                    ),
                    f"add_evaluation {evaluation.evaluation_id}",
                )

        weights_payload = {str(k): v for k, v in (payload.round.weights or {}).items()}
        await _ensure_response(
            await client.post(
                f"/api/v1/validator-rounds/{validator_round_id}/finish",
                json={
                    "status": payload.round.status,
                    "winners": payload.round.winners or [],
                    "winner_scores": payload.round.winner_scores,
                    "weights": weights_payload,
                    "ended_at": payload.round.ended_at,
                    "summary": {
                        "tasks": len(payload.tasks),
                        "evaluations": len(payload.evaluation_results),
                        "agent_runs": len(payload.agent_evaluation_runs),
                    },
                },
            ),
            "finish_round",
        )
    finally:
        if owns_client and client is not None:
            await client.aclose()

    async with AsyncSessionLocal() as session:
        round_row = await session.scalar(
            select(RoundORM).where(RoundORM.validator_round_id == validator_round_id)
        )
        if not round_row:
            raise RuntimeError(f"Round {validator_round_id} not found after seeding")

        agent_runs_saved = list(
            await session.scalars(
                select(AgentEvaluationRunORM.agent_run_id).where(
                    AgentEvaluationRunORM.validator_round_id == validator_round_id
                )
            )
        )
        tasks_saved = list(
            await session.scalars(
                select(TaskORM.task_id).where(TaskORM.validator_round_id == validator_round_id)
            )
        )
        task_solutions_saved = list(
            await session.scalars(
                select(TaskSolutionORM.solution_id).where(
                    TaskSolutionORM.validator_round_id == validator_round_id
                )
            )
        )
        evaluations_saved = list(
            await session.scalars(
                select(EvaluationResultORM.evaluation_id).where(
                    EvaluationResultORM.validator_round_id == validator_round_id
                )
            )
        )

    return PersistenceResult(
        validator_uid=round_row.validator_uid or validator_uid,
        saved_entities={
            "round": round_row.validator_round_id,
            "agent_evaluation_runs": agent_runs_saved,
            "tasks": tasks_saved,
            "task_solutions": task_solutions_saved,
            "evaluation_results": evaluations_saved,
        },
    )
