from __future__ import annotations

import logging
import random
import time
import uuid
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import httpx
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.data import VALIDATOR_DIRECTORY
from app.db.models import ValidatorRoundORM
from app.db.session import AsyncSessionLocal
from app.models.core import (
    Action,
    AgentEvaluationRun,
    Evaluation,
    EvaluationResult,
    Miner,
    ValidatorRoundMiner,
    Task,
    TaskSolution,
    TestResult,
    Validator,
    ValidatorRound,
    ValidatorRoundSubmissionRequest,
    ValidatorRoundValidator,
)
from app.services.validator_storage import (
    PersistenceResult,
    RoundConflictError,
    ValidatorRoundPersistenceService,
)

logger = logging.getLogger(__name__)

METAGRAPH_NETUID = 36
MAX_FALLBACK_MINERS = 200


@lru_cache(maxsize=1)
def _get_fastapi_app():
    from app.main import app as fastapi_app

    return fastapi_app


@dataclass(frozen=True)
class ValidatorSeedRecord:
    uid: int
    hotkey: str
    coldkey: Optional[str]
    name: Optional[str]
    image: Optional[str]
    version: Optional[str]


@dataclass(frozen=True)
class MinerSeedRecord:
    uid: int
    hotkey: str
    coldkey: Optional[str]
    name: str
    image: Optional[str]
    provider: Optional[str]
    github: Optional[str]
    description: Optional[str]


@dataclass(frozen=True)
class TaskTemplate:
    website_name: str
    website_slug: str
    url: str
    prompt: str
    success_criteria: str
    use_case_label: str
    use_case_slug: str
    default_actions: List[Dict[str, Any]]


@dataclass
class AgentRunBundle:
    miner_identity: Miner
    miner_snapshot: ValidatorRoundMiner
    agent_run: AgentEvaluationRun
    task_solutions: List[TaskSolution]
    evaluations: List[Evaluation]
    evaluation_results: List[EvaluationResult]
    average_score: float


@dataclass
class SeedPayload:
    validator_identity: Validator
    validator_snapshot: ValidatorRoundValidator
    validator_round: ValidatorRound
    validator_record: ValidatorSeedRecord
    miner_records: List[MinerSeedRecord]
    tasks: List[Task]
    agent_bundles: List[AgentRunBundle]

    @property
    def miner_identities(self) -> List[Miner]:
        return [bundle.miner_identity for bundle in self.agent_bundles]

    @property
    def miner_snapshots(self) -> List[ValidatorRoundMiner]:
        return [bundle.miner_snapshot for bundle in self.agent_bundles]

    @property
    def agent_runs(self) -> List[AgentEvaluationRun]:
        return [bundle.agent_run for bundle in self.agent_bundles]

    @property
    def task_solutions(self) -> List[TaskSolution]:
        return [
            solution
            for bundle in self.agent_bundles
            for solution in bundle.task_solutions
        ]

    @property
    def evaluations(self) -> List[Evaluation]:
        return [
            evaluation
            for bundle in self.agent_bundles
            for evaluation in bundle.evaluations
        ]

    @property
    def evaluation_results(self) -> List[EvaluationResult]:
        return [
            result
            for bundle in self.agent_bundles
            for result in bundle.evaluation_results
        ]


def _try_fetch_metagraph(netuid: int = METAGRAPH_NETUID) -> Dict[int, Tuple[str, Optional[str]]]:
    try:
        import bittensor as bt  # type: ignore
    except Exception as exc:  # pragma: no cover - optional dependency
        logger.info("Bittensor not available for metagraph lookup: %s", exc)
        return {}

    try:
        subtensor = bt.subtensor()  # type: ignore[attr-defined]
        metagraph = subtensor.metagraph(netuid=netuid)
        uids = list(map(int, metagraph.uids))
        hotkeys = [str(hk) for hk in metagraph.hotkeys]
        coldkeys = getattr(metagraph, "coldkeys", None)
        records: Dict[int, Tuple[str, Optional[str]]] = {}
        for index, uid in enumerate(uids):
            coldkey = None
            if coldkeys:
                coldkey = str(coldkeys[index])
            records[uid] = (hotkeys[index], coldkey)
        return records
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Failed to fetch metagraph identities: %s", exc)
        return {}


def _build_validator_seed_records() -> Dict[int, ValidatorSeedRecord]:
    metagraph_records = _try_fetch_metagraph()
    records: Dict[int, ValidatorSeedRecord] = {}

    for uid, metadata in VALIDATOR_DIRECTORY.items():
        meta_hotkey = metadata.get("hotkey")
        meta_coldkey = metadata.get("coldkey")
        metagraph_hotkey, metagraph_coldkey = metagraph_records.get(uid, (meta_hotkey, meta_coldkey))

        if metagraph_hotkey is None:
            metagraph_hotkey = meta_hotkey or f"validator_hotkey_{uid}"

        records[uid] = ValidatorSeedRecord(
            uid=uid,
            hotkey=metagraph_hotkey,
            coldkey=metagraph_coldkey,
            name=metadata.get("name"),
            image=metadata.get("image"),
            version=metadata.get("version"),
        )

    # Include any validators present on-chain but missing in the static directory.
    for uid, (hotkey, coldkey) in metagraph_records.items():
        if uid in records:
            continue
        records[uid] = ValidatorSeedRecord(
            uid=uid,
            hotkey=hotkey,
            coldkey=coldkey,
            name=f"Validator {uid}",
            image=None,
            version=None,
        )

    return records


def _fallback_miner_seed_records(count: int, exclude_uids: set[int]) -> List[MinerSeedRecord]:
    providers = ["TensorOps", "SynapseX", "AutoMiner Labs", "NeuraForge"]
    records: List[MinerSeedRecord] = []
    start_uid = 10_000

    for index in range(count):
        uid = start_uid + index
        if uid in exclude_uids:
            continue
        provider = providers[index % len(providers)]
        records.append(
            MinerSeedRecord(
                uid=uid,
                hotkey=f"mock_miner_hotkey_{uid}",
                coldkey=f"mock_miner_coldkey_{uid}",
                name=f"Mock Miner {uid}",
                image=f"/miners/mock_miner_{(index % 8) + 1}.png",
                provider=provider,
                github=f"https://github.com/autoppia/mock-miner-{uid}",
                description=f"Synthetic miner profile provided by {provider}.",
            )
        )
    return records


def _build_miner_seed_records(num_miners: int, exclude_uids: set[int]) -> List[MinerSeedRecord]:
    metagraph_records = _try_fetch_metagraph()
    records: List[MinerSeedRecord] = []

    # Skip metagraph UIDs that are not positive or that are explicitly excluded.
    metagraph_uids = [
        uid for uid in metagraph_records.keys() if uid > 0 and uid not in exclude_uids
    ]
    for uid in sorted(metagraph_uids):
        hotkey, coldkey = metagraph_records[uid]
        if uid in exclude_uids:
            continue
        records.append(
            MinerSeedRecord(
                uid=uid,
                hotkey=hotkey,
                coldkey=coldkey,
                name=f"Metagraph Miner {uid}",
                image=None,
                provider="metagraph",
                github=f"https://github.com/bittensor/miner-{uid}",
                description="Miner identity discovered via Bittensor metagraph.",
            )
        )
        if len(records) >= num_miners:
            break

    if len(records) < num_miners:
        fallback_needed = num_miners - len(records)
        records.extend(
            _fallback_miner_seed_records(fallback_needed, exclude_uids=exclude_uids)
        )

    return records[:num_miners]


def _random_stake() -> float:
    return round(random.uniform(150_000, 950_000), 2)


def _random_vtrust() -> float:
    return round(random.uniform(0.55, 0.99), 4)


def _random_provider(seed_record: MinerSeedRecord) -> str:
    providers = [
        "Neuron Labs",
        "Synapse Collective",
        "Autoppia Community",
        "Tensor Guild",
    ]
    if seed_record.provider:
        return seed_record.provider
    return random.choice(providers)


def _build_validator_identity_and_snapshot(
    validator_round_id: str,
    record: ValidatorSeedRecord,
    round_number: int,
    started_at: float,
) -> Tuple[Validator, ValidatorRoundValidator]:
    identity = Validator(
        uid=record.uid,
        hotkey=record.hotkey,
        coldkey=record.coldkey,
    )

    snapshot = ValidatorRoundValidator(
        validator_round_id=validator_round_id,
        validator_uid=record.uid,
        validator_hotkey=record.hotkey,
        name=record.name,
        stake=_random_stake(),
        vtrust=_random_vtrust(),
        image_url=record.image,
        version=record.version,
        role="primary",
        metadata={
            "round_number": round_number,
            "seeded": True,
            "generated_at": started_at,
        },
    )
    return identity, snapshot


def _build_miner_identity_and_snapshot(
    validator_round_id: str,
    record: MinerSeedRecord,
    now_ts: float,
) -> Tuple[Miner, ValidatorRoundMiner]:
    identity = Miner(
        uid=record.uid,
        hotkey=record.hotkey,
        coldkey=record.coldkey,
    )

    first_seen = now_ts - random.uniform(3600, 72_000)
    last_seen = now_ts - random.uniform(0, 1800)

    snapshot = ValidatorRoundMiner(
        validator_round_id=validator_round_id,
        miner_uid=record.uid,
        miner_hotkey=record.hotkey,
        miner_coldkey=record.coldkey,
        agent_key=None,
        agent_name=record.name,
        image_url=record.image,
        github_url=record.github,
        provider=_random_provider(record),
        description=record.description,
        is_sota=False,
        first_seen_at=first_seen,
        last_seen_at=last_seen,
        metadata={"seeded": True},
    )
    return identity, snapshot


TASK_LIBRARY: List[TaskTemplate] = [
    TaskTemplate(
        website_name="Autoppia Cinema",
        website_slug="autocinema",
        url="https://autoppia.example/cinema",
        prompt="Locate the film 'The Matrix' and list its showtimes.",
        success_criteria="Showtimes list includes The Matrix.",
        use_case_label="Find Showtimes",
        use_case_slug="find-showtimes",
        default_actions=[
            {"type": "navigate", "url": "https://autoppia.example/cinema"},
            {"type": "search", "query": "The Matrix showtimes"},
            {"type": "extract", "target": "#showtimes"},
        ],
    ),
    TaskTemplate(
        website_name="Autoppia Travel",
        website_slug="autotravel",
        url="https://autoppia.example/travel",
        prompt="Search for flights from Paris to Tokyo next month.",
        success_criteria="Results show at least one flight option from Paris to Tokyo.",
        use_case_label="Search Flights",
        use_case_slug="search-flights",
        default_actions=[
            {"type": "navigate", "url": "https://autoppia.example/travel"},
            {"type": "input", "field": "from", "value": "Paris"},
            {"type": "input", "field": "to", "value": "Tokyo"},
            {"type": "click", "selector": "#search"},
        ],
    ),
    TaskTemplate(
        website_name="Autoppia Market",
        website_slug="automarket",
        url="https://autoppia.example/market",
        prompt="Find the current price of 'Autoppia Energy Drink'.",
        success_criteria="Displays price for Autoppia Energy Drink.",
        use_case_label="Find Product Price",
        use_case_slug="find-product-price",
        default_actions=[
            {"type": "navigate", "url": "https://autoppia.example/market"},
            {"type": "input", "field": "search", "value": "Autoppia Energy Drink"},
            {"type": "click", "selector": ".product-card"},
        ],
    ),
    TaskTemplate(
        website_name="Autoppia Jobs",
        website_slug="autojobs",
        url="https://autoppia.example/jobs",
        prompt="List remote positions for Python developers.",
        success_criteria="Results show remote Python developer listings.",
        use_case_label="Search Jobs",
        use_case_slug="search-jobs",
        default_actions=[
            {"type": "navigate", "url": "https://autoppia.example/jobs"},
            {"type": "input", "field": "keywords", "value": "Python developer"},
            {"type": "click", "selector": "#remote-filter"},
        ],
    ),
    TaskTemplate(
        website_name="Autoppia News",
        website_slug="autonews",
        url="https://autoppia.example/news",
        prompt="Summarize the headline article in technology section.",
        success_criteria="Summary references the headline technology article.",
        use_case_label="Summarize Headline",
        use_case_slug="summarize-headline",
        default_actions=[
            {"type": "navigate", "url": "https://autoppia.example/news"},
            {"type": "click", "selector": "#section-technology"},
            {"type": "extract", "target": ".headline"},
        ],
    ),
]


def _build_tasks(validator_round_id: str, num_tasks: int) -> Tuple[List[Task], List[TaskTemplate]]:
    tasks: List[Task] = []
    templates: List[TaskTemplate] = []

    for index in range(num_tasks):
        template = TASK_LIBRARY[index % len(TASK_LIBRARY)]
        task_id = f"{validator_round_id}-task-{index:03d}"
        task = Task(
            task_id=task_id,
            validator_round_id=validator_round_id,
            sequence=index,
            scope="global",
            is_web_real=False,
            web_project_id=None,
            url=template.url,
            prompt=template.prompt,
            html=f"<html><body>{template.website_name} task {index}</body></html>",
            clean_html=f"<html><body>{template.website_name} task {index}</body></html>",
            interactive_elements=None,
            screenshot=None,
            screenshot_description=None,
            specifications={"browser": "chromium"},
            tests=[],
            milestones=None,
            relevant_data={
                "website": template.website_slug,
                "use_case": template.use_case_slug,
            },
            success_criteria=template.success_criteria,
            use_case={
                "name": template.use_case_label,
                "slug": template.use_case_slug,
            },
            should_record=False,
        )
        tasks.append(task)
        templates.append(template)

    return tasks, templates


def _build_agent_run_bundle(
    validator_round: ValidatorRound,
    miner_identity: Miner,
    miner_snapshot: ValidatorRoundMiner,
    task_templates: Sequence[TaskTemplate],
    tasks: Sequence[Task],
    base_started_at: float,
    index: int,
) -> AgentRunBundle:
    agent_run_id = f"{validator_round.validator_round_id}-run-{miner_identity.uid or index}"
    task_count = len(tasks)

    run_start = base_started_at + index * 15
    run_end = run_start + random.uniform(60, 220)
    total_reward = random.uniform(5, 25)
    average_score = round(random.uniform(0.55, 0.98), 4)

    agent_run = AgentEvaluationRun(
        agent_run_id=agent_run_id,
        validator_round_id=validator_round.validator_round_id,
        validator_uid=validator_round.validator_uid,
        validator_hotkey=validator_round.validator_hotkey,
        miner_uid=miner_identity.uid,
        miner_hotkey=miner_identity.hotkey,
        miner_agent_key=None,
        is_sota=False,
        version="1.0.0",
        started_at=run_start,
        ended_at=run_end,
        elapsed_sec=run_end - run_start,
        average_score=average_score,
        average_execution_time=random.uniform(5, 20),
        average_reward=total_reward / task_count,
        total_reward=total_reward,
        total_tasks=task_count,
        completed_tasks=task_count,
        failed_tasks=0,
        rank=None,
        weight=None,
        metadata={"seed_index": index},
    )

    task_solutions: List[TaskSolution] = []
    evaluations: List[Evaluation] = []
    evaluation_results: List[EvaluationResult] = []

    for task_index, task in enumerate(tasks):
        template = task_templates[task_index % len(task_templates)]
        solution_id = f"{agent_run_id}-solution-{task_index:03d}"

        actions: List[Action] = []
        for action_payload in template.default_actions:
            action_payload = dict(action_payload)
            action_type = action_payload.pop("type")
            action_payload.setdefault("status", "completed")
            actions.append(Action(type=action_type, attributes=action_payload))

        task_solution = TaskSolution(
            solution_id=solution_id,
            task_id=task.task_id,
            agent_run_id=agent_run.agent_run_id,
            validator_round_id=validator_round.validator_round_id,
            validator_uid=validator_round.validator_uid,
            validator_hotkey=validator_round.validator_hotkey,
            miner_uid=miner_identity.uid,
            miner_hotkey=miner_identity.hotkey,
            miner_agent_key=None,
            actions=actions,
            web_agent_id=f"web-agent-{agent_run_id}",
            recording=None,
            metadata={"template": template.website_slug},
        )
        task_solutions.append(task_solution)

        evaluation_id = f"{solution_id}-evaluation"
        task_score = round(max(0.4, average_score - random.uniform(0, 0.1)), 4)

        evaluation = Evaluation(
            evaluation_id=evaluation_id,
            validator_round_id=validator_round.validator_round_id,
            task_id=task.task_id,
            task_solution_id=task_solution.solution_id,
            agent_run_id=agent_run.agent_run_id,
            validator_uid=validator_round.validator_uid,
            validator_hotkey=validator_round.validator_hotkey,
            miner_uid=miner_identity.uid,
            miner_hotkey=miner_identity.hotkey,
            miner_agent_key=None,
            final_score=task_score,
            raw_score=task_score,
            evaluation_time=random.uniform(1.0, 6.0),
            summary={
                "tests_passed": 1,
                "tests_total": 1,
                "template": template.website_slug,
            },
        )
        evaluations.append(evaluation)

        evaluation_result = EvaluationResult(
            result_id=str(uuid.uuid4()),
            evaluation_id=evaluation.evaluation_id,
            validator_round_id=validator_round.validator_round_id,
            agent_run_id=agent_run.agent_run_id,
            task_id=task.task_id,
            task_solution_id=task_solution.solution_id,
            miner_uid=miner_identity.uid,
            validator_uid=validator_round.validator_uid,
            final_score=evaluation.final_score,
            test_results_matrix=[
                [
                    TestResult(
                        success=True,
                        extra_data={
                            "task_id": task.task_id,
                            "template": template.website_slug,
                        },
                    )
                ]
            ],
            execution_history=[action.attributes for action in actions],
            feedback=None,
            web_agent_id=task_solution.web_agent_id,
            raw_score=evaluation.raw_score,
            evaluation_time=evaluation.evaluation_time,
            stats=None,
            gif_recording=None,
            metadata={"seed_index": task_index},
        )
        evaluation_results.append(evaluation_result)

    return AgentRunBundle(
        miner_identity=miner_identity,
        miner_snapshot=miner_snapshot,
        agent_run=agent_run,
        task_solutions=task_solutions,
        evaluations=evaluations,
        evaluation_results=evaluation_results,
        average_score=average_score,
    )


def build_seed_payload(
    validator_round_id: str,
    validator_uid: int,
    num_tasks: int,
    num_miners: int,
    round_number: int,
) -> SeedPayload:
    if num_tasks <= 0:
        raise ValueError("num_tasks must be greater than zero")
    if num_miners <= 0:
        raise ValueError("num_miners must be greater than zero")

    validator_records = _build_validator_seed_records()
    if validator_uid not in validator_records:
        raise ValueError(f"Validator UID {validator_uid} is not recognised")
    validator_record = validator_records[validator_uid]

    exclude_uids = {validator_uid}
    miner_records = _build_miner_seed_records(num_miners, exclude_uids=exclude_uids)

    tasks, task_templates = _build_tasks(validator_round_id, num_tasks)
    started_at = time.time()

    validator_identity, validator_snapshot = _build_validator_identity_and_snapshot(
        validator_round_id=validator_round_id,
        record=validator_record,
        round_number=round_number,
        started_at=started_at,
    )

    validator_round = ValidatorRound(
        validator_round_id=validator_round_id,
        round_number=round_number,
        validator_uid=validator_identity.uid,
        validator_hotkey=validator_identity.hotkey,
        validator_coldkey=validator_identity.coldkey,
        start_block=random.randint(1_000_000, 2_000_000),
        end_block=None,
        start_epoch=random.randint(10_000, 11_000),
        end_epoch=None,
        started_at=started_at,
        ended_at=None,
        elapsed_sec=None,
        max_epochs=20,
        max_blocks=360,
        n_tasks=num_tasks,
        n_miners=num_miners,
        n_winners=min(3, num_miners),
        status="active",
        average_score=None,
        top_score=None,
        summary={},
        metadata={"source": "seed"},
    )

    agent_bundles: List[AgentRunBundle] = []
    for index, miner_record in enumerate(miner_records):
        miner_identity, miner_snapshot = _build_miner_identity_and_snapshot(
            validator_round_id=validator_round_id,
            record=miner_record,
            now_ts=started_at,
        )
        bundle = _build_agent_run_bundle(
            validator_round=validator_round,
            miner_identity=miner_identity,
            miner_snapshot=miner_snapshot,
            task_templates=task_templates,
            tasks=tasks,
            base_started_at=started_at,
            index=index,
        )
        agent_bundles.append(bundle)

    summary = {
        "tasks": len(tasks),
        "agent_runs": len(agent_bundles),
        "task_solutions": sum(len(bundle.task_solutions) for bundle in agent_bundles),
        "evaluations": sum(len(bundle.evaluations) for bundle in agent_bundles),
    }
    top_score = max((bundle.average_score for bundle in agent_bundles), default=None)
    avg_score = (
        sum(bundle.average_score for bundle in agent_bundles) / len(agent_bundles)
        if agent_bundles
        else None
    )

    validator_round = validator_round.model_copy(
        update={
            "summary": summary,
            "top_score": top_score,
            "average_score": avg_score,
        }
    )

    return SeedPayload(
        validator_identity=validator_identity,
        validator_snapshot=validator_snapshot,
        validator_round=validator_round,
        validator_record=validator_record,
        miner_records=miner_records,
        tasks=tasks,
        agent_bundles=agent_bundles,
    )


def build_submission_request(payload: SeedPayload) -> ValidatorRoundSubmissionRequest:
    return ValidatorRoundSubmissionRequest(
        validator_identities=[payload.validator_identity],
        miner_identities=payload.miner_identities,
        validator_round=payload.validator_round,
        validator_snapshots=[payload.validator_snapshot],
        miner_snapshots=payload.miner_snapshots,
        agent_evaluation_runs=payload.agent_runs,
        tasks=payload.tasks,
        task_solutions=payload.task_solutions,
        evaluations=payload.evaluations,
        evaluation_results=payload.evaluation_results,
    )


async def _determine_next_round_number() -> int:
    async with AsyncSessionLocal() as session:
        stmt = select(ValidatorRoundORM.round_number).order_by(ValidatorRoundORM.round_number.desc())
        round_numbers = await session.scalars(stmt)
        for number in round_numbers:
            if number is not None:
                return int(number) + 1
    return 1


async def _guard_duplicate_round(validator_uid: int, round_number: int) -> None:
    async with AsyncSessionLocal() as session:
        service = ValidatorRoundPersistenceService(session)
        await service.ensure_unique_round_number(validator_uid, round_number)


def _ensure_response(response: httpx.Response, context: str) -> Dict[str, Any]:
    if response.status_code >= 400:
        raise RuntimeError(
            f"{context} failed with status={response.status_code} body={response.text}"
        )
    return response.json()


def _compute_round_outcome(
    payload: SeedPayload,
) -> Tuple[List[Dict[str, Any]], List[float], Dict[str, float]]:
    sorted_bundles = sorted(
        payload.agent_bundles, key=lambda bundle: bundle.average_score, reverse=True
    )
    n_winners = min(payload.validator_round.n_winners or 3, len(sorted_bundles))
    winners_data: List[Dict[str, Any]] = []
    winner_scores: List[float] = []

    for rank, bundle in enumerate(sorted_bundles[:n_winners], start=1):
        winners_data.append(
            {
                "miner_uid": bundle.agent_run.miner_uid,
                "rank": rank,
                "score": bundle.average_score,
            }
        )
        winner_scores.append(bundle.average_score)

    total_score = sum(bundle.average_score for bundle in sorted_bundles) or 1.0
    weights = {
        str(bundle.agent_run.miner_uid): round(bundle.average_score / total_score, 4)
        for bundle in sorted_bundles
        if bundle.agent_run.miner_uid is not None
    }

    return winners_data, winner_scores, weights


async def seed_validator_round(
    validator_round_id: str,
    validator_uid: int,
    num_tasks: int,
    num_miners: int,
    *,
    client: Optional[AsyncClient] = None,
    round_number: Optional[int] = None,
) -> PersistenceResult:
    if round_number is None:
        round_number = await _determine_next_round_number()

    await _guard_duplicate_round(validator_uid, round_number)

    payload = build_seed_payload(
        validator_round_id=validator_round_id,
        validator_uid=validator_uid,
        num_tasks=num_tasks,
        num_miners=num_miners,
        round_number=round_number,
    )

    owns_client = client is None
    if owns_client:
        transport = ASGITransport(app=_get_fastapi_app())
        client = AsyncClient(transport=transport, base_url="http://seed-server")

    try:
        start_body = {
            "validator_identity": payload.validator_identity.model_dump(mode="json", exclude_none=True),
            "validator_round": payload.validator_round.model_dump(mode="json", exclude_none=True),
            "validator_snapshot": payload.validator_snapshot.model_dump(mode="json", exclude_none=True),
        }
        _ensure_response(
            await client.post("/api/v1/validator-rounds/start", json=start_body),
            "start_round",
        )

        tasks_body = {
            "tasks": [
                task.model_dump(mode="json", exclude_none=True)
                for task in payload.tasks
            ]
        }
        _ensure_response(
            await client.post(
                f"/api/v1/validator-rounds/{validator_round_id}/tasks",
                json=tasks_body,
            ),
            "set_tasks",
        )

        tasks_by_id = {task.task_id: task for task in payload.tasks}

        for bundle in payload.agent_bundles:
            start_run_body = {
                "agent_run": bundle.agent_run.model_dump(mode="json", exclude_none=True),
                "miner_identity": bundle.miner_identity.model_dump(mode="json", exclude_none=True),
                "miner_snapshot": bundle.miner_snapshot.model_dump(mode="json", exclude_none=True),
            }
            _ensure_response(
                await client.post(
                    f"/api/v1/validator-rounds/{validator_round_id}/agent-runs/start",
                    json=start_run_body,
                ),
                "start_agent_run",
            )

            for evaluation, evaluation_result, task_solution in zip(
                bundle.evaluations,
                bundle.evaluation_results,
                bundle.task_solutions,
            ):
                task = tasks_by_id[evaluation.task_id]
                body = {
                    "task": task.model_dump(mode="json", exclude_none=True),
                    "task_solution": task_solution.model_dump(mode="json", exclude_none=True),
                    "evaluation": evaluation.model_dump(mode="json", exclude_none=True),
                    "evaluation_result": evaluation_result.model_dump(mode="json", exclude_none=True),
                }
                _ensure_response(
                    await client.post(
                        f"/api/v1/validator-rounds/{validator_round_id}/agent-runs/{bundle.agent_run.agent_run_id}/evaluations",
                        json=body,
                    ),
                    "add_evaluation",
                )

        winners, winner_scores, weights = _compute_round_outcome(payload)
        finish_body = {
            "status": "completed",
            "winners": winners,
            "winner_scores": winner_scores,
            "weights": weights,
            "ended_at": time.time(),
            "summary": payload.validator_round.summary,
        }
        _ensure_response(
            await client.post(
                f"/api/v1/validator-rounds/{validator_round_id}/finish",
                json=finish_body,
            ),
            "finish_round",
        )

    finally:
        if owns_client:
            await client.aclose()

    saved_entities: Dict[str, Any] = {
        "validator_round": payload.validator_round.validator_round_id,
        "validator_snapshots": [
            payload.validator_snapshot.validator_hotkey
        ],
        "miner_snapshots": [
            snapshot.miner_hotkey or snapshot.agent_key
            for snapshot in payload.miner_snapshots
        ],
        "agent_evaluation_runs": [
            bundle.agent_run.agent_run_id for bundle in payload.agent_bundles
        ],
        "tasks": [task.task_id for task in payload.tasks],
        "task_solutions": [
            solution.solution_id
            for solution in payload.task_solutions
        ],
        "evaluations": [
            evaluation.evaluation_id for evaluation in payload.evaluations
        ],
        "evaluation_results": [
            result.result_id for result in payload.evaluation_results
        ],
    }

    return PersistenceResult(
        validator_uid=payload.validator_round.validator_uid,
        saved_entities=saved_entities,
    )


async def seed_validator_round_bulk(
    validator_round_id: str,
    validator_uid: int,
    num_tasks: int,
    num_miners: int,
    *,
    round_number: Optional[int] = None,
) -> PersistenceResult:
    if round_number is None:
        round_number = await _determine_next_round_number()

    await _guard_duplicate_round(validator_uid, round_number)
    payload = build_seed_payload(
        validator_round_id=validator_round_id,
        validator_uid=validator_uid,
        num_tasks=num_tasks,
        num_miners=num_miners,
        round_number=round_number,
    )
    submission = build_submission_request(payload)

    async with AsyncSessionLocal() as session:
        service = ValidatorRoundPersistenceService(session)
        async with session.begin():
            result = await service.submit_round(submission)

    return result


def generate_validator_round_id(validator_uid: int, round_number: int) -> str:
    return f"validator-{validator_uid}-round-{round_number}-{uuid.uuid4().hex[:8]}"


__all__ = [
    "AgentRunBundle",
    "SeedPayload",
    "build_seed_payload",
    "build_submission_request",
    "seed_validator_round",
    "seed_validator_round_bulk",
    "generate_validator_round_id",
]
