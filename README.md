# Autoppia Leaderboard API

Backend service that powers the Autoppia validator dashboard. It exposes FastAPI endpoints that validators call to register validator rounds, agent runs, evaluations, and aggregate metrics. The service persists data to SQLite (`autoppia.db` locally, `autoppia_prod.db` in production) and provides UI-ready views consumed by the frontend.

## Local Development

```bash
python -m venv venv
source venv/bin/activate
pip install -e .[dev]
uvicorn app.main:app --reload
```

Populate the database by running the validator seeding flow in `seeding/validator_round.py`, which uses the public endpoints to insert data exactly as validators do in production. Refer to `app/config.py` for available environment variables, including validator authentication toggles.
