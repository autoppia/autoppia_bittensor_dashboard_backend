#!/usr/bin/env python3
"""
Corrige post_consensus_tasks_success (y tasks_received) en round_validator_miners
usando las tablas evaluations y miner_evaluation_runs como fuente de verdad.

Problema original:
  round_manager.round_rewards no se reseteaba entre rounds, acumulando rewards
  de rondas previas. Esto inflaba success_tasks al llamar _register_evaluated_commit,
  propagando valores incorrectos (ej. 6/20 en vez de 2/20) a post_consensus_json
  y luego a round_validator_miners.post_consensus_tasks_success.

Este script:
  1. Corrige miner_evaluation_runs.success_tasks desde evaluations (reward >= 0.5)
  2. Actualiza round_validator_miners.post_consensus_tasks_success desde los runs corregidos
  3. Para rondas reutilizadas (sin evaluation runs), copia el valor de la ronda fuente
  4. Corrige round_summary.tasks_success / tasks_evaluated
  5. Hace todo en modo --dry-run por defecto (no modifica nada)

Uso:
  cd autoppia_bittensor_dashboard_backend
  python scripts/maintenance/backfill_tasks_success_from_evaluations.py --dry-run
  python scripts/maintenance/backfill_tasks_success_from_evaluations.py
"""

import asyncio
import os
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[2]
os.chdir(root)
sys.path.insert(0, str(root))

from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

from app.db.session import AsyncSessionLocal  # noqa: E402

DRY_RUN = "--dry-run" in sys.argv


# ─────────────────────────────────────────────────────────────────────────────
# Paso 1: corregir miner_evaluation_runs.success_tasks desde evaluations
# ─────────────────────────────────────────────────────────────────────────────
STEP1_SQL = """
WITH correct AS (
    SELECT
        mer.agent_run_id,
        mer.total_tasks,
        COUNT(e.id) FILTER (WHERE e.reward >= 0.5) AS correct_success,
        COUNT(e.id) FILTER (WHERE e.reward < 0.5)  AS correct_failed
    FROM miner_evaluation_runs mer
    JOIN evaluations e ON e.agent_run_id = mer.agent_run_id
    WHERE mer.total_tasks > 0
    GROUP BY mer.agent_run_id, mer.total_tasks
)
SELECT
    c.agent_run_id,
    mer.success_tasks    AS old_success,
    c.correct_success    AS new_success,
    mer.failed_tasks     AS old_failed,
    c.correct_failed     AS new_failed
FROM correct c
JOIN miner_evaluation_runs mer ON mer.agent_run_id = c.agent_run_id
WHERE c.correct_success <> mer.success_tasks
   OR c.correct_failed  <> mer.failed_tasks
ORDER BY c.agent_run_id
"""

STEP1_UPDATE_SQL = """
UPDATE miner_evaluation_runs mer
SET
    success_tasks = c.correct_success,
    failed_tasks  = c.correct_failed,
    updated_at    = NOW()
FROM (
    SELECT
        mer2.agent_run_id,
        COUNT(e.id) FILTER (WHERE e.reward >= 0.5) AS correct_success,
        COUNT(e.id) FILTER (WHERE e.reward < 0.5)  AS correct_failed
    FROM miner_evaluation_runs mer2
    JOIN evaluations e ON e.agent_run_id = mer2.agent_run_id
    WHERE mer2.total_tasks > 0
    GROUP BY mer2.agent_run_id
) c
WHERE mer.agent_run_id = c.agent_run_id
  AND (c.correct_success <> mer.success_tasks OR c.correct_failed <> mer.failed_tasks)
"""


# ─────────────────────────────────────────────────────────────────────────────
# Paso 2: actualizar round_validator_miners desde miner_evaluation_runs corregidos
# Para rondas con evaluación real
# ─────────────────────────────────────────────────────────────────────────────
STEP2_DIFF_SQL = """
WITH per_rv_miner AS (
    SELECT
        rv.round_validator_id,
        mer.miner_uid,
        SUM(mer.total_tasks)   AS total_tasks,
        SUM(mer.success_tasks) AS success_tasks
    FROM miner_evaluation_runs mer
    JOIN round_validators rv ON rv.validator_round_id = mer.validator_round_id
    GROUP BY rv.round_validator_id, mer.miner_uid
)
SELECT
    rvm.id,
    rvm.round_validator_id,
    rvm.miner_uid,
    rvm.post_consensus_tasks_received AS old_received,
    p.total_tasks                     AS new_received,
    rvm.post_consensus_tasks_success  AS old_success,
    p.success_tasks                   AS new_success
FROM per_rv_miner p
JOIN round_validator_miners rvm
  ON rvm.round_validator_id = p.round_validator_id
 AND rvm.miner_uid          = p.miner_uid
WHERE p.success_tasks <> COALESCE(rvm.post_consensus_tasks_success, -1)
   OR p.total_tasks   <> COALESCE(rvm.post_consensus_tasks_received, -1)
ORDER BY rvm.round_validator_id, rvm.miner_uid
"""

STEP2_UPDATE_SQL = """
UPDATE round_validator_miners rvm
SET
    post_consensus_tasks_success  = p.success_tasks,
    post_consensus_tasks_received = p.total_tasks,
    updated_at = NOW()
FROM (
    SELECT
        rv.round_validator_id,
        mer.miner_uid,
        SUM(mer.total_tasks)   AS total_tasks,
        SUM(mer.success_tasks) AS success_tasks
    FROM miner_evaluation_runs mer
    JOIN round_validators rv ON rv.validator_round_id = mer.validator_round_id
    GROUP BY rv.round_validator_id, mer.miner_uid
) p
WHERE rvm.round_validator_id = p.round_validator_id
  AND rvm.miner_uid          = p.miner_uid
  AND (
      p.success_tasks <> COALESCE(rvm.post_consensus_tasks_success, -1)
   OR p.total_tasks   <> COALESCE(rvm.post_consensus_tasks_received, -1)
  )
"""


# ─────────────────────────────────────────────────────────────────────────────
# Paso 3: rondas reutilizadas – copiar desde la ronda fuente más reciente
#   Reusada = round_validators existe pero NO hay miner_evaluation_runs en ningún
#   validator de esa round_id
# ─────────────────────────────────────────────────────────────────────────────
STEP3_DIFF_SQL = """
WITH reused_rvs AS (
    -- Round_validators de rondas sin evaluation runs
    SELECT rv.round_validator_id, rv.round_id
    FROM round_validators rv
    WHERE NOT EXISTS (
        SELECT 1 FROM miner_evaluation_runs mer
        WHERE mer.validator_round_id = rv.validator_round_id
    )
),
source_rounds AS (
    -- Para cada round reutilizada, la ronda fuente más reciente de la misma season
    SELECT DISTINCT ON (r_reused.round_id)
        r_reused.round_id    AS reused_round_id,
        r_src.round_id       AS source_round_id
    FROM reused_rvs rr
    JOIN rounds r_reused ON r_reused.round_id = rr.round_id
    JOIN rounds r_src    ON r_src.season_id                = r_reused.season_id
                        AND r_src.round_number_in_season   < r_reused.round_number_in_season
    WHERE EXISTS (
        SELECT 1 FROM round_validators rv_src
        JOIN miner_evaluation_runs mer_src ON mer_src.validator_round_id = rv_src.validator_round_id
        WHERE rv_src.round_id = r_src.round_id
    )
    ORDER BY r_reused.round_id, r_src.round_number_in_season DESC
)
SELECT
    rvm_reused.id,
    rvm_reused.round_validator_id     AS reused_rv_id,
    rvm_reused.miner_uid,
    rvm_reused.post_consensus_tasks_success  AS old_success,
    rvm_reused.post_consensus_tasks_received AS old_received,
    rvm_src.post_consensus_tasks_success     AS new_success,
    rvm_src.post_consensus_tasks_received    AS new_received
FROM source_rounds sr
JOIN reused_rvs rr ON rr.round_id = sr.reused_round_id
JOIN round_validator_miners rvm_reused ON rvm_reused.round_validator_id = rr.round_validator_id
JOIN round_validators rv_src ON rv_src.round_id = sr.source_round_id
JOIN round_validator_miners rvm_src
  ON rvm_src.round_validator_id = rv_src.round_validator_id
 AND rvm_src.miner_uid          = rvm_reused.miner_uid
WHERE rvm_src.post_consensus_tasks_success IS NOT NULL
  AND (
      rvm_src.post_consensus_tasks_success <> COALESCE(rvm_reused.post_consensus_tasks_success, -1)
   OR rvm_src.post_consensus_tasks_received <> COALESCE(rvm_reused.post_consensus_tasks_received, -1)
  )
ORDER BY rvm_reused.round_validator_id, rvm_reused.miner_uid
"""

STEP3_UPDATE_SQL = """
UPDATE round_validator_miners rvm_reused
SET
    post_consensus_tasks_success  = src.post_consensus_tasks_success,
    post_consensus_tasks_received = src.post_consensus_tasks_received,
    updated_at = NOW()
FROM (
    WITH reused_rvs AS (
        SELECT rv.round_validator_id, rv.round_id
        FROM round_validators rv
        WHERE NOT EXISTS (
            SELECT 1 FROM miner_evaluation_runs mer
            WHERE mer.validator_round_id = rv.validator_round_id
        )
    ),
    source_rounds AS (
        SELECT DISTINCT ON (r_reused.round_id)
            r_reused.round_id AS reused_round_id,
            r_src.round_id    AS source_round_id
        FROM reused_rvs rr
        JOIN rounds r_reused ON r_reused.round_id = rr.round_id
        JOIN rounds r_src    ON r_src.season_id                = r_reused.season_id
                            AND r_src.round_number_in_season   < r_reused.round_number_in_season
        WHERE EXISTS (
            SELECT 1 FROM round_validators rv_src
            JOIN miner_evaluation_runs mer_src ON mer_src.validator_round_id = rv_src.validator_round_id
            WHERE rv_src.round_id = r_src.round_id
        )
        ORDER BY r_reused.round_id, r_src.round_number_in_season DESC
    )
    -- Pick the best source row per (reused_rv, miner_uid):
    -- highest post_consensus_tasks_success (the "real" evaluation validator)
    SELECT DISTINCT ON (rr.round_validator_id, rvm_src.miner_uid)
        rr.round_validator_id   AS reused_rv_id,
        rvm_src.miner_uid,
        rvm_src.post_consensus_tasks_success,
        rvm_src.post_consensus_tasks_received
    FROM source_rounds sr
    JOIN reused_rvs rr ON rr.round_id = sr.reused_round_id
    JOIN round_validators rv_src ON rv_src.round_id = sr.source_round_id
    JOIN round_validator_miners rvm_src ON rvm_src.round_validator_id = rv_src.round_validator_id
    WHERE rvm_src.post_consensus_tasks_success IS NOT NULL
    ORDER BY rr.round_validator_id, rvm_src.miner_uid,
             rvm_src.post_consensus_tasks_success DESC NULLS LAST
) src
WHERE rvm_reused.round_validator_id = src.reused_rv_id
  AND rvm_reused.miner_uid          = src.miner_uid
  AND (
      src.post_consensus_tasks_success  <> COALESCE(rvm_reused.post_consensus_tasks_success, -1)
   OR src.post_consensus_tasks_received <> COALESCE(rvm_reused.post_consensus_tasks_received, -1)
  )
"""


# ─────────────────────────────────────────────────────────────────────────────
# Paso 4: round_summary.tasks_success / tasks_evaluated  (agregado por round)
# ─────────────────────────────────────────────────────────────────────────────
STEP4_DIFF_SQL = """
WITH round_agg AS (
    -- Sum per round across ALL validator rows (the burn miner has success=0 anyway)
    SELECT
        rv.round_id,
        SUM(rvm.post_consensus_tasks_received) AS tasks_evaluated,
        SUM(rvm.post_consensus_tasks_success)  AS tasks_success
    FROM round_validators rv
    JOIN round_validator_miners rvm ON rvm.round_validator_id = rv.round_validator_id
    GROUP BY rv.round_id
)
SELECT
    rs.round_id,
    rs.tasks_evaluated AS old_evaluated,
    ra.tasks_evaluated AS new_evaluated,
    rs.tasks_success   AS old_success,
    ra.tasks_success   AS new_success
FROM round_agg ra
JOIN round_summary rs ON rs.round_id = ra.round_id
WHERE ra.tasks_success   <> COALESCE(rs.tasks_success, -1)
   OR ra.tasks_evaluated <> COALESCE(rs.tasks_evaluated, -1)
ORDER BY rs.round_id
"""

STEP4_UPDATE_SQL = """
UPDATE round_summary rs
SET
    tasks_evaluated = ra.tasks_evaluated,
    tasks_success   = ra.tasks_success,
    updated_at      = NOW()
FROM (
    SELECT
        rv.round_id,
        SUM(rvm.post_consensus_tasks_received) AS tasks_evaluated,
        SUM(rvm.post_consensus_tasks_success)  AS tasks_success
    FROM round_validators rv
    JOIN round_validator_miners rvm ON rvm.round_validator_id = rv.round_validator_id
    GROUP BY rv.round_id
) ra
WHERE rs.round_id = ra.round_id
  AND (
      ra.tasks_success   <> COALESCE(rs.tasks_success, -1)
   OR ra.tasks_evaluated <> COALESCE(rs.tasks_evaluated, -1)
  )
"""


async def run(session: AsyncSession) -> None:
    tag = "[DRY-RUN]" if DRY_RUN else "[UPDATE]"

    # ── Paso 1 ────────────────────────────────────────────────────────────────
    print("\n=== Paso 1: corregir miner_evaluation_runs.success_tasks ===")
    rows = (await session.execute(text(STEP1_SQL))).mappings().all()
    if not rows:
        print("  Sin cambios necesarios.")
    else:
        for r in rows:
            print(f"  {tag} run {r['agent_run_id']}: success {r['old_success']} → {r['new_success']}, failed {r['old_failed']} → {r['new_failed']}")
        if not DRY_RUN:
            result = await session.execute(text(STEP1_UPDATE_SQL))
            print(f"  Actualizadas {result.rowcount} filas.")

    # ── Paso 2 ────────────────────────────────────────────────────────────────
    print("\n=== Paso 2: actualizar round_validator_miners (rondas con evaluación real) ===")
    rows = (await session.execute(text(STEP2_DIFF_SQL))).mappings().all()
    if not rows:
        print("  Sin cambios necesarios.")
    else:
        for r in rows:
            print(f"  {tag} rv={r['round_validator_id']} miner={r['miner_uid']}: success {r['old_success']} → {r['new_success']}, received {r['old_received']} → {r['new_received']}")
        if not DRY_RUN:
            result = await session.execute(text(STEP2_UPDATE_SQL))
            print(f"  Actualizadas {result.rowcount} filas.")

    # ── Paso 3 ────────────────────────────────────────────────────────────────
    print("\n=== Paso 3: propagar a rondas reutilizadas (sin evaluation runs) ===")
    rows = (await session.execute(text(STEP3_DIFF_SQL))).mappings().all()
    if not rows:
        print("  Sin cambios necesarios.")
    else:
        for r in rows:
            print(f"  {tag} rv={r['reused_rv_id']} miner={r['miner_uid']}: success {r['old_success']} → {r['new_success']}, received {r['old_received']} → {r['new_received']}")
        if not DRY_RUN:
            result = await session.execute(text(STEP3_UPDATE_SQL))
            print(f"  Actualizadas {result.rowcount} filas.")

    # ── Paso 4 ────────────────────────────────────────────────────────────────
    print("\n=== Paso 4: corregir round_summary.tasks_success / tasks_evaluated ===")
    rows = (await session.execute(text(STEP4_DIFF_SQL))).mappings().all()
    if not rows:
        print("  Sin cambios necesarios.")
    else:
        for r in rows:
            print(f"  {tag} round_id={r['round_id']}: tasks_success {r['old_success']} → {r['new_success']}, tasks_evaluated {r['old_evaluated']} → {r['new_evaluated']}")
        if not DRY_RUN:
            result = await session.execute(text(STEP4_UPDATE_SQL))
            print(f"  Actualizadas {result.rowcount} filas.")

    if not DRY_RUN:
        await session.commit()
        print("\n✅ Commit realizado.")
    else:
        print("\n[DRY-RUN] Sin cambios aplicados. Usa sin --dry-run para aplicar.")


def main() -> None:
    if DRY_RUN:
        print("Modo DRY-RUN: solo muestra los cambios, no modifica nada.")
    else:
        print("Modo UPDATE: aplicará los cambios a la base de datos.")

    async def _run() -> None:
        async with AsyncSessionLocal() as session:
            await run(session)

    asyncio.run(_run())


if __name__ == "__main__":
    main()
