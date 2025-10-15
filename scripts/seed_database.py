from __future__ import annotations

import argparse
import asyncio
from typing import Dict, List

from app.db.session import AsyncSessionLocal, init_db
from app.models.core import (
    Action,
    AgentEvaluationRun,
    EvaluationResult,
    MinerInfo,
    Round,
    RoundSubmissionRequest,
    Task,
    TaskSolution,
    ValidatorInfo,
)
from app.services.validator_storage import RoundPersistenceService

VALIDATOR_PRESETS = [
    {"uid": 201, "name": "Autoppia", "stake": 1500.0, "vtrust": 0.92, "version": "7.1.0"},
    {"uid": 202, "name": "Yuma", "stake": 1380.5, "vtrust": 0.88, "version": "7.0.2"},
    {"uid": 203, "name": "Kraken", "stake": 1295.7, "vtrust": 0.85, "version": "7.0.1"},
    {"uid": 204, "name": "RoundTable21", "stake": 1410.3, "vtrust": 0.90, "version": "7.1.1"},
    {"uid": 205, "name": "Tao5", "stake": 1225.4, "vtrust": 0.83, "version": "6.9.0"},
    {"uid": 206, "name": "Synapse Labs", "stake": 1188.2, "vtrust": 0.81, "version": "6.8.3"},
    {"uid": 207, "name": "TensorWave", "stake": 1456.8, "vtrust": 0.91, "version": "7.2.0"},
    {"uid": 208, "name": "Flux Dynamics", "stake": 1350.6, "vtrust": 0.86, "version": "7.0.4"},
]

REGULAR_MINER_NAMES = [
    "BrowserFox",
    "Selenium Master",
    "Puppeteer Expert",
    "Autoppia Agent",
    "Tensor Runner",
    "Quantum Miner",
    "Synapse Scout",
    "Orbit Labs",
    "Flux Seeker",
    "Signal Bridge",
    "Neural Forge",
    "Pulse Metrics",
    "Aurora Stack",
    "Graph Rover",
    "Beacon AI",
    "Waveform",
    "Optimus",
    "Pathfinder",
    "Atlas Miner",
    "Vector Labs",
]

SOTA_PRESETS = [
    {"name": "OpenAI", "provider": "OpenAI", "uid": 90001},
    {"name": "Anthropic", "provider": "Anthropic", "uid": 90002},
    {"name": "Browser Use", "provider": "Browser Use", "uid": 90003},
]

VALIDATORS_PER_ROUND = 3
MIN_MINERS_PER_ROUND = 20
MIN_TASKS_PER_ROUND = 30
DEFAULT_ROUND_COUNT = 20
GIF_URLS = ['https://media.giphy.com/media/3oEjI6SIIHBdRxXI40/giphy.gif', 'https://media.giphy.com/media/l0HlTy9x8FZo0XO1i/giphy.gif', 'https://media.giphy.com/media/xT9IgtlYf05D5wW0aE/giphy.gif', 'https://media.giphy.com/media/3o7aD4LgSga8fPzZ7W/giphy.gif', 'https://media.giphy.com/media/26Fxy3Iz1ari8oytO/giphy.gif']


def _validator_for_round(round_index: int, position: int) -> ValidatorInfo:
    preset = VALIDATOR_PRESETS[(round_index * VALIDATORS_PER_ROUND + position) % len(VALIDATOR_PRESETS)]
    uid = preset["uid"] + round_index
    return ValidatorInfo(
        uid=uid,
        hotkey=f"validator_hotkey_{uid}",
        coldkey=None,
        stake=preset["stake"],
        vtrust=preset["vtrust"],
        name=preset["name"],
        version=preset["version"],
    )


def _miner_image(uid: int) -> str:
    index = uid % 50
    return f"https://infinitewebarena.autoppia.com/miners/{index}.svg"


def _create_miner(uid: int, name: str, is_sota: bool = False, provider: str | None = None) -> MinerInfo:
    return MinerInfo(
        uid=uid,
        hotkey=f"miner_hotkey_{uid}",
        coldkey=f"miner_coldkey_{uid}",
        agent_name=name,
        agent_image=_miner_image(uid),
        github=f"https://github.com/autoppia/{name.lower().replace(' ', '-')}",
        is_sota=is_sota,
        description=f"Seeded profile for {name}",
        provider=provider,
    )


def _build_round(round_index: int) -> RoundSubmissionRequest:
    round_id = f"round_{round_index + 1:03d}"

    validators = [
        _validator_for_round(round_index, pos)
        for pos in range(VALIDATORS_PER_ROUND)
    ]
    validator_info = validators[0]

    sota_infos = [
        _create_miner(
            preset["uid"] + round_index,
            preset["name"],
            is_sota=True,
            provider=preset["provider"],
        )
        for preset in SOTA_PRESETS
    ]

    regular_miners: List[MinerInfo] = []
    base_uid = 1000 + round_index * MIN_MINERS_PER_ROUND
    for idx in range(MIN_MINERS_PER_ROUND - len(sota_infos)):
        name = REGULAR_MINER_NAMES[(round_index * MIN_MINERS_PER_ROUND + idx) % len(REGULAR_MINER_NAMES)]
        regular_miners.append(_create_miner(base_uid + idx, name))

    miner_infos = sota_infos + regular_miners

    # distribute tasks across runs (at least one per run)
    tasks_per_run = [1] * MIN_MINERS_PER_ROUND
    remaining_tasks = MIN_TASKS_PER_ROUND - MIN_MINERS_PER_ROUND
    for i in range(remaining_tasks):
        tasks_per_run[i % MIN_MINERS_PER_ROUND] += 1

    agent_runs: List[AgentEvaluationRun] = []
    tasks: List[Task] = []
    solutions: List[TaskSolution] = []
    evaluations: List[EvaluationResult] = []

    run_scores: Dict[str, float] = {}
    task_counter = 0

    for run_idx, miner in enumerate(miner_infos):
        run_id = f"{round_id}_run_{run_idx + 1:02d}"
        average_base = 0.7 + ((round_index + run_idx) % 10) * 0.02
        if miner.is_sota:
            average_base = 0.94 - (run_idx * 0.01)

        task_ids: List[str] = []
        total_reward = 0.0
        completed = 0
        for _ in range(tasks_per_run[run_idx]):
            task_counter += 1
            task_id = f"{round_id}_task_{task_counter:03d}"
            task_ids.append(task_id)

            task = Task(
                task_id=task_id,
                validator_round_id=round_id,
                agent_run_id=run_id,
                scope="local",
                is_web_real=False,
                web_project_id=None,
                url=f"https://example.com/task/{task_counter}",
                prompt=f"Execute seeded task {task_counter}",
                html="<html></html>",
                clean_html="<html></html>",
                interactive_elements=None,
                screenshot=f"/screenshots/{task_id}.png",
                screenshot_description="Seeded task screenshot",
                specifications={"browser": "chrome"},
                tests=[],
                milestones=None,
                relevant_data={"category": "demo"},
                success_criteria="Complete the seeded objective",
                use_case={"name": "Seed Use Case"},
                should_record=True,
            )

            solution = TaskSolution(
                solution_id=f"{task_id}_solution",
                task_id=task_id,
                validator_round_id=round_id,
                agent_run_id=run_id,
                miner_uid=miner.uid,
                validator_uid=validator_info.uid,
                actions=[
                    Action(type="click", attributes={"selector": "#submit"}),
                    Action(type="type", attributes={"selector": "#input", "value": "seed"}),
                ],
                web_agent_id=miner.agent_name.lower().replace(" ", "-"),
                recording=GIF_URLS[task_counter % len(GIF_URLS)],
            )

            score = min(0.99, average_base + (task_counter % 5) * 0.01)
            reward = round(score * 2.0, 3)
            evaluation = EvaluationResult(
                evaluation_id=f"{task_id}_evaluation",
                task_id=task_id,
                task_solution_id=solution.solution_id,
                validator_round_id=round_id,
                agent_run_id=run_id,
                miner_uid=miner.uid,
                validator_uid=validator_info.uid,
                final_score=score,
                test_results_matrix=[[{"success": score >= 0.7, "extra_data": {"precision": round(score, 3)}}]],
                execution_history=[{"action": "navigate", "selector": "#home"}],
                feedback=None,
                web_agent_id=solution.web_agent_id,
                raw_score=score,
                evaluation_time=4.0 + (task_counter % 3),
                stats=None,
                gif_recording=GIF_URLS[task_counter % len(GIF_URLS)],
            )

            tasks.append(task)
            solutions.append(solution)
            evaluations.append(evaluation)

            total_reward += reward
            completed += 1 if score >= 0.5 else 0

        avg_score = sum(ev.final_score for ev in evaluations[-tasks_per_run[run_idx]:]) / tasks_per_run[run_idx]
        run_scores[run_id] = avg_score

        agent_runs.append(
            AgentEvaluationRun(
                agent_run_id=run_id,
                validator_round_id=round_id,
                validator_uid=validator_info.uid,
                miner_uid=miner.uid,
                miner_info=miner,
                is_sota=miner.is_sota,
                version="1.0",
                task_ids=task_ids,
                started_at=1_700_000_050.0 + run_idx * 5,
                ended_at=1_700_000_200.0 + run_idx * 5,
                elapsed_sec=150.0,
                avg_eval_score=avg_score,
                avg_execution_time=20.0,
                avg_reward=total_reward / len(task_ids),
                total_reward=total_reward,
                n_tasks_total=len(task_ids),
                n_tasks_completed=completed,
                n_tasks_failed=len(task_ids) - completed,
                rank=None,
                weight=None,
                metadata={"seed": True},
            )
        )

    sorted_runs = sorted(agent_runs, key=lambda run: run.avg_eval_score or 0.0, reverse=True)
    winners = []
    winner_scores = []
    weights: Dict[int, float] = {}

    for rank, run in enumerate(sorted_runs[:3], start=1):
        winners.append(
            {
                "miner_uid": run.miner_uid,
                "validator_uid": validator_info.uid,
                "task_id": run.task_ids[0] if run.task_ids else None,
                "score": run.avg_eval_score or 0.0,
                "rank": rank,
                "reward": round((run.avg_eval_score or 0.0) * 2.5, 3),
            }
        )
        winner_scores.append(run.avg_eval_score or 0.0)

    total_score = sum(run.avg_eval_score or 0.0 for run in agent_runs)
    for run in agent_runs:
        uid = run.miner_uid or 0
        weights[uid] = round((run.avg_eval_score or 0.0) / total_score, 4) if total_score else 0.0

    round_model = Round(
        validator_round_id=round_id,
        validator_info=validator_info,
        validators=validators,
        start_block=1200 + round_index * 100,
        start_epoch=round_index + 1,
        end_block=1200 + round_index * 100 + 300,
        end_epoch=round_index + 2,
        started_at=1_700_000_000.0 + round_index * 1_000,
        ended_at=1_700_000_600.0 + round_index * 1_000,
        elapsed_sec=600.0,
        max_epochs=20,
        max_blocks=360,
        n_tasks=MIN_TASKS_PER_ROUND,
        n_miners=MIN_MINERS_PER_ROUND,
        n_winners=len(winners),
        miners=miner_infos,
        sota_agents=[info for info in miner_infos if info.is_sota],
        winners=winners,
        winner_scores=winner_scores,
        weights={uid: weight for uid, weight in weights.items() if uid is not None},
        average_score=sum((run.avg_eval_score or 0.0) for run in agent_runs) / len(agent_runs),
        top_score=max(winner_scores) if winner_scores else 0.0,
        status="completed",
    )

    return RoundSubmissionRequest(
        round=round_model,
        agent_evaluation_runs=agent_runs,
        tasks=tasks,
        task_solutions=solutions,
        evaluation_results=evaluations,
    )


def _build_payloads(count: int) -> List[RoundSubmissionRequest]:
    return [_build_round(index) for index in range(count)]


def _persist(payloads: List[RoundSubmissionRequest]) -> None:
    async def _run() -> None:
        await init_db()
        async with AsyncSessionLocal() as session:
            service = RoundPersistenceService(session)
            for submission in payloads:
                async with session.begin():
                    await service.upsert_round_submission(submission)

    asyncio.run(_run())


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed the database with validator round data.")
    parser.add_argument(
        "--count",
        type=int,
        default=DEFAULT_ROUND_COUNT,
        help="Number of seeded rounds to create (default: 20)",
    )
    args = parser.parse_args()

    payloads = _build_payloads(args.count)
    _persist(payloads)
    print(f"Seeded {len(payloads)} rounds into the database.")


if __name__ == "__main__":
    main()
