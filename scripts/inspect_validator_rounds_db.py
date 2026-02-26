#!/usr/bin/env python3
"""
Inspecciona qué se guarda en la base de datos para validator_rounds (validator_summary, s3_logs).
Uso: desde el directorio del backend, con el venv activo y .env con DATABASE_URL:
  python scripts/inspect_validator_rounds_db.py
"""

import asyncio
import os
import sys

# Add project root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _summary(obj, max_val_len=80):
    if obj is None:
        return "null"
    if not isinstance(obj, dict):
        s = str(obj)
        return s[:max_val_len] + "..." if len(s) > max_val_len else s
    keys = list(obj.keys())
    return "{" + ", ".join(keys) + "}"


async def main():
    from sqlalchemy import select
    from app.db.session import AsyncSessionLocal
    from app.db.models import ValidatorRoundORM

    limit = 5
    async with AsyncSessionLocal() as session:
        stmt = select(ValidatorRoundORM).where(ValidatorRoundORM.status == "finished").order_by(ValidatorRoundORM.ended_at.desc().nulls_last()).limit(limit)
        result = await session.execute(stmt)
        rows = result.scalars().all()

    if not rows:
        print("No hay validator_rounds con status=finished en la BD.")
        return

    print(f"Últimos {len(rows)} validator_rounds (status=finished):\n")
    for r in rows:
        print(f"  validator_round_id: {r.validator_round_id}")
        print(f"  season_number: {r.season_number}  round_number_in_season: {r.round_number_in_season}")
        print(f"  status: {r.status}  ended_at: {r.ended_at}")

        meta = dict(r.validator_summary or {})
        if r.s3_logs:
            meta["s3_logs"] = r.s3_logs
        print(f"  meta keys: {list(meta.keys())}")
        if "s3_logs" in meta:
            s3 = meta["s3_logs"]
            if isinstance(s3, dict) and "round_log" in s3:
                rl = s3["round_log"]
                print(f"    meta.s3_logs.round_log: uploaded={rl.get('uploaded')} url={str(rl.get('url', ''))[:60]}... size_bytes={rl.get('size_bytes')}")
        if "ipfs_uploaded" in meta:
            iu = meta["ipfs_uploaded"]
            print(f"    meta.ipfs_uploaded: cid={iu.get('cid') if isinstance(iu, dict) else 'n/a'}")
        if "ipfs_downloaded" in meta:
            id_ = meta["ipfs_downloaded"]
            print(f"    meta.ipfs_downloaded keys: {list(id_.keys()) if isinstance(id_, dict) else type(id_)}")

        # Columnas sueltas (duplican lo que está en meta)
        print(f"  validator_summary (columna) keys: {list((r.validator_summary or {}).keys())}")
        if r.s3_logs:
            print(f"  s3_logs (columna) keys: {list(r.s3_logs.keys())}")
            if "round_log" in (r.s3_logs or {}):
                rl = r.s3_logs["round_log"]
                print(f"    s3_logs.round_log: uploaded={rl.get('uploaded')} size_bytes={rl.get('size_bytes')}")
        print()
    print("Done.")


if __name__ == "__main__":
    asyncio.run(main())
