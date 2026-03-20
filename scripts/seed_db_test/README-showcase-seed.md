# Showcase Seed Dataset

This repository includes a demo seed that creates a realistic dataset for local development, screenshots, reviews, and open-source onboarding.

## What it creates

- 2 validators
- 3 miners
- 2 seasons
- 3 rounds per season
- 1 active current round (`season 2 / round 3`)
- Tasks, task solutions, evaluation runs, evaluations, execution history, LLM usage, task execution logs
- Round-level consensus/leadership data
- Runtime config rows in `config_app_runtime` and `config_season_round`

## Table coverage

The seed populates the main app tables used by the UI and API:

- `config_app_runtime`
- `config_season_round`
- `seasons`
- `rounds`
- `round_validators`
- `round_validator_miners`
- `round_summary`
- `miner_evaluation_runs`
- `tasks`
- `task_solutions`
- `evaluations`
- `evaluations_execution_history`
- `evaluation_llm_usage`
- `task_execution_logs`

Note:

- `round_outcomes` is a database view backed by `round_summary`, so it is available automatically after seeding.
- `validator_round_summary_miners` is also a view backed by `round_validator_miners`.

## Quick start

Run the full bootstrap from the backend root:

```bash
bash scripts/bash/seed_showcase_db.sh
```

This will:

1. create the schema
2. truncate existing user tables
3. seed the showcase dataset

## Optional smoke test

```bash
SMOKE=1 bash scripts/bash/seed_showcase_db.sh
```

This calls a small set of API endpoints after seeding to confirm the dataset is usable.

## Environment requirements

The script expects the same PostgreSQL environment variables already used by the backend, for example:

- `POSTGRES_USER`
- `POSTGRES_PASSWORD`
- `POSTGRES_HOST`
- `POSTGRES_PORT`
- `POSTGRES_DB`
- or the environment-specific variants such as `POSTGRES_USER_LOCAL`

## Safe usage

By default the wrapper truncates the target database before inserting demo data.

If you want to keep existing data, run the Python seed directly and skip truncation:

```bash
python3 scripts/seed_db_test/seed_showcase_open_repo.py --no-truncate
```

## Files

- Seed script: [seed_showcase_open_repo.py](/home/riiveer/Escritorio/proyectos/autoppia/autoppia_bittensor_dashboard_backend/scripts/seed_db_test/seed_showcase_open_repo.py)
- Wrapper: [seed_showcase_db.sh](/home/riiveer/Escritorio/proyectos/autoppia/autoppia_bittensor_dashboard_backend/scripts/bash/seed_showcase_db.sh)
