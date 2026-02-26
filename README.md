# Autoppia Leaderboard Backend

FastAPI service that ingests validator rounds from Subnet 36 (Bittensor) and serves UI-ready data for the dashboard. Persistence is PostgreSQL; Redis is used for caching.

## Quickstart (local)
1. Requisitos: Python 3.11+, PostgreSQL (p. ej. `autoppia_dev` en `127.0.0.1:5432`), opcional Redis.
2. Copia `.env` (ya hay uno en el repo) y ajusta si hace falta; por defecto usa `autoppia_user` / `Autoppia2025.Leaderboard` y DB `autoppia_dev`.
3. Instala dependencias:
   ```bash
   python -m venv venv && source venv/bin/activate
   pip install -e .
   ```
4. Levanta Redis (opcional, recomendado):
   ```bash
   docker compose -f docker-compose.yml up -d redis
   ```
5. Arranca el backend:
   ```bash
   uvicorn app.main:app --host 0.0.0.0 --port 8080
   ```
6. Abre la documentación: `http://localhost:8080/docs` o `/redoc`.

## Datos y esquema
- Base de datos: **solo PostgreSQL** (no SQLite).
- Tablas principales: `validator_rounds`, `validator_round_validators`, `validator_round_miners`, `miner_evaluation_runs`, `tasks`, `task_solutions`, `evaluations`, `validator_round_summary_miners`.
- El campo `meta` en `validator_rounds` guarda el payload de soporte de la ronda (local_evaluation, post_consensus_evaluation, ipfs_uploaded/ipfs_downloaded, etc.).
- Nuevo campo dedicado `validator_summary` en `validator_rounds` para guardar un resumen estructurado de la ronda (pre/post consensus, ganador, decisión, etc.).

## Entorno
- `ENVIRONMENT=local|development|production` con sufijos `_LOCAL/_DEVELOPMENT/_PRODUCTION` en las variables (ver `app/config.py`).
- Autenticación de validadores: `AUTH_DISABLED` y `MIN_VALIDATOR_STAKE` controlan la verificación de stake/firma.

## Tests
```bash
pytest -q
```
Las pruebas que consultan red Bittensor se omiten salvo que definas `RUN_LIVE_TESTS=1`.

## Utilidades
- Redis rápido: `docker compose -f docker-compose.yml up -d redis`
- Arranque simple: `bash scripts/bash/start_backend.sh` (si tienes la venv y .env listos)
- Probar Redis: `bash scripts/bash/test_redis.sh`
- Background updater: `bash scripts/bash/start_background_updater.sh`
