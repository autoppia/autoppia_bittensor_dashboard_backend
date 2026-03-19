#!/usr/bin/env python3
"""
Auditoria de consistencia (10 puntos) para rounds/miner runs/evaluations/task logs.

Uso:
  cd autoppia_bittensor_dashboard_backend
  python scripts/maintenance/audit_rounds_10points.py
"""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass

import asyncpg


@dataclass
class CheckResult:
    name: str
    status: str  # OK | WARN | FAIL
    details: str


def _fmt_rows(rows) -> str:
    if not rows:
        return "[]"
    return "; ".join(str(tuple(r)) for r in rows)


def _nested(obj, *keys):
    cur = obj
    for k in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
    return cur


def _as_dict(v):
    if isinstance(v, dict):
        return v
    if isinstance(v, str):
        try:
            parsed = json.loads(v)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def _require_env(*names: str) -> str:
    for name in names:
        value = os.getenv(name)
        if value:
            return value
    raise RuntimeError(f"Missing required environment variable. Set one of: {', '.join(names)}")


async def _scalar(conn: asyncpg.Connection, sql: str) -> int:
    return int((await conn.fetchval(sql)) or 0)


async def run_audit() -> list[CheckResult]:
    out: list[CheckResult] = []
    host = os.getenv("POSTGRES_HOST_DEVELOPMENT", os.getenv("POSTGRES_HOST", "127.0.0.1"))
    port = int(os.getenv("POSTGRES_PORT_DEVELOPMENT", os.getenv("POSTGRES_PORT", "5432")))
    user = os.getenv("POSTGRES_USER_DEVELOPMENT", os.getenv("POSTGRES_USER", "autoppia_user"))
    password = _require_env("POSTGRES_PASSWORD_DEVELOPMENT", "POSTGRES_PASSWORD")
    database = os.getenv("POSTGRES_DB_DEVELOPMENT", os.getenv("POSTGRES_DB", "autoppia_dev"))

    conn = await asyncpg.connect(
        host=host,
        port=port,
        user=user,
        password=password,
        database=database,
    )
    try:
        # 1) Rounds + outcomes
        rounds = await conn.fetch(
            """
            SELECT s.season_number, r.round_number_in_season, r.status, r.consensus_status,
                   CASE WHEN ro.round_id IS NULL THEN false ELSE true END AS has_outcome
            FROM rounds r
            JOIN seasons s ON s.season_id = r.season_id
            LEFT JOIN round_outcomes ro ON ro.round_id = r.round_id
            ORDER BY s.season_number, r.round_number_in_season
            """
        )
        finished_finalized_without_outcome = await conn.fetchval(
            """
            SELECT count(*)
            FROM rounds r
            LEFT JOIN round_outcomes ro ON ro.round_id = r.round_id
            WHERE lower(r.status) = 'finished'
              AND lower(coalesce(r.consensus_status,'')) = 'finalized'
              AND ro.round_id IS NULL
            """
        )
        status = "OK" if int(finished_finalized_without_outcome or 0) == 0 else "FAIL"
        out.append(
            CheckResult(
                name="1) Rounds/Outcomes",
                status=status,
                details=f"rounds={_fmt_rows(rounds)} | finished+finalized sin outcome={finished_finalized_without_outcome}",
            )
        )

        # 2) Integridad relacional principal
        eval_without_task = await _scalar(
            conn,
            "SELECT count(*) FROM evaluations e LEFT JOIN tasks t ON t.task_id=e.task_id WHERE t.task_id IS NULL",
        )
        eval_without_solution = await _scalar(
            conn,
            "SELECT count(*) FROM evaluations e LEFT JOIN task_solutions ts ON ts.solution_id=e.task_solution_id WHERE ts.solution_id IS NULL",
        )
        eval_without_run = await _scalar(
            conn,
            "SELECT count(*) FROM evaluations e LEFT JOIN miner_evaluation_runs r ON r.agent_run_id=e.agent_run_id WHERE r.agent_run_id IS NULL",
        )
        sol_without_eval = await _scalar(
            conn,
            "SELECT count(*) FROM task_solutions ts LEFT JOIN evaluations e ON e.task_solution_id=ts.solution_id WHERE e.task_solution_id IS NULL",
        )
        log_without_task = await _scalar(
            conn,
            "SELECT count(*) FROM task_execution_logs l LEFT JOIN tasks t ON t.task_id=l.task_id WHERE t.task_id IS NULL",
        )
        log_without_run = await _scalar(
            conn,
            "SELECT count(*) FROM task_execution_logs l LEFT JOIN miner_evaluation_runs r ON r.agent_run_id=l.agent_run_id WHERE r.agent_run_id IS NULL",
        )
        llm_without_eval = await _scalar(
            conn,
            "SELECT count(*) FROM evaluation_llm_usage u LEFT JOIN evaluations e ON e.evaluation_id=u.evaluation_id WHERE e.evaluation_id IS NULL",
        )
        total_orphans = eval_without_task + eval_without_solution + eval_without_run + sol_without_eval + log_without_task + log_without_run + llm_without_eval
        out.append(
            CheckResult(
                name="2) Integridad referencial",
                status="OK" if total_orphans == 0 else "FAIL",
                details=(
                    f"eval_without_task={eval_without_task}, eval_without_solution={eval_without_solution}, "
                    f"eval_without_run={eval_without_run}, solution_without_eval={sol_without_eval}, "
                    f"log_without_task={log_without_task}, log_without_run={log_without_run}, "
                    f"llm_without_eval={llm_without_eval}"
                ),
            )
        )

        # 3) Totales por round (runs/tasks/evals/logs/llm)
        per_round = await conn.fetch(
            """
                    WITH base AS (
                      SELECT r.round_id, s.season_number, r.round_number_in_season
                      FROM rounds r JOIN seasons s ON s.season_id=r.season_id
                    ),
                    rv AS (
                      SELECT round_id, count(*) validators
                      FROM round_validators GROUP BY round_id
                    ),
                    runs AS (
                      SELECT rv.round_id,
                             count(*) runs,
                             count(*) FILTER (WHERE mer.is_reused) reused_runs,
                             coalesce(sum(mer.total_tasks),0) total_tasks,
                             coalesce(sum(mer.success_tasks),0) success_tasks,
                             coalesce(sum(mer.failed_tasks),0) failed_tasks
                      FROM round_validators rv
                      LEFT JOIN miner_evaluation_runs mer ON mer.round_validator_id=rv.round_validator_id
                      GROUP BY rv.round_id
                    ),
                    evals AS (
                      SELECT rv.round_id, count(*) evaluations
                      FROM round_validators rv
                      JOIN miner_evaluation_runs mer ON mer.round_validator_id=rv.round_validator_id
                      JOIN evaluations e ON e.agent_run_id=mer.agent_run_id
                      GROUP BY rv.round_id
                    )
                    SELECT b.season_number, b.round_number_in_season,
                           coalesce(rv.validators,0) validators,
                           coalesce(runs.runs,0) runs,
                           coalesce(runs.reused_runs,0) reused_runs,
                           coalesce(runs.total_tasks,0) total_tasks,
                           coalesce(runs.success_tasks,0) success_tasks,
                           coalesce(runs.failed_tasks,0) failed_tasks,
                           coalesce(evals.evaluations,0) evaluations
                    FROM base b
                    LEFT JOIN rv USING(round_id)
                    LEFT JOIN runs USING(round_id)
                    LEFT JOIN evals USING(round_id)
                    ORDER BY b.season_number, b.round_number_in_season
            """
        )
        out.append(
            CheckResult(
                name="3) Totales por round",
                status="OK",
                details=_fmt_rows(per_round),
            )
        )

        # 4) Reused: source valido + no cross-season
        reused_missing_source = await _scalar(
            conn,
            """
            SELECT count(*)
            FROM miner_evaluation_runs r
            LEFT JOIN miner_evaluation_runs src ON src.agent_run_id = r.reused_from_agent_run_id
            WHERE r.is_reused = true
              AND src.agent_run_id IS NULL
            """,
        )
        cross_season_reused = await _scalar(
            conn,
            """
            SELECT count(*)
            FROM miner_evaluation_runs tgt
            JOIN miner_evaluation_runs src ON src.agent_run_id = tgt.reused_from_agent_run_id
            JOIN round_validators rv_tgt ON rv_tgt.round_validator_id = tgt.round_validator_id
            JOIN round_validators rv_src ON rv_src.round_validator_id = src.round_validator_id
            JOIN rounds r_tgt ON r_tgt.round_id = rv_tgt.round_id
            JOIN rounds r_src ON r_src.round_id = rv_src.round_id
            JOIN seasons s_tgt ON s_tgt.season_id = r_tgt.season_id
            JOIN seasons s_src ON s_src.season_id = r_src.season_id
            WHERE tgt.is_reused = true
              AND s_tgt.season_number <> s_src.season_number
            """,
        )
        status = "OK" if reused_missing_source == 0 and cross_season_reused == 0 else "FAIL"
        out.append(
            CheckResult(
                name="4) Reused coherente",
                status=status,
                details=f"reused_missing_source={reused_missing_source}, cross_season_reused={cross_season_reused}",
            )
        )

        # 5) Miners "participantes" por round/validator vs filas totales
        participants = await conn.fetch(
            """
                    SELECT s.season_number, r.round_number_in_season, rv.validator_uid,
                           count(*) AS miner_rows,
                           count(*) FILTER (WHERE coalesce(rvm.local_tasks_received,0) > 0) AS local_participants,
                           count(*) FILTER (WHERE coalesce(rvm.post_consensus_tasks_received,0) > 0) AS post_participants
                    FROM round_validator_miners rvm
                    JOIN round_validators rv ON rv.round_validator_id = rvm.round_validator_id
                    JOIN rounds r ON r.round_id = rvm.round_id
                    JOIN seasons s ON s.season_id = r.season_id
                    GROUP BY s.season_number, r.round_number_in_season, rv.validator_uid
                    ORDER BY s.season_number, r.round_number_in_season, rv.validator_uid
            """
        )
        out.append(
            CheckResult(
                name="5) Miners en tabla por validator",
                status="OK",
                details=_fmt_rows(participants),
            )
        )

        # 6) Dos miners evaluados por round en runs (si aplica)
        miners_in_runs = await conn.fetch(
            """
                    SELECT s.season_number, r.round_number_in_season,
                           count(DISTINCT mer.miner_uid) FILTER (WHERE mer.total_tasks > 0) miners_with_tasks
                    FROM rounds r
                    JOIN seasons s ON s.season_id = r.season_id
                    JOIN round_validators rv ON rv.round_id = r.round_id
                    JOIN miner_evaluation_runs mer ON mer.round_validator_id = rv.round_validator_id
                    GROUP BY s.season_number, r.round_number_in_season
                    ORDER BY s.season_number, r.round_number_in_season
            """
        )
        out.append(
            CheckResult(
                name="6) Miners con tareas por round",
                status="OK",
                details=_fmt_rows(miners_in_runs),
            )
        )

        # 7) Timeouts y scores
        timeout_stats = await conn.fetch(
            """
                    SELECT s.season_number, r.round_number_in_season, e.miner_uid,
                           count(*) evals,
                           count(*) FILTER (WHERE e.zero_reason='task_timeout') timeout_evals,
                           round(avg(e.evaluation_score)::numeric,6) avg_eval_score,
                           round(avg(e.reward)::numeric,6) avg_reward
                    FROM evaluations e
                    JOIN round_validators rv ON rv.validator_round_id=e.validator_round_id
                    JOIN rounds r ON r.round_id=rv.round_id
                    JOIN seasons s ON s.season_id=r.season_id
                    GROUP BY s.season_number, r.round_number_in_season, e.miner_uid
                    ORDER BY s.season_number, r.round_number_in_season, e.miner_uid
            """
        )
        out.append(
            CheckResult(
                name="7) Timeouts/Score por miner",
                status="OK",
                details=_fmt_rows(timeout_stats),
            )
        )

        # 8) LLM usage / coste
        llm_stats = await conn.fetch(
            """
                    SELECT s.season_number, r.round_number_in_season,
                           count(*) llm_rows,
                           coalesce(sum(u.tokens),0) total_tokens,
                           round(coalesce(sum(u.cost),0)::numeric,6) total_cost
                    FROM evaluation_llm_usage u
                    JOIN evaluations e ON e.evaluation_id=u.evaluation_id
                    JOIN round_validators rv ON rv.validator_round_id=e.validator_round_id
                    JOIN rounds r ON r.round_id=rv.round_id
                    JOIN seasons s ON s.season_id=r.season_id
                    GROUP BY s.season_number, r.round_number_in_season
                    ORDER BY s.season_number, r.round_number_in_season
            """
        )
        out.append(
            CheckResult(
                name="8) LLM usage/coste",
                status="OK",
                details=_fmt_rows(llm_stats),
            )
        )

        # 9) Post-consensus coherente con runs (tasks/success/avg_score)
        post_consensus_mismatch = await conn.fetch(
            """
            WITH agg AS (
              SELECT rv.round_id, mer.miner_uid,
                     sum(mer.total_tasks) AS exp_tasks,
                     sum(mer.success_tasks) AS exp_success,
                     avg(mer.average_score) AS exp_avg_score
              FROM round_validators rv
              JOIN miner_evaluation_runs mer ON mer.round_validator_id=rv.round_validator_id
              GROUP BY rv.round_id, mer.miner_uid
            )
            SELECT s.season_number, r.round_number_in_season, rv.validator_uid, rvm.miner_uid,
                   coalesce(rvm.post_consensus_tasks_received,0) got_tasks,
                   coalesce(a.exp_tasks,0) exp_tasks,
                   coalesce(rvm.post_consensus_tasks_success,0) got_success,
                   coalesce(a.exp_success,0) exp_success,
                   round(coalesce(rvm.post_consensus_avg_eval_score,0)::numeric,6) got_avg_score,
                   round(coalesce(a.exp_avg_score,0)::numeric,6) exp_avg_score
            FROM round_validator_miners rvm
            JOIN round_validators rv ON rv.round_validator_id=rvm.round_validator_id
            JOIN rounds r ON r.round_id=rvm.round_id
            JOIN seasons s ON s.season_id=r.season_id
            LEFT JOIN agg a ON a.round_id=rvm.round_id AND a.miner_uid=rvm.miner_uid
            WHERE coalesce(rvm.post_consensus_tasks_received,0) > 0
              AND (
                coalesce(rvm.post_consensus_tasks_received,0) <> coalesce(a.exp_tasks,0)
                OR coalesce(rvm.post_consensus_tasks_success,0) <> coalesce(a.exp_success,0)
                OR abs(coalesce(rvm.post_consensus_avg_eval_score,0) - coalesce(a.exp_avg_score,0)) > 0.00001
              )
            ORDER BY s.season_number, r.round_number_in_season, rv.validator_uid, rvm.miner_uid
            """
        )
        out.append(
            CheckResult(
                name="9) Post-consensus vs runs",
                status="OK" if len(post_consensus_mismatch) == 0 else "FAIL",
                details=f"mismatches={len(post_consensus_mismatch)}" + ("" if len(post_consensus_mismatch) == 0 else f" | {_fmt_rows(post_consensus_mismatch)}"),
            )
        )

        # 10) Summary/IPFS en rounds finished+finalized
        missing_summary_finished = await _scalar(
            conn,
            """
            SELECT count(*)
            FROM round_validators rv
            JOIN rounds r ON r.round_id = rv.round_id
            WHERE lower(r.status)='finished'
              AND lower(coalesce(r.consensus_status,''))='finalized'
              AND (rv.local_summary_json IS NULL OR rv.post_consensus_summary IS NULL)
            """,
        )
        missing_ipfs_finished = await _scalar(
            conn,
            """
            SELECT count(*)
            FROM round_validators rv
            JOIN rounds r ON r.round_id = rv.round_id
            WHERE lower(r.status)='finished'
              AND lower(coalesce(r.consensus_status,''))='finalized'
              AND (rv.ipfs_uploaded IS NULL OR rv.ipfs_downloaded IS NULL)
            """,
        )
        status = "OK" if missing_summary_finished == 0 and missing_ipfs_finished == 0 else "WARN"
        out.append(
            CheckResult(
                name="10) Summary/IPFS en rounds finalizados",
                status=status,
                details=f"missing_summary={missing_summary_finished}, missing_ipfs={missing_ipfs_finished}",
            )
        )

        # 11) IPFS uploaded/downloaded coherente entre validators
        finalized_rows = await conn.fetch(
            """
            SELECT s.season_number, r.round_number_in_season, rv.validator_uid, rv.ipfs_uploaded, rv.ipfs_downloaded
            FROM round_validators rv
            JOIN rounds r ON r.round_id = rv.round_id
            JOIN seasons s ON s.season_id = r.season_id
            WHERE lower(r.status)='finished' AND lower(coalesce(r.consensus_status,''))='finalized'
            ORDER BY s.season_number, r.round_number_in_season, rv.validator_uid
            """
        )
        uploaded_by_round: dict[tuple[int, int], set[str]] = {}
        downloaded_by_round: dict[tuple[int, int], set[str]] = {}
        bad_uploaded_shape = 0
        bad_downloaded_shape = 0
        for row in finalized_rows:
            key = (row["season_number"], row["round_number_in_season"])
            ipfs_up = _as_dict(row["ipfs_uploaded"])
            ipfs_down = _as_dict(row["ipfs_downloaded"])
            cid = _nested(ipfs_up, "cid")
            if not isinstance(cid, str) or not cid:
                bad_uploaded_shape += 1
            else:
                uploaded_by_round.setdefault(key, set()).add(cid)

            payloads = ipfs_down.get("payloads") if isinstance(ipfs_down, dict) else None
            if not isinstance(payloads, list):
                bad_downloaded_shape += 1
                continue
            for p in payloads:
                if not isinstance(p, dict):
                    continue
                pcid = p.get("cid")
                if isinstance(pcid, str) and pcid:
                    downloaded_by_round.setdefault(key, set()).add(pcid)

        missing_in_downloads = []
        unknown_downloaded = []
        for key, cids in uploaded_by_round.items():
            seen = downloaded_by_round.get(key, set())
            for c in cids:
                if c not in seen:
                    missing_in_downloads.append((key[0], key[1], c))
            for c in seen:
                if c not in cids:
                    unknown_downloaded.append((key[0], key[1], c))

        ipfs_ok = bad_uploaded_shape == 0 and bad_downloaded_shape == 0 and len(missing_in_downloads) == 0 and len(unknown_downloaded) == 0
        out.append(
            CheckResult(
                name="11) IPFS published/downloaded coherence",
                status="OK" if ipfs_ok else "FAIL",
                details=(
                    f"bad_uploaded_shape={bad_uploaded_shape}, bad_downloaded_shape={bad_downloaded_shape}, "
                    f"missing_uploaded_in_downloaded={len(missing_in_downloads)}, "
                    f"downloaded_not_uploaded={len(unknown_downloaded)}"
                ),
            )
        )

        # 12) Websites/tasks por round
        websites = await conn.fetch(
            """
                    SELECT s.season_number, r.round_number_in_season, t.web_project_id, count(*) tasks
                    FROM tasks t
                    JOIN round_validators rv ON rv.validator_round_id=t.validator_round_id
                    JOIN rounds r ON r.round_id=rv.round_id
                    JOIN seasons s ON s.season_id=r.season_id
                    GROUP BY s.season_number, r.round_number_in_season, t.web_project_id
                    ORDER BY s.season_number, r.round_number_in_season, t.web_project_id
            """
        )
        out.append(
            CheckResult(
                name="12) Tasks por website",
                status="OK",
                details=_fmt_rows(websites),
            )
        )

    finally:
        await conn.close()

    return out


async def main() -> None:
    print("Audit 10 puntos (DB) - set_weights ignorado (modo tests)\n")
    results = await run_audit()

    for r in results:
        print(f"[{r.status}] {r.name}")
        print(f"  {r.details}\n")

    fail = sum(1 for r in results if r.status == "FAIL")
    warn = sum(1 for r in results if r.status == "WARN")
    print(f"Resumen: FAIL={fail} WARN={warn} OK={len(results) - fail - warn}")


if __name__ == "__main__":
    asyncio.run(main())
