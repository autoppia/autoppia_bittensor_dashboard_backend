#!/usr/bin/env python3
"""
Script de STRESS TEST para generar grandes volúmenes de datos:
- Múltiples rounds (configurable, default: 50)
- Múltiples validators (configurable, default: 5)
- Múltiples miners (configurable, default: 30)
- Múltiples tareas por round (configurable, default: 15)
- Relación 1-1: 1 tarea = 1 solution = 1 evaluation

Este script está diseñado para probar el rendimiento del sistema con grandes volúmenes de datos.
"""

import asyncio
import sys
import random
import time
from pathlib import Path
from typing import Dict, List

sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import text, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import AsyncSessionLocal
from app.db.models import (
    ValidatorRoundORM,
    ValidatorRoundValidatorORM,
    ValidatorRoundMinerORM,
    ValidatorRoundSummaryORM,
    AgentEvaluationRunORM,
    TaskORM,
    TaskSolutionORM,
    EvaluationORM,
)


# ============================================================================
# CONFIGURACIÓN DEL STRESS TEST
# ============================================================================
NUM_ROUNDS = 50  # Número de rounds a crear
NUM_VALIDATORS = 5  # Número de validators
NUM_MINERS = 30  # Número de miners
TASKS_PER_ROUND = 30  # Tareas por validator por round
BATCH_SIZE = 10  # Procesar en lotes para mejor rendimiento


# Generar validators dinámicamente
def generate_validators(count: int) -> List[Dict]:
    """Genera una lista de validators para el stress test."""
    base_validators = [
        {"uid": 124, "name": "Autoppia", "hotkey": "5DUmbxsTWuMxefEk36BYX8qNsF18BbUeTgBPuefBN6gSDe8j", "stake": 925_000, "vtrust": 0.97},
        {"uid": 133, "name": "RoundTable21", "hotkey": "5C5hkvYVTtArY7sG39UUd1zrM1AczdtRgyydHSJRkdXGsn36", "stake": 582_500, "vtrust": 0.88},
        {"uid": 129, "name": "tao5", "hotkey": "5CsvRJ...5A2zVp", "stake": 640_000, "vtrust": 0.91},
        {"uid": 135, "name": "Kraken", "hotkey": "5C5xWa...Vhhs36", "stake": 870_000, "vtrust": 0.93},
        {"uid": 137, "name": "Yuma", "hotkey": "5DLDdE...GuJjst", "stake": 455_000, "vtrust": 0.86},
    ]

    validators = base_validators[:count]

    # Si necesitamos más validators, generarlos
    for i in range(len(base_validators), count):
        validators.append(
            {
                "uid": 200 + i,
                "name": f"Validator_{200 + i}",
                "hotkey": f"5D{'A' * 40}{i:02d}",
                "coldkey": f"5D{'B' * 40}{i:02d}",
                "stake": random.randint(300_000, 1_000_000),
                "vtrust": round(random.uniform(0.80, 0.99), 2),
            }
        )

    return validators


# Generar miners dinámicamente
def generate_miners(count: int) -> List[Dict]:
    """Genera una lista de miners para el stress test."""
    base_miners = [
        {"uid": 80, "hotkey": "5DypvN3kYgf19DmpXNxqUU7fZkccRJS6HnsREaWj82sQdWd8", "name": "Miner 80"},
        {"uid": 127, "hotkey": "5C5xWaJRpgdmdq1m6MHvgoABCGS2SC9h6Bvb9T6bQcVhhs36", "name": "Miner 127"},
    ]

    miners = base_miners[:]

    # Generar miners adicionales
    for i in range(len(base_miners), count):
        miners.append(
            {
                "uid": 100 + i,
                "hotkey": f"5D{'M' * 40}{i:02d}",
                "name": f"Miner {100 + i}",
            }
        )

    return miners


# Generar tareas dinámicamente
def generate_tasks(count: int, round_num: int) -> List[Dict]:
    """Genera una lista de tareas para el stress test."""
    websites = ["autocinema", "autobooks", "autoshop", "autotravel", "autofood"]
    use_cases = [
        {"name": "Find Showtimes", "slug": "find-showtimes"},
        {"name": "Add to Cart", "slug": "add-to-cart"},
        {"name": "Search Products", "slug": "search-products"},
        {"name": "Book Flight", "slug": "book-flight"},
        {"name": "Order Food", "slug": "order-food"},
    ]

    tasks = []
    for i in range(count):
        website = websites[i % len(websites)]
        use_case = use_cases[i % len(use_cases)]
        tasks.append(
            {
                "task_id": f"task_{round_num}_{i}",
                "web_project_id": website,
                "url": f"http://localhost:800{i % 5}/?seed={round_num * 100 + i}",
                "web_version": "0.1.0+6cbcca09",
                "prompt": f"Task {i + 1} for {website}: {use_case['name']}",
                "use_case": use_case,
            }
        )

    return tasks


async def clear_all_tables():
    """Elimina todos los datos de las tablas."""
    async with AsyncSessionLocal() as session:
        try:
            print("🗑️  Limpiando todas las tablas...")

            # Orden importante: eliminar primero las tablas con foreign keys
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

            # Verificar que todas las tablas estén vacías
            print("\n🔍 Verificando que todas las tablas estén vacías...")
            for table in tables:
                result = await session.execute(text(f"SELECT COUNT(*) FROM {table}"))
                count = result.scalar()
                if count > 0:
                    print(f"  ⚠️  {table} todavía tiene {count} registros, limpiando de nuevo...")
                    await session.execute(text(f"TRUNCATE TABLE {table} CASCADE"))
                    await session.commit()

            print("✅ Todas las tablas limpiadas\n")

        except Exception as e:
            await session.rollback()
            print(f"❌ Error limpiando tablas: {e}")
            raise


async def create_validator_round(
    session: AsyncSession,
    validator: dict,
    round_number: int,
    tasks: List[Dict],
    miners: List[Dict],
):
    """Crea un validator round completo con todos los datos."""
    validator_round_id = f"round_{round_number}_validator_{validator['uid']}"
    now = time.time()
    started_at = now - (NUM_ROUNDS - round_number) * 3600  # Distribuir en el tiempo
    ended_at = started_at + 3600  # 1 hora de duración

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
        n_tasks=len(tasks),
        status="finished",
        validator_summary={
            "round": {"round_number": round_number, "started_at": started_at, "ended_at": ended_at},
            "s3_logs": None,
            "ipfs_uploaded": None,
            "ipfs_downloaded": None,
            "evaluation_pre_consensus": None,
            "evaluation_post_consensus": None,
        },
    )
    session.add(round_obj)
    await session.flush()

    # 2. Crear validator_round_validators
    validator_snapshot = ValidatorRoundValidatorORM(
        validator_round_id=validator_round_id,
        validator_uid=validator["uid"],
        validator_hotkey=validator["hotkey"],
        validator_coldkey=validator.get("coldkey", validator["hotkey"]),
        name=validator["name"],
        stake=validator["stake"],
        vtrust=validator["vtrust"],
        version="1.0.0",
        config={
            "round": {
                "tasks_per_miner": len(tasks),
                "timeout_seconds": 300,
                "evaluation_mode": "standard",
            }
        },
    )
    session.add(validator_snapshot)
    await session.flush()

    # 3. Crear validator_round_miners
    for miner in miners:
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
    await session.flush()

    # 4. Crear tasks
    task_objs = []
    for task_data in tasks:
        task = TaskORM(
            task_id=f"{validator_round_id}_{task_data['task_id']}",
            validator_round_id=validator_round_id,
            is_web_real=True,
            web_project_id=task_data.get("web_project_id"),
            web_version=task_data.get("web_version"),
            url=task_data["url"],
            prompt=task_data["prompt"],
            specifications={},
            tests=[],
            use_case=task_data["use_case"],
        )
        session.add(task)
        task_objs.append(task)
    await session.flush()

    # 5. Crear agent runs, solutions y evaluations en lotes
    all_evaluations = []

    for miner_idx, miner in enumerate(miners):
        # Asegurar que el agent_run_id sea único incluyendo el índice del miner
        agent_run_id = f"{validator_round_id}_run_{miner['uid']}_{miner_idx}"
        run_start = started_at + miner_idx * 30
        run_end = run_start + random.uniform(120, 180)

        # Calcular scores para este miner
        base_score = random.uniform(0.5, 0.95)
        avg_reward = base_score * random.uniform(0.8, 1.0)

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
            total_tasks=len(tasks),
            success_tasks=len(tasks),
            failed_tasks=0,
            meta={},
        )
        session.add(agent_run)
        await session.flush()

        # Crear 1 solution y 1 evaluation por cada tarea (relación 1-1)
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
                evaluation_score=evaluation_score,
                reward=reward,
                evaluation_time=eval_time,
                extra_info={},
            )
            session.add(evaluation)
            all_evaluations.append(evaluation)

            # Flush cada BATCH_SIZE evaluations para mejor rendimiento
            if len(all_evaluations) % BATCH_SIZE == 0:
                await session.flush()

    await session.flush()

    # Create execution_history records (empty for seed)
    from app.db.models import EvaluationExecutionHistoryORM

    print("  📝 Creando execution_history records...")
    for evaluation in all_evaluations:
        execution_history_record = EvaluationExecutionHistoryORM(
            evaluations_id=evaluation.id,
            execution_history=[],
        )
        session.add(execution_history_record)
    await session.flush()

    # 6. Crear datos en validator_round_summary_miners
    all_miner_rewards = {}
    for miner in miners:
        miner_evaluations = [e for e in all_evaluations if e.miner_uid == miner["uid"]]

        if miner_evaluations:
            local_avg_reward = sum(e.reward for e in miner_evaluations) / len(miner_evaluations)
            all_miner_rewards[miner["uid"]] = local_avg_reward

    # Calcular ranks
    sorted_miners = sorted(all_miner_rewards.items(), key=lambda x: x[1], reverse=True)
    rank_map = {uid: i + 1 for i, (uid, _) in enumerate(sorted_miners)}

    # Get subnet price
    from app.services.subnet_utils import get_price
    from app.config import settings

    try:
        subnet_price = get_price(netuid=settings.VALIDATOR_NETUID if hasattr(settings, "VALIDATOR_NETUID") else 36)
        if subnet_price <= 0:
            subnet_price = 0.0043
    except Exception:
        subnet_price = 0.0043

    # Crear summary records - verificar si ya existe antes de crear
    for miner in miners:
        miner_evaluations = [e for e in all_evaluations if e.miner_uid == miner["uid"]]
        if not miner_evaluations:
            continue

        # Verificar si ya existe un summary para este validator_round_id y miner_uid
        existing_summary = await session.scalar(
            select(ValidatorRoundSummaryORM).where(
                ValidatorRoundSummaryORM.validator_round_id == validator_round_id,
                ValidatorRoundSummaryORM.miner_uid == miner["uid"],
            )
        )

        if existing_summary:
            # Ya existe, actualizar en lugar de crear
            local_avg_reward = sum(e.reward for e in miner_evaluations) / len(miner_evaluations)
            local_avg_eval_score = sum(e.evaluation_score for e in miner_evaluations) / len(miner_evaluations)
            local_avg_eval_time = sum(e.evaluation_time for e in miner_evaluations) / len(miner_evaluations)
            local_tasks_received = len(set(e.task_id for e in miner_evaluations))
            local_tasks_success = len(set(e.task_id for e in miner_evaluations if e.evaluation_score >= 0.5))
            local_rank = rank_map.get(miner["uid"], 1)

            existing_summary.local_rank = local_rank
            existing_summary.local_avg_reward = local_avg_reward
            existing_summary.local_avg_eval_score = local_avg_eval_score
            existing_summary.local_avg_eval_time = local_avg_eval_time
            existing_summary.local_tasks_received = local_tasks_received
            existing_summary.local_tasks_success = local_tasks_success
            existing_summary.post_consensus_rank = local_rank
            existing_summary.post_consensus_avg_eval_score = local_avg_eval_score
            existing_summary.post_consensus_avg_eval_time = local_avg_eval_time
            existing_summary.post_consensus_tasks_received = local_tasks_received
            existing_summary.post_consensus_tasks_success = local_tasks_success
            if existing_summary.subnet_price is None:
                existing_summary.subnet_price = subnet_price
        else:
            # No existe, crear nuevo
            local_avg_reward = sum(e.reward for e in miner_evaluations) / len(miner_evaluations)
            local_avg_eval_score = sum(e.evaluation_score for e in miner_evaluations) / len(miner_evaluations)
            local_avg_eval_time = sum(e.evaluation_time for e in miner_evaluations) / len(miner_evaluations)
            local_tasks_received = len(set(e.task_id for e in miner_evaluations))
            local_tasks_success = len(set(e.task_id for e in miner_evaluations if e.evaluation_score >= 0.5))
            local_rank = rank_map.get(miner["uid"], 1)

            summary = ValidatorRoundSummaryORM(
                validator_round_id=validator_round_id,
                miner_uid=miner["uid"],
                miner_hotkey=miner["hotkey"],
                local_rank=local_rank,
                local_avg_reward=local_avg_reward,
                local_avg_eval_score=local_avg_eval_score,
                local_avg_eval_time=local_avg_eval_time,
                local_tasks_received=local_tasks_received,
                local_tasks_success=local_tasks_success,
                post_consensus_rank=local_rank,  # Temporal, se actualizará
                post_consensus_avg_reward=0.0,  # Se calculará después
                post_consensus_avg_eval_score=local_avg_eval_score,
                post_consensus_avg_eval_time=local_avg_eval_time,
                post_consensus_tasks_received=local_tasks_received,
                post_consensus_tasks_success=local_tasks_success,
                weight=0.0,  # Se calculará después
                subnet_price=subnet_price,
            )
            session.add(summary)

    await session.flush()

    return len(all_evaluations)


async def calculate_post_consensus_scores(session: AsyncSession, round_number: int, validators: List[Dict]):
    """Calcula los scores post-consensus agregando datos de todos los validators del round."""
    validator_round_ids = [f"round_{round_number}_validator_{v['uid']}" for v in validators]

    summaries_by_miner: Dict[int, List[ValidatorRoundSummaryORM]] = {}
    total_stake = 0.0

    for validator_round_id in validator_round_ids:
        result = await session.execute(select(ValidatorRoundSummaryORM).where(ValidatorRoundSummaryORM.validator_round_id == validator_round_id))
        summaries = list(result.scalars().all())

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
    consensus_scores: Dict[int, float] = {}
    for miner_uid, summaries in summaries_by_miner.items():
        weighted_sum = 0.0
        total_weight = 0.0

        for summary in summaries:
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

    # Actualizar todos los summaries
    for miner_uid, summaries in summaries_by_miner.items():
        consensus_reward = consensus_scores.get(miner_uid, 0.0)
        consensus_rank = consensus_rank_map.get(miner_uid, 1)

        total_eval_score = sum(s.local_avg_eval_score for s in summaries)
        total_eval_time = sum(s.local_avg_eval_time for s in summaries)
        total_tasks_received = sum(s.local_tasks_received for s in summaries)
        total_tasks_success = sum(s.local_tasks_success for s in summaries)
        count = len(summaries)

        for summary in summaries:
            summary.post_consensus_rank = consensus_rank
            summary.post_consensus_avg_reward = consensus_reward
            summary.post_consensus_avg_eval_score = total_eval_score / count if count > 0 else 0.0
            summary.post_consensus_avg_eval_time = total_eval_time / count if count > 0 else 0.0
            summary.post_consensus_tasks_received = total_tasks_received
            summary.post_consensus_tasks_success = total_tasks_success

            total_consensus = sum(consensus_scores.values())
            summary.weight = consensus_reward / total_consensus if total_consensus > 0 else 0.0

    await session.commit()


async def main():
    """Función principal del stress test."""
    print("=" * 70)
    print("🔥 STRESS TEST - GENERACIÓN DE DATOS MASIVOS")
    print("=" * 70)
    print("\n📊 Configuración:")
    print(f"   - Rounds: {NUM_ROUNDS}")
    print(f"   - Validators: {NUM_VALIDATORS}")
    print(f"   - Miners: {NUM_MINERS}")
    print(f"   - Tareas por round: {TASKS_PER_ROUND}")
    print(f"   - Batch size: {BATCH_SIZE}")

    total_expected = NUM_ROUNDS * NUM_VALIDATORS * NUM_MINERS * TASKS_PER_ROUND
    print("\n📈 Volumen esperado:")
    print(f"   - Evaluations: ~{total_expected:,}")
    print(f"   - Tasks: ~{NUM_ROUNDS * NUM_VALIDATORS * TASKS_PER_ROUND:,}")
    print(f"   - Summary records: ~{NUM_ROUNDS * NUM_VALIDATORS * NUM_MINERS:,}")
    print()

    try:
        start_time = time.time()

        # 1. Limpiar todas las tablas
        await clear_all_tables()

        # 2. Generar datos base
        validators = generate_validators(NUM_VALIDATORS)
        miners = generate_miners(NUM_MINERS)

        # 3. Crear rounds
        async with AsyncSessionLocal() as session:
            total_evaluations = 0

            for round_num in range(1, NUM_ROUNDS + 1):
                if round_num % 10 == 0:
                    elapsed = time.time() - start_time
                    print(f"\n⏱️  Progreso: Round {round_num}/{NUM_ROUNDS} ({elapsed:.1f}s)")

                tasks = generate_tasks(TASKS_PER_ROUND, round_num)

                # Crear round para cada validator
                for validator in validators:
                    try:
                        evaluations_count = await create_validator_round(session, validator, round_num, tasks, miners)
                        total_evaluations += evaluations_count
                    except Exception as e:
                        print(f"  ⚠️  Error creando round {round_num} para validator {validator['name']}: {e}")
                        await session.rollback()
                        # Continuar con el siguiente validator
                        continue

                # Commit después de crear todos los validator rounds para este round
                try:
                    await session.commit()
                except Exception as e:
                    print(f"  ⚠️  Error en commit del round {round_num}: {e}")
                    await session.rollback()
                    continue

                # Calcular post-consensus scores (hace su propio commit)
                await calculate_post_consensus_scores(session, round_num, validators)

        # 4. Verificar datos creados
        async with AsyncSessionLocal() as session:
            print("\n" + "=" * 70)
            print("📊 RESUMEN FINAL")
            print("=" * 70)

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
                print(f"  ✓ {table}: {count:,} registros")

            elapsed = time.time() - start_time
            print(f"\n⏱️  Tiempo total: {elapsed:.2f} segundos")
            print(f"📈 Evaluations creadas: {total_evaluations:,}")
            print(f"⚡ Velocidad: {total_evaluations / elapsed:.0f} evaluations/segundo")
            print("\n✅ ¡Stress test completado exitosamente!")

    except Exception as e:
        print(f"\n❌ Error durante el stress test: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
