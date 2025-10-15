# Autoppia Leaderboard API

A FastAPI backend that ingests validator submissions and serves leaderboard data from an SQL database. The service now relies entirely on SQLAlchemy (SQLite by default) and no longer depends on MongoDB or Motor.

## Features

- **Async FastAPI** with reusable service layer
- **SQLAlchemy ORM** using SQLite out of the box (replaceable via `DATABASE_URL`)
- **Validator ingestion APIs** for rounds, tasks, agent runs, and evaluation results
- **Read APIs** under `/v1/...` that provide agents, miners, tasks, evaluations, overview metrics, miner lists, and subnet timelines
- **API key authentication** and optional idempotency helpers
- **Docker support** and integration tests powered by `pytest`/`httpx`

## Quick Start

### Prerequisites

- Python 3.11+
- Optional: Docker 24+

### Local Development

```bash
# Clone and create virtualenv
cd autoppia_bittensor_dashboard_backend
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt

# Configure environment (uses SQLite by default)
cp config/env.example .env

# (Optional) seed example data
python scripts/seed_database.py --count 3

# Run the API
uvicorn app.main:app --reload --port 8080
```

Then visit:

- API root: http://localhost:8080
- Docs: http://localhost:8080/docs
- Health: http://localhost:8080/health

### Docker Compose

```bash
docker-compose up --build -d
```

The container uses SQLite inside the image; override `DATABASE_URL` for Postgres/MySQL deployments. Run the seed script inside the container if you want fixture data.

## Key Endpoints

- `POST /v1/rounds/submit` – ingest a full round payload (round, agent runs, tasks, solutions, evaluations)
- `POST /v1/validator-rounds/...` – progressive ingestion workflow
- `GET /v1/rounds` – list rounds, details, and associated agent runs
- `GET /v1/agent-runs` – agent run listings plus personas/stats/summary/logs/metrics
- `GET /v1/evaluations` – evaluation list/details
- `GET /v1/tasks` – tasks list/details/results/logs/timeline/metrics
- `GET /v1/agents` and `GET /v1/miners` – aggregated agent/miner views
- `GET /v1/overview/...` – dashboard metrics, validators, rounds, leaderboard, statistics, activity
- `GET /v1/miner-list` – minimal miner roster data for UI tables
- `GET /v1/subnets/{id}/timeline` – subnet timeline animation payload

All read endpoints are protected by the same API-key mechanism as the ingestion routes.

## Configuration

Environment variables are defined in `config/env.example`. Core settings include:

| Variable | Description |
|----------|-------------|
| `APP_NAME` | Application name |
| `DEBUG` | Enable FastAPI debug mode |
| `HOST` / `PORT` | Server bind settings |
| `DATABASE_URL` | SQLAlchemy connection string (defaults to SQLite) |
| `API_KEYS` | JSON list of valid API keys |
| `CORS_ORIGINS` | Allowed CORS origins |
| `IDEMPOTENCY_TTL` | Idempotency cache retention time |

## Testing & Seeding

```bash
pip install -r requirements.txt
python scripts/seed_database.py --count 5  # optional sample data
pytest
```

The integration suite in `tests/test_validator_endpoints.py` ingests synthetic round submissions and exercises every `/v1/...` endpoint, so a plain `pytest` validates ingestion and read flows.

## Project Structure (excerpt)

```
app/
├── api/
│   ├── routes/            # Read-only endpoints for leaderboard views
│   └── validator/         # Ingestion endpoints
├── db/
│   ├── base.py            # Declarative base
│   ├── models.py          # ORM models
│   └── session.py         # Async session/engine helpers
├── services/
│   ├── services/          # Service layer (rounds, agents, tasks, etc.)
│   ├── subnet_timeline.py # Deterministic subnet timeline generator
│   └── validator_storage.py
├── models/
│   ├── core.py            # Pydantic ingestion models
│   └── ui/                # Response schemas for UI consumers
└── tests/                 # Integration tests
```

## Support

For issues or feature requests, open a ticket in the repository or contact the Autoppia team.
