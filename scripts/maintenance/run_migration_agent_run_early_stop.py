#!/usr/bin/env python3
"""
Add agent-run early-stop fields to miner_evaluation_runs.

Usage:
  cd autoppia_bittensor_dashboard_backend
  python scripts/maintenance/run_migration_agent_run_early_stop.py
"""

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
os.chdir(root)
sys.path.insert(0, str(root))


def _load_env_from_pm2_process():
    try:
        out = subprocess.run(
            ["pm2", "jlist"],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=root,
        )
        if out.returncode != 0 or not out.stdout.strip():
            return False
        data = json.loads(out.stdout)
        for app in data or []:
            if not isinstance(app, dict):
                continue
            name = (app.get("name") or "").lower()
            if "api" not in name and "leaderboard" not in name and "iwap" not in name:
                continue
            pid = app.get("pid")
            if not pid:
                continue
            env_path = Path(f"/proc/{pid}/environ")
            if not env_path.exists():
                continue
            raw = env_path.read_bytes()
            for part in raw.split(b"\x00"):
                if not part:
                    continue
                try:
                    k, _, v = part.decode("utf-8", errors="replace").partition("=")
                    if k and v is not None:
                        os.environ.setdefault(k, v)
                except Exception:
                    pass
            return True
    except Exception:
        pass
    return False


got_pm2 = _load_env_from_pm2_process()
env_path = root / ".env"
if env_path.exists():
    from dotenv import load_dotenv

    load_dotenv(env_path)
if not got_pm2 and "ENVIRONMENT" not in os.environ:
    os.environ["ENVIRONMENT"] = "development"

from sqlalchemy import text  # noqa: E402


async def main():
    from app.db.session import engine

    statements = [
        "ALTER TABLE miner_evaluation_runs ADD COLUMN IF NOT EXISTS tasks_attempted INTEGER NULL",
        "ALTER TABLE miner_evaluation_runs ADD COLUMN IF NOT EXISTS early_stop_reason VARCHAR(128) NULL",
        "ALTER TABLE miner_evaluation_runs ADD COLUMN IF NOT EXISTS early_stop_message TEXT NULL",
    ]
    async with engine.begin() as conn:
        for index, sql in enumerate(statements, 1):
            await conn.execute(text(sql))
            print(f"  {index}. OK")
    print("Migration agent_run early_stop applied successfully.")
    await engine.dispose()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as exc:
        print(f"Error: {exc}")
        sys.exit(1)
