#!/usr/bin/env python3
"""
Repair stale season leadership fields in round_outcomes and seasons.

Usage:
  python scripts/maintenance/repair_round_outcomes_leadership.py
  python scripts/maintenance/repair_round_outcomes_leadership.py --season 1
  python scripts/maintenance/repair_round_outcomes_leadership.py --dry-run
"""

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

root = Path(__file__).resolve().parents[2]
os.chdir(root)
sys.path.insert(0, str(root))

from app.db.session import AsyncSessionLocal  # noqa: E402


def _to_json_dict(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            return {}
    return {}


def _apply_leadership_to_summary(
    *,
    summary: Dict[str, Any],
    winner_uid: Optional[int],
    winner_score: Optional[float],
    reigning_uid_before_round: Optional[int],
    reigning_score_before_round: Optional[float],
    top_candidate_uid: Optional[int],
    top_candidate_score: Optional[float],
    required_improvement_pct: float,
    dethroned: bool,
    leader_uid_after_round: Optional[int],
    leader_score_after_round: Optional[float],
) -> Dict[str, Any]:
    payload = dict(summary or {})
    round_summary = payload.get("round_summary") if isinstance(payload.get("round_summary"), dict) else {}
    decision = round_summary.get("decision") if isinstance(round_summary.get("decision"), dict) else {}
    winner_obj = round_summary.get("winner") if isinstance(round_summary.get("winner"), dict) else {}
    season_summary = payload.get("season_summary") if isinstance(payload.get("season_summary"), dict) else {}

    winner_obj["miner_uid"] = winner_uid
    winner_obj["score"] = winner_score
    round_summary["winner"] = winner_obj

    decision["reigning_uid_before_round"] = reigning_uid_before_round
    decision["reigning_score_before_round"] = reigning_score_before_round
    decision["top_candidate_uid"] = top_candidate_uid
    decision["top_candidate_score"] = top_candidate_score
    decision["required_improvement_pct"] = required_improvement_pct
    decision["dethroned"] = dethroned
    round_summary["decision"] = decision
    payload["round_summary"] = round_summary

    season_summary["required_improvement_pct"] = required_improvement_pct
    season_summary["winner_before_round_uid"] = reigning_uid_before_round
    season_summary["winner_before_round_score"] = reigning_score_before_round
    season_summary["candidate_uid"] = top_candidate_uid
    season_summary["candidate_score"] = top_candidate_score
    season_summary["winner_after_round_uid"] = leader_uid_after_round
    season_summary["winner_after_round_score"] = leader_score_after_round
    season_summary["current_winner_uid"] = leader_uid_after_round
    season_summary["dethroned"] = dethroned
    if dethroned:
        season_summary["round_result"] = "dethroned"
    elif leader_uid_after_round is not None:
        season_summary["round_result"] = "retained"
    else:
        season_summary["round_result"] = "no_winner"
    payload["season_summary"] = season_summary
    return payload


async def _repair_season(session: AsyncSession, season_id: int, dry_run: bool) -> int:
    rows = (
        (
            await session.execute(
                text(
                    """
                SELECT
                  ro.round_id,
                  r.round_number_in_season,
                  ro.winner_miner_uid,
                  ro.winner_score,
                  ro.required_improvement_pct,
                  ro.source_round_validator_id,
                  ro.post_consensus_summary
                FROM round_outcomes ro
                JOIN rounds r ON r.round_id = ro.round_id
                WHERE r.season_id = :season_id
                ORDER BY COALESCE(r.round_number_in_season, 2147483647), ro.round_id
                """
                ),
                {"season_id": season_id},
            )
        )
        .mappings()
        .all()
    )
    if not rows:
        return 0

    updates = 0
    leader_uid: Optional[int] = None
    leader_score: Optional[float] = None

    for row in rows:
        round_id = int(row["round_id"])
        winner_uid = int(row["winner_miner_uid"]) if row["winner_miner_uid"] is not None else None
        winner_score = float(row["winner_score"]) if row["winner_score"] is not None else None
        required_improvement_pct = float(row["required_improvement_pct"] or 0.05)

        reigning_uid_before_round = leader_uid
        reigning_score_before_round = leader_score
        top_candidate_uid = winner_uid
        top_candidate_score = winner_score
        dethroned = False

        if winner_uid is not None and winner_score is not None:
            if leader_uid is None or leader_score is None:
                leader_uid = winner_uid
                leader_score = winner_score
            elif winner_uid == leader_uid:
                leader_score = max(float(leader_score), float(winner_score))
            else:
                dethrone_threshold = float(leader_score) * (1.0 + required_improvement_pct)
                if float(winner_score) >= dethrone_threshold:
                    dethroned = True
                    leader_uid = winner_uid
                    leader_score = winner_score

        if dry_run:
            print(
                f"[dry-run] season_id={season_id} round_id={round_id} "
                f"reigning={reigning_uid_before_round}:{reigning_score_before_round} "
                f"candidate={top_candidate_uid}:{top_candidate_score} dethroned={dethroned} "
                f"leader_after={leader_uid}:{leader_score}"
            )
            updates += 1
            continue

        post_consensus_summary = _apply_leadership_to_summary(
            summary=_to_json_dict(row.get("post_consensus_summary")),
            winner_uid=winner_uid,
            winner_score=winner_score,
            reigning_uid_before_round=reigning_uid_before_round,
            reigning_score_before_round=reigning_score_before_round,
            top_candidate_uid=top_candidate_uid,
            top_candidate_score=top_candidate_score,
            required_improvement_pct=required_improvement_pct,
            dethroned=dethroned,
            leader_uid_after_round=leader_uid,
            leader_score_after_round=leader_score,
        )

        await session.execute(
            text(
                """
                UPDATE round_outcomes
                SET
                  reigning_miner_uid_before_round = :reigning_uid_before_round,
                  reigning_score_before_round = :reigning_score_before_round,
                  top_candidate_miner_uid = :top_candidate_uid,
                  top_candidate_score = :top_candidate_score,
                  required_improvement_pct = :required_improvement_pct,
                  dethroned = :dethroned,
                  post_consensus_summary = CAST(:post_consensus_summary AS JSONB),
                  updated_at = NOW()
                WHERE round_id = :round_id
                """
            ),
            {
                "round_id": round_id,
                "reigning_uid_before_round": reigning_uid_before_round,
                "reigning_score_before_round": reigning_score_before_round,
                "top_candidate_uid": top_candidate_uid,
                "top_candidate_score": top_candidate_score,
                "required_improvement_pct": required_improvement_pct,
                "dethroned": dethroned,
                "post_consensus_summary": json.dumps(post_consensus_summary),
            },
        )
        updates += 1

    leader_repo = None
    if leader_uid is not None:
        leader_repo = await session.scalar(
            text(
                """
                SELECT rvm.github_url
                FROM round_outcomes ro
                JOIN rounds r ON r.round_id = ro.round_id
                JOIN round_validator_miners rvm
                  ON rvm.round_validator_id = ro.source_round_validator_id
                 AND rvm.miner_uid = :leader_uid
                WHERE r.season_id = :season_id
                  AND rvm.github_url IS NOT NULL
                ORDER BY COALESCE(r.round_number_in_season, 2147483647) DESC, ro.round_id DESC
                LIMIT 1
                """
            ),
            {"season_id": season_id, "leader_uid": leader_uid},
        )

    if dry_run:
        print(f"[dry-run] season_id={season_id} leader={leader_uid}:{leader_score} repo={leader_repo}")
        return updates

    await session.execute(
        text(
            """
            UPDATE seasons
            SET
              leader_miner_uid = :leader_uid,
              leader_reward = :leader_score,
              leader_github_url = :leader_repo,
              updated_at = NOW()
            WHERE season_id = :season_id
            """
        ),
        {
            "season_id": season_id,
            "leader_uid": leader_uid,
            "leader_score": leader_score,
            "leader_repo": leader_repo,
        },
    )
    return updates


async def main_async(season_number: Optional[int], dry_run: bool) -> None:
    async with AsyncSessionLocal() as session:
        if season_number is None:
            seasons = (
                (
                    await session.execute(
                        text(
                            """
                        SELECT DISTINCT r.season_id
                        FROM rounds r
                        JOIN round_outcomes ro ON ro.round_id = r.round_id
                        ORDER BY r.season_id
                        """
                        )
                    )
                )
                .scalars()
                .all()
            )
        else:
            seasons = (
                (
                    await session.execute(
                        text(
                            """
                        SELECT season_id
                        FROM seasons
                        WHERE season_number = :season_number
                        LIMIT 1
                        """
                        ),
                        {"season_number": season_number},
                    )
                )
                .scalars()
                .all()
            )

        if not seasons:
            print("No seasons with round_outcomes found.")
            return

        total_updates = 0
        for season_id in seasons:
            total_updates += await _repair_season(session, int(season_id), dry_run)

        if dry_run:
            await session.rollback()
            print(f"[dry-run] processed rows: {total_updates}")
        else:
            await session.commit()
            print(f"Repaired rows: {total_updates}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Repair stale season leadership fields in round_outcomes.")
    parser.add_argument("--season", type=int, default=None, help="Season number to repair (default: all seasons with outcomes).")
    parser.add_argument("--dry-run", action="store_true", help="Print changes without writing.")
    args = parser.parse_args()
    asyncio.run(main_async(args.season, args.dry_run))


if __name__ == "__main__":
    main()
