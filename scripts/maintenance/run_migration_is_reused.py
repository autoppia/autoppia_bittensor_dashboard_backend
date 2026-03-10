#!/usr/bin/env python3
"""
Crea las columnas is_reused y reused_from_agent_run_id en miner_evaluation_runs.

Uso: desde el directorio del backend, con el venv activo y .env con DATABASE_URL
     (o POSTGRES_* para tu ENVIRONMENT):
  python scripts/maintenance/run_migration_is_reused.py

  O con el mismo entorno que PM2 (mismo .env y cwd):
  cd /path/to/autoppia_bittensor_dashboard_backend && python scripts/maintenance/run_migration_is_reused.py

Requiere: PostgreSQL levantado y credenciales correctas en .env.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys

# Igual que PM2: cwd = directorio del backend para que load_dotenv() en app.config cargue el mismo .env
from pathlib import Path

root = Path(__file__).resolve().parents[1]
os.chdir(root)
sys.path.insert(0, str(root))


def _load_env_from_pm2_process():
    """Usar el entorno del proceso Python/uvicorn que PM2 tiene levantado (misma DB que el backend)."""
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
                        os.environ.setdefault(k, v)  # no sobrescribir env ya definido (ej. credenciales pasadas por línea de comandos)
                except Exception:  # noqa: BLE001
                    pass
            return True
    except Exception:  # noqa: BLE001
        pass
    return False


# Mismo env que el backend: primero env del proceso PM2 (ENVIRONMENT, etc.), luego .env
got_pm2 = _load_env_from_pm2_process()
env_path = root / ".env"
if env_path.exists():
    from dotenv import load_dotenv

    load_dotenv(env_path)
# Si no hay proceso PM2, asumir development para usar POSTGRES_*_DEVELOPMENT como el API dev
if not got_pm2 and "ENVIRONMENT" not in os.environ:
    os.environ["ENVIRONMENT"] = "development"

from sqlalchemy import text  # noqa: E402


async def main():
    from app.db.session import engine

    statements = [
        "ALTER TABLE miner_evaluation_runs ADD COLUMN IF NOT EXISTS is_reused BOOLEAN NOT NULL DEFAULT FALSE",
        "ALTER TABLE miner_evaluation_runs ADD COLUMN IF NOT EXISTS reused_from_agent_run_id VARCHAR(128) NULL",
        "ALTER TABLE miner_evaluation_runs DROP CONSTRAINT IF EXISTS fk_miner_evaluation_runs_reused_from",
        """ALTER TABLE miner_evaluation_runs ADD CONSTRAINT fk_miner_evaluation_runs_reused_from
           FOREIGN KEY (reused_from_agent_run_id) REFERENCES miner_evaluation_runs(agent_run_id) ON DELETE SET NULL""",
        """CREATE INDEX IF NOT EXISTS idx_miner_evaluation_runs_reused_from
           ON miner_evaluation_runs(reused_from_agent_run_id) WHERE reused_from_agent_run_id IS NOT NULL""",
    ]
    async with engine.begin() as conn:
        for i, sql in enumerate(statements, 1):
            await conn.execute(text(sql))
            print(f"  {i}. OK")
    print("Migración is_reused aplicada correctamente.")
    await engine.dispose()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:  # noqa: BLE001
        err = str(e).strip()
        if "password" in err.lower() or "authentication" in err.lower():
            print("Error: fallo de autenticación con PostgreSQL.")
            print("Comprueba que en .env la contraseña (POSTGRES_PASSWORD_DEVELOPMENT o POSTGRES_PASSWORD_LOCAL)")
            print("coincida con la del usuario de la base de datos.")
            print("")
            print("Alternativa: ejecutar el SQL a mano con psql:")
            print("  PGPASSWORD=<valor desde .env> psql -h 127.0.0.1 -U autoppia_user -d autoppia_dev -f scripts/migrations/add_is_reused_to_miner_evaluation_runs.sql")
        else:
            print(f"Error: {e}")
        sys.exit(1)
