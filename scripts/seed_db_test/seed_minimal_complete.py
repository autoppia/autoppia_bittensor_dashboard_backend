#!/usr/bin/env python3
"""
Script para limpiar todas las tablas y crear datos mínimos pero completos:
- 2 validators con datos del meta (lo que se subiría a IPFS)
- 2 tareas por validator
- 2 miners
- 1 solution por cada tarea (relación 1-1: 1 tarea = 1 solution = 1 evaluation)
- 1 evaluation por cada solution (relación 1-1)
- Datos en validator_round_summary_miners
"""

from __future__ import annotations

import asyncio
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    AgentEvaluationRunORM,
    EvaluationORM,
    TaskORM,
    TaskSolutionORM,
    ValidatorRoundMinerORM,
    ValidatorRoundORM,
    ValidatorRoundSummaryORM,
    ValidatorRoundValidatorORM,
)
from app.db.session import AsyncSessionLocal

# Validators a usar (con datos completos)
VALIDATORS = [
    {
        "uid": 124,
        "name": "Autoppia",
        "hotkey": "5DUmbxsTWuMxefEk36BYX8qNsF18BbUeTgBPuefBN6gSDe8j",
        "coldkey": "5DPtMdJqJqJqJqJqJqJqJqJqJqJqJqJqJqJqJqLVT3EF",
        "stake": 925_000,
        "vtrust": 0.97,
    },
    {
        "uid": 133,
        "name": "RoundTable21",
        "hotkey": "5C5hkvYVTtArY7sG39UUd1zrM1AczdtRgyydHSJRkdXGsn36",
        "coldkey": "5GZSAgJqJqJqJqJqJqJqJqJqJqJqJqJqJqJqJqBMKpGQ",
        "stake": 582_500,
        "vtrust": 0.88,
    },
]

# Miners
MINERS = [
    {"uid": 80, "hotkey": "5DypvN3kYgf19DmpXNxqUU7fZkccRJS6HnsREaWj82sQdWd8", "name": "Miner 80"},
    {"uid": 127, "hotkey": "5C5xWaJRpgdmdq1m6MHvgoABCGS2SC9h6Bvb9T6bQcVhhs36", "name": "Miner 127"},
]

# Tareas (de las existentes)
TASKS = [
    {
        "task_id": "task_1",
        "web_project_id": "autocinema",
        "url": "http://localhost:8000/?seed=201",
        "web_version": "0.1.0+6cbcca09",  # Versión con hash Git
        "prompt": "Find the next available showtime for 'Interstellar' and note the auditorium.",
        "use_case": {"name": "Find Showtimes", "slug": "find-showtimes"},
    },
    {
        "task_id": "task_2",
        "web_project_id": "autobooks",
        "url": "http://localhost:8001/?seed=301",
        "web_version": "0.1.0+6cbcca09",  # Versión con hash Git
        "prompt": "Search for 'Neural Horizons' and add it to the shopping cart.",
        "use_case": {"name": "Add to Cart", "slug": "add-to-cart"},
    },
]


async def clear_all_tables():
    """Elimina todos los datos de las tablas."""
    async with AsyncSessionLocal() as session:
        try:
            print("🗑️  Limpiando todas las tablas...")

            tables = [
                "validator_round_summary_miners",
                "evaluations",
                "task_solutions",
                "tasks",
                "miner_evaluation_runs",
                "validator_round_miners",
                "validator_round_validators",
                "validator_rounds",
            ]

            for table in tables:
                result = await session.execute(text(f"DELETE FROM {table}"))
                count = result.rowcount
                print(f"  ✓ {table}: {count} registros eliminados")

            await session.commit()
            print("✅ Todas las tablas limpiadas\n")

        except Exception as e:  # noqa: BLE001
            await session.rollback()
            print(f"❌ Error limpiando tablas: {e}")
            raise


async def create_validator_round(session: AsyncSession, validator: dict, round_number: int):
    """Crea un validator round completo con todos los datos."""
    validator_round_id = f"round_{round_number}_validator_{validator['uid']}"
    now = time.time()
    started_at = now - 3600  # Hace 1 hora
    ended_at = now - 60  # Hace 1 minuto

    print(f"\n📦 Creando round {round_number} para validator {validator['name']} (UID {validator['uid']})...")

    # 1. Crear validator_round
    round_obj = ValidatorRoundORM(
        validator_round_id=validator_round_id,
        season_number=1,
        round_number_in_season=round_number,
        start_block=7000000 + round_number * 1000,
        end_block=7000000 + round_number * 1000 + 500,
        start_epoch=19500.0 + round_number,
        end_epoch=19500.0 + round_number + 0.5,
        started_at=started_at,
        ended_at=ended_at,
        n_tasks=len(TASKS),
        status="finished",
    )
    session.add(round_obj)
    await session.flush()

    # 2. Crear validator_round_validators
    validator_snapshot = ValidatorRoundValidatorORM(
        validator_round_id=validator_round_id,
        validator_uid=validator["uid"],
        validator_hotkey=validator["hotkey"],
        validator_coldkey=validator["coldkey"],
        name=validator["name"],
        stake=validator["stake"],
        vtrust=validator["vtrust"],
        version="1.0.0",
        config={
            "round": {
                "tasks_per_miner": 2,
                "timeout_seconds": 300,
                "evaluation_mode": "standard",
            }
        },
    )
    session.add(validator_snapshot)
    await session.flush()

    # 3. Crear validator_round_miners
    miner_snapshots = []
    for miner in MINERS:
        miner_snapshot = ValidatorRoundMinerORM(
            validator_round_id=validator_round_id,
            miner_uid=miner["uid"],
            miner_hotkey=miner["hotkey"],
            name=miner["name"],
            is_sota=False,
            version="1.0.0",
            first_seen_at=started_at - 3600,
            last_seen_at=ended_at,
        )
        session.add(miner_snapshot)
        miner_snapshots.append(miner_snapshot)
    await session.flush()

    # 4. Crear tasks
    task_objs = []
    for task_data in TASKS:
        task = TaskORM(
            task_id=f"{validator_round_id}_{task_data['task_id']}",
            validator_round_id=validator_round_id,
            is_web_real=True,  # Cambiado a True porque usamos webs reales
            web_project_id=task_data.get("web_project_id"),
            web_version=task_data.get("web_version"),  # ✅ Incluir web_version
            url=task_data["url"],
            prompt=task_data["prompt"],
            specifications={},
            tests=[],
            use_case=task_data["use_case"],
        )
        session.add(task)
        task_objs.append(task)
    await session.flush()

    # 5. Crear agent runs, solutions y evaluations
    agent_runs = []
    all_solutions = []
    all_evaluations = []

    for miner_idx, miner in enumerate(MINERS):
        agent_run_id = f"{validator_round_id}_run_{miner['uid']}"
        run_start = started_at + miner_idx * 30
        run_end = run_start + random.uniform(120, 180)

        # Calcular scores para este miner
        base_score = random.uniform(0.6, 0.95)
        avg_reward = base_score * random.uniform(0.8, 1.0)
        avg_eval_score = base_score

        agent_run = AgentEvaluationRunORM(
            agent_run_id=agent_run_id,
            validator_round_id=validator_round_id,
            miner_uid=miner["uid"],
            miner_hotkey=miner["hotkey"],
            started_at=run_start,
            ended_at=run_end,
            elapsed_sec=run_end - run_start,
            average_score=base_score,
            average_execution_time=random.uniform(8, 15),
            average_reward=avg_reward,
            total_tasks=len(TASKS),
            success_tasks=len(TASKS),
            failed_tasks=0,
            meta={},
        )
        session.add(agent_run)
        agent_runs.append(agent_run)
        await session.flush()

        # Crear 1 solution por cada tarea (relación 1-1: 1 tarea = 1 solution = 1 evaluation)
        for task_idx, task in enumerate(task_objs):
            solution_id = f"{agent_run_id}_solution_{task_idx}"

            solution = TaskSolutionORM(
                solution_id=solution_id,
                task_id=task.task_id,
                agent_run_id=agent_run_id,
                validator_round_id=validator_round_id,
                validator_uid=validator["uid"],
                validator_hotkey=validator["hotkey"],
                miner_uid=miner["uid"],
                miner_hotkey=miner["hotkey"],
                actions=[
                    {"type": "navigate", "url": task.url},
                    {"type": "click", "selector": f"#element-{task_idx}"},
                    {"type": "extract", "target": "#result"},
                ],
            )
            session.add(solution)
            all_solutions.append(solution)
            await session.flush()

            # Crear 1 evaluation para cada solution (relación 1-1)
            evaluation_score = base_score + random.uniform(-0.1, 0.1)
            evaluation_score = max(0.0, min(1.0, evaluation_score))
            eval_time = random.uniform(5, 12)
            reward = evaluation_score * random.uniform(0.9, 1.0)

            evaluation = EvaluationORM(
                evaluation_id=f"{solution_id}_eval",
                validator_round_id=validator_round_id,
                agent_run_id=agent_run_id,
                task_id=task.task_id,
                task_solution_id=solution_id,
                miner_uid=miner["uid"],
                miner_hotkey=miner["hotkey"],
                validator_uid=validator["uid"],
                validator_hotkey=validator["hotkey"],
                evaluation_score=evaluation_score,  # ✅ Usa evaluation_score (existe en BD)
                reward=reward,  # ✅ Usa reward (existe en BD)
                evaluation_time=eval_time,
                extra_info={},
            )
            session.add(evaluation)
            all_evaluations.append(evaluation)
            await session.flush()

            # Create execution_history record (empty for seed)
            from app.db.models import EvaluationExecutionHistoryORM

            execution_history_record = EvaluationExecutionHistoryORM(
                evaluations_id=evaluation.id,
                execution_history=[],
            )
            session.add(execution_history_record)

    # 6. Crear datos en validator_round_summary_miners
    print("  📊 Creando datos en validator_round_summary_miners...")

    all_miner_rewards = {}
    for miner in MINERS:
        miner_evaluations = [e for e in all_evaluations if e.miner_uid == miner["uid"]]

        if miner_evaluations:
            local_avg_reward = sum(e.reward for e in miner_evaluations) / len(miner_evaluations)
            local_avg_eval_score = sum(e.evaluation_score for e in miner_evaluations) / len(miner_evaluations)
            local_avg_eval_time = sum(e.evaluation_time for e in miner_evaluations) / len(miner_evaluations)
            # Contar tareas únicas recibidas (cada tarea tiene 1 evaluation)
            local_tasks_received = len({e.task_id for e in miner_evaluations})
            # Contar tareas únicas exitosas (cada tarea tiene 1 evaluation, relación 1-1)
            local_tasks_success = len({e.task_id for e in miner_evaluations if e.evaluation_score >= 0.5})

            all_miner_rewards[miner["uid"]] = local_avg_reward

    # Calcular ranks
    sorted_miners = sorted(all_miner_rewards.items(), key=lambda x: x[1], reverse=True)
    rank_map = {uid: i + 1 for i, (uid, _) in enumerate(sorted_miners)}

    # Crear summary records
    for miner in MINERS:
        miner_evaluations = [e for e in all_evaluations if e.miner_uid == miner["uid"]]
        if not miner_evaluations:
            continue

        local_avg_reward = sum(e.reward for e in miner_evaluations) / len(miner_evaluations)
        local_avg_eval_score = sum(e.evaluation_score for e in miner_evaluations) / len(miner_evaluations)
        local_avg_eval_time = sum(e.evaluation_time for e in miner_evaluations) / len(miner_evaluations)
        # Contar tareas únicas recibidas (cada tarea tiene 1 evaluation)
        local_tasks_received = len({e.task_id for e in miner_evaluations})
        # Contar tareas únicas exitosas (cada tarea tiene 1 evaluation, relación 1-1)
        local_tasks_success = len({e.task_id for e in miner_evaluations if e.evaluation_score >= 0.5})
        local_rank = rank_map.get(miner["uid"], 1)

        # Post-consensus se calculará después agregando datos de todos los validators
        # Por ahora, inicializamos con valores temporales (se actualizarán en calculate_post_consensus_scores)
        post_consensus_rank = local_rank  # Temporal, se actualizará
        post_consensus_avg_reward = 0.0  # ✅ Se calculará después con consensus
        post_consensus_avg_eval_score = local_avg_eval_score  # Promedio de todos los validators
        post_consensus_avg_eval_time = local_avg_eval_time  # Promedio de todos los validators
        post_consensus_tasks_received = local_tasks_received  # Suma de todos los validators
        post_consensus_tasks_success = local_tasks_success  # Suma de todos los validators

        weight = 0.0  # ✅ Se calculará después con consensus_reward

        # Get subnet price (alpha to TAO rate) - use a default value for seed data
        from app.config import settings
        from app.services.subnet_utils import get_price

        try:
            subnet_price = get_price(netuid=settings.VALIDATOR_NETUID if hasattr(settings, "VALIDATOR_NETUID") else 36)
            if subnet_price <= 0:
                subnet_price = 0.0043  # Default fallback price
        except Exception:  # noqa: BLE001
            subnet_price = 0.0043  # Default fallback price

        summary = ValidatorRoundSummaryORM(
            validator_round_id=validator_round_id,
            miner_uid=miner["uid"],
            miner_hotkey=miner["hotkey"],
            local_avg_reward=local_avg_reward,
            local_avg_eval_score=local_avg_eval_score,
            local_avg_eval_time=local_avg_eval_time,
            local_tasks_received=local_tasks_received,
            local_tasks_success=local_tasks_success,
            post_consensus_rank=post_consensus_rank,
            post_consensus_avg_reward=post_consensus_avg_reward,
            post_consensus_avg_eval_score=post_consensus_avg_eval_score,
            post_consensus_avg_eval_time=post_consensus_avg_eval_time,
            post_consensus_tasks_received=post_consensus_tasks_received,
            post_consensus_tasks_success=post_consensus_tasks_success,
            weight=weight,
            subnet_price=subnet_price,
        )
        session.add(summary)

    await session.flush()

    # 7. Crear meta con datos de IPFS (lo que se subiría)
    print("  📤 Creando meta con datos de IPFS...")

    # Construir stats_list (lo que se sube a IPFS)
    stats_list = []
    for miner in MINERS:
        miner_evaluations = [e for e in all_evaluations if e.miner_uid == miner["uid"]]
        if miner_evaluations:
            avg_reward = sum(e.reward for e in miner_evaluations) / len(miner_evaluations)
            avg_eval_score = sum(e.evaluation_score for e in miner_evaluations) / len(miner_evaluations)

            stats_list.append(
                {
                    "miner_uid": miner["uid"],
                    "miner_hotkey": miner["hotkey"],
                    "avg_reward": round(avg_reward, 6),
                    "avg_eval_score": round(avg_eval_score, 6),
                }
            )

    # Construir local_evaluation (lo que se guarda en meta, NO se sube a IPFS)
    local_miners = []
    for miner in MINERS:
        summary = await session.scalar(
            select(ValidatorRoundSummaryORM).where(
                ValidatorRoundSummaryORM.validator_round_id == validator_round_id,
                ValidatorRoundSummaryORM.miner_uid == miner["uid"],
            )
        )
        if summary:
            local_miners.append(
                {
                    "miner_uid": miner["uid"],
                    "miner_hotkey": miner["hotkey"],
                    "rank": summary.local_rank,
                    "avg_reward": round(summary.local_avg_reward, 6) if summary.local_avg_reward else 0.0,
                    "avg_eval_score": round(summary.local_avg_eval_score, 6) if summary.local_avg_eval_score else 0.0,
                    "avg_evaluation_time": round(summary.local_avg_eval_time, 2) if summary.local_avg_eval_time else 0.0,
                    "tasks_attempted": summary.local_tasks_received,
                    "tasks_completed": summary.local_tasks_success,
                }
            )

    # Construir post_consensus_evaluation (NO se sube a IPFS, se calcula después)
    # ✅ Usa consensus_reward (no reward) como espera el código
    post_consensus_miners = []
    for miner in MINERS:
        summary = await session.scalar(
            select(ValidatorRoundSummaryORM).where(
                ValidatorRoundSummaryORM.validator_round_id == validator_round_id,
                ValidatorRoundSummaryORM.miner_uid == miner["uid"],
            )
        )
        if summary:
            post_consensus_miners.append(
                {
                    "miner_uid": miner["uid"],
                    "miner_hotkey": miner["hotkey"],
                    "rank": summary.post_consensus_rank,
                    "consensus_reward": round(summary.post_consensus_avg_reward, 6) if summary.post_consensus_avg_reward else 0.0,  # ✅ consensus_reward
                    "avg_eval_score": round(summary.post_consensus_avg_eval_score, 6) if summary.post_consensus_avg_eval_score else 0.0,
                    "avg_eval_time": round(summary.post_consensus_avg_eval_time, 2) if summary.post_consensus_avg_eval_time else 0.0,
                    "tasks_sent": summary.post_consensus_tasks_received,
                    "tasks_success": summary.post_consensus_tasks_success,
                    "weight": round(summary.weight, 6) if summary.weight else 0.0,
                }
            )

    # validator_summary: solo round, s3_logs, ipfs_uploaded, ipfs_downloaded, evaluation_pre_consensus, evaluation_post_consensus
    round_obj.validator_summary = {
        "round": {
            "round_number": round_number,
            "started_at": started_at,
            "ended_at": ended_at,
            "tasks_total": len(TASKS),
            "miners_evaluated": len(MINERS),
            "tasks_completed": len([e for e in all_evaluations if e.evaluation_score > 0.5]),
            "emission": {
                "alpha_price": 0.0043,
                "burn_percentage": 92.5,
                "burn_recipient_uid": 5,
            },
        },
        "s3_logs": None,
        "ipfs_uploaded": {
            "timestamp": ended_at,
            "validator_hotkey": validator["hotkey"],
            "validator_uid": validator["uid"],
            "stake": validator["stake"],
            "stats_list": stats_list,
        },
        "ipfs_downloaded": None,
        "evaluation_pre_consensus": {"miners": local_miners, "timestamp": ended_at},
        "evaluation_post_consensus": {"miners": post_consensus_miners, "timestamp": ended_at},
    }

    # No hacer commit aquí, se hará después de calcular post-consensus
    await session.flush()

    print(f"  ✅ Round {round_number} creado para validator {validator['name']}:")
    print(f"     - Tasks: {len(task_objs)}")
    print(f"     - Agent runs: {len(agent_runs)}")
    print(f"     - Solutions: {len(all_solutions)} (1 por tarea por miner)")
    print(f"     - Evaluations: {len(all_evaluations)} (1 por solution, relación 1-1)")
    print(f"     - Summary records: {len(MINERS)}")


async def calculate_post_consensus_scores(session: AsyncSession, round_number: int):
    """Calcula los scores post-consensus agregando datos de todos los validators del round."""
    print(f"\n📊 Calculando post-consensus scores para round {round_number}...")

    # Obtener todos los validator_round_ids para este round
    validator_round_ids = [f"round_{round_number}_validator_{v['uid']}" for v in VALIDATORS]

    # Obtener todos los summaries para este round
    summaries_by_miner: dict[int, list[ValidatorRoundSummaryORM]] = {}
    total_stake = 0.0

    for validator_round_id in validator_round_ids:
        result = await session.execute(select(ValidatorRoundSummaryORM).where(ValidatorRoundSummaryORM.validator_round_id == validator_round_id))
        summaries = list(result.scalars().all())

        # Obtener stake del validator
        validator_result = await session.execute(select(ValidatorRoundValidatorORM).where(ValidatorRoundValidatorORM.validator_round_id == validator_round_id))
        validator_snapshot = validator_result.scalar_one_or_none()
        if validator_snapshot:
            stake = float(validator_snapshot.stake or 0.0)
            total_stake += stake

        for summary in summaries:
            miner_uid = summary.miner_uid
            if miner_uid is not None:
                if miner_uid not in summaries_by_miner:
                    summaries_by_miner[miner_uid] = []
                summaries_by_miner[miner_uid].append(summary)

    # Calcular consensus scores (stake-weighted average)
    consensus_scores: dict[int, float] = {}
    for miner_uid, summaries in summaries_by_miner.items():
        weighted_sum = 0.0
        total_weight = 0.0

        for summary in summaries:
            # Obtener stake del validator para este summary
            validator_round_id = summary.validator_round_id
            validator_result = await session.execute(select(ValidatorRoundValidatorORM).where(ValidatorRoundValidatorORM.validator_round_id == validator_round_id))
            validator_snapshot = validator_result.scalar_one_or_none()
            if validator_snapshot:
                stake = float(validator_snapshot.stake or 0.0)
                weight = stake / total_stake if total_stake > 0 else 1.0 / len(summaries)
                weighted_sum += summary.local_avg_reward * weight
                total_weight += weight

        consensus_scores[miner_uid] = weighted_sum / total_weight if total_weight > 0 else 0.0

    # Calcular ranks post-consensus
    sorted_miners = sorted(consensus_scores.items(), key=lambda x: x[1], reverse=True)
    consensus_rank_map = {uid: i + 1 for i, (uid, _) in enumerate(sorted_miners)}
    print(f"  📊 Consensus ranks: {consensus_rank_map}")

    # Actualizar todos los summaries con datos post-consensus
    for miner_uid, summaries in summaries_by_miner.items():
        consensus_reward = consensus_scores.get(miner_uid, 0.0)
        consensus_rank = consensus_rank_map.get(miner_uid, 1)

        # Agregar promedios de todos los validators
        total_eval_score = sum(s.local_avg_eval_score for s in summaries)
        total_eval_time = sum(s.local_avg_eval_time for s in summaries)
        total_tasks_received = sum(s.local_tasks_received for s in summaries)
        total_tasks_success = sum(s.local_tasks_success for s in summaries)
        count = len(summaries)

        # Get subnet price for this round (should be the same for all summaries in the round)
        from app.config import settings
        from app.services.subnet_utils import get_price

        try:
            subnet_price = get_price(netuid=settings.VALIDATOR_NETUID if hasattr(settings, "VALIDATOR_NETUID") else 36)
            if subnet_price <= 0:
                subnet_price = 0.0043  # Default fallback price
        except Exception:  # noqa: BLE001
            subnet_price = 0.0043  # Default fallback price

        for summary in summaries:
            summary.post_consensus_rank = consensus_rank
            summary.post_consensus_avg_reward = consensus_reward
            summary.post_consensus_avg_eval_score = total_eval_score / count if count > 0 else 0.0
            summary.post_consensus_avg_eval_time = total_eval_time / count if count > 0 else 0.0
            summary.post_consensus_tasks_received = total_tasks_received
            summary.post_consensus_tasks_success = total_tasks_success

            # Calcular weight basado en consensus_reward
            total_consensus = sum(consensus_scores.values())
            summary.weight = consensus_reward / total_consensus if total_consensus > 0 else 0.0

            # Ensure subnet_price is set (use existing if already set, otherwise use current price)
            if summary.subnet_price is None:
                summary.subnet_price = subnet_price

    await session.commit()  # ✅ Commit para guardar los cambios
    print(f"  ✅ Post-consensus scores calculados y guardados para {len(summaries_by_miner)} miners")


async def main():
    """Función principal."""
    print("=" * 70)
    print("🧹 LIMPIEZA Y CREACIÓN DE DATOS MÍNIMOS")
    print("=" * 70)
    print()

    try:
        # 1. Limpiar todas las tablas
        await clear_all_tables()

        # 2. Crear 2 rounds, cada uno con ambos validators
        async with AsyncSessionLocal() as session:
            for round_num in [1, 2]:
                print(f"\n{'=' * 70}")
                print(f"🔄 CREANDO ROUND {round_num} (con {len(VALIDATORS)} validators)")
                print(f"{'=' * 70}")

                # Crear round para cada validator
                for validator in VALIDATORS:
                    await create_validator_round(session, validator, round_num)

                # IMPORTANTE: Flush para que los datos estén disponibles para la consulta
                await session.flush()

                # Calcular post-consensus scores agregando datos de ambos validators
                # (calculate_post_consensus_scores hace su propio commit)
                await calculate_post_consensus_scores(session, round_num)

        # 3. Verificar datos creados
        async with AsyncSessionLocal() as session:
            print("\n📊 Resumen final:")
            tables = [
                "validator_rounds",
                "validator_round_validators",
                "validator_round_miners",
                "validator_round_summary_miners",
                "miner_evaluation_runs",
                "tasks",
                "task_solutions",
                "evaluations",
            ]

            for table in tables:
                result = await session.execute(text(f"SELECT COUNT(*) FROM {table}"))
                count = result.scalar()
                print(f"  - {table}: {count} registros")

            print("\n✅ ¡Proceso completado exitosamente!")
            print("\n💡 Estructura de datos creada:")
            print("   📦 2 rounds (round_1 y round_2)")
            print("   👥 2 validators por round (4 validator_rounds total)")
            print("   📋 2 tareas por validator (8 tasks total)")
            print("   ⛏️  2 miners evaluados por cada validator")
            print("   💾 1 solution por cada tarea (2 por miner, 4 por validator) - relación 1-1")
            print("   ✅ 1 evaluation por cada solution (2 por miner, 4 por validator) - relación 1-1")
            print("   📊 validator_round_summary_miners: 8 registros (2 miners × 2 validators × 2 rounds)")
            print("   🔄 Post-consensus scores calculados agregando datos de ambos validators")
            print("   📤 Meta con ipfs_uploaded, local_evaluation y post_consensus_evaluation")

            # Verificar que web_version está presente
            result = await session.execute(text("SELECT COUNT(*) FROM tasks WHERE web_version IS NOT NULL"))
            tasks_with_version = result.scalar()
            print(f"\n   ✅ {tasks_with_version} tareas tienen web_version asignada")

    except Exception as e:  # noqa: BLE001
        print(f"\n❌ Error durante el proceso: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
