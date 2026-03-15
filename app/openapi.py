from __future__ import annotations

import inspect
import re

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi
from fastapi.routing import APIRoute

UI_OVERVIEW_TAG = "UI - Overview"
UI_AGENTS_TAG = "UI - Agents"
UI_AGENT_RUNS_TAG = "UI - Agent Runs"
UI_ROUNDS_TAG = "UI - Rounds"
UI_TASKS_TAG = "UI - Tasks"
UI_EVALUATIONS_TAG = "UI - Evaluations"
UI_MINERS_TAG = "UI - Miners"
UI_MINER_LIST_TAG = "UI - Miner List"
UI_VALIDATORS_TAG = "UI - Validators"
UI_SUBNETS_TAG = "UI - Subnets"
VALIDATOR_ROUNDS_TAG = "Validator - Round Ingestion"
VALIDATOR_TASK_LOGS_TAG = "Validator - Task Logs"
EXTERNAL_TASKS_TAG = "External - Tasks"
SYSTEM_TAG = "System"

TAG_NAME_MAP = {
    "overview": UI_OVERVIEW_TAG,
    "agents": UI_AGENTS_TAG,
    "agent-runs": UI_AGENT_RUNS_TAG,
    "rounds": UI_ROUNDS_TAG,
    "tasks": UI_TASKS_TAG,
    "evaluations": UI_EVALUATIONS_TAG,
    "miners": UI_MINERS_TAG,
    "miner-list": UI_MINER_LIST_TAG,
    "validators": UI_VALIDATORS_TAG,
    "subnets": UI_SUBNETS_TAG,
    "validator-rounds": VALIDATOR_ROUNDS_TAG,
    "task-logs": VALIDATOR_TASK_LOGS_TAG,
    "external-tasks": EXTERNAL_TASKS_TAG,
}

OPENAPI_TAGS = [
    {
        "name": UI_OVERVIEW_TAG,
        "description": "KPIs, leaderboard, rounds overview and validator summaries used by the main dashboard landing views.",
    },
    {
        "name": UI_AGENTS_TAG,
        "description": "Agent catalog, season ranking, historical performance and agent detail views.",
    },
    {
        "name": UI_AGENT_RUNS_TAG,
        "description": "Agent run listings and detail endpoints for personas, tasks, logs, metrics and timeline views.",
    },
    {
        "name": UI_ROUNDS_TAG,
        "description": "Round listings, round detail, per-round miners/validators, progress, activity and comparisons.",
    },
    {
        "name": UI_TASKS_TAG,
        "description": "Task catalog and task detail endpoints used by the UI, including analytics and comparisons.",
    },
    {
        "name": UI_EVALUATIONS_TAG,
        "description": "Evaluation catalog plus UI-friendly evaluation detail endpoints and GIF uploads.",
    },
    {
        "name": UI_MINERS_TAG,
        "description": "Miner list and miner detail endpoints used in secondary UI views.",
    },
    {
        "name": UI_MINER_LIST_TAG,
        "description": "Compact miner directory endpoints for lightweight selectors and lookup screens.",
    },
    {
        "name": UI_VALIDATORS_TAG,
        "description": "Validator detail endpoints used by UI pages outside the main overview module.",
    },
    {
        "name": UI_SUBNETS_TAG,
        "description": "Subnet animation and timeline endpoints for visualizations.",
    },
    {
        "name": VALIDATOR_ROUNDS_TAG,
        "description": (
            "Authenticated ingestion API used by validators during an IWAP round lifecycle: auth check, runtime config sync, round start, tasks, agent runs, evaluations, logs and finish."
        ),
    },
    {
        "name": VALIDATOR_TASK_LOGS_TAG,
        "description": "Authenticated upload endpoint for per-task execution logs stored in S3 and indexed in PostgreSQL.",
    },
    {
        "name": EXTERNAL_TASKS_TAG,
        "description": "External read-only export endpoint for tasks with solutions. This endpoint is publicly accessible.",
    },
    {
        "name": SYSTEM_TAG,
        "description": "Health, debug and maintenance endpoints for operators.",
    },
]

OPENAPI_DESCRIPTION = """
Autoppia IWAP backend for Subnet 36.

## API families

- **UI API**: read-only endpoints consumed by the dashboard and other internal frontends.
- **Validator API**: authenticated ingestion endpoints called by validators while a round is running.
- **External API**: controlled export endpoint for tasks with solutions.
- **System API**: health and operator/debug helpers.

## Validator authentication

Validator endpoints require both headers:

- `x-validator-hotkey`
- `x-validator-signature`

The signature must be a base64-encoded signature of the configured `VALIDATOR_AUTH_MESSAGE`.
Swagger keeps these credentials between requests when `Authorize` is used.

## Recommended validator flow

1. `POST /api/v1/validator-rounds/auth-check`
2. `POST /api/v1/validator-rounds/runtime-config`
3. `POST /api/v1/validator-rounds/start`
4. `POST /api/v1/validator-rounds/{validator_round_id}/tasks`
5. `POST /api/v1/validator-rounds/{validator_round_id}/agent-runs`
6. `POST /api/v1/validator-rounds/{validator_round_id}/agent-runs/{agent_run_id}/evaluations`
7. Optional log uploads:
   - `POST /api/v1/validator-rounds/{validator_round_id}/round-log`
   - `POST /api/v1/task-logs`
8. `POST /api/v1/validator-rounds/{validator_round_id}/finish`

## Documentation

- Swagger UI: `/docs`
- ReDoc: `/redoc`
- Raw schema: `/openapi.json`
""".strip()

TAG_GROUPS = [
    {
        "name": "UI",
        "tags": [
            UI_OVERVIEW_TAG,
            UI_AGENTS_TAG,
            UI_AGENT_RUNS_TAG,
            UI_ROUNDS_TAG,
            UI_TASKS_TAG,
            UI_EVALUATIONS_TAG,
            UI_MINERS_TAG,
            UI_MINER_LIST_TAG,
            UI_VALIDATORS_TAG,
            UI_SUBNETS_TAG,
        ],
    },
    {
        "name": "Validator",
        "tags": [
            VALIDATOR_ROUNDS_TAG,
            VALIDATOR_TASK_LOGS_TAG,
        ],
    },
    {
        "name": "Other",
        "tags": [
            EXTERNAL_TASKS_TAG,
            SYSTEM_TAG,
        ],
    },
]

ROUTE_OVERRIDES = {
    ("GET", "/health"): {
        "summary": "Health Check",
        "description": "Basic liveness endpoint for load balancers, monitors and local smoke tests.",
        "tags": [SYSTEM_TAG],
    },
    ("GET", "/debug/idempotency-stats"): {
        "summary": "Get Idempotency Cache Stats",
        "tags": [SYSTEM_TAG],
    },
    ("GET", "/debug/cache-stats"): {
        "summary": "Get Redis Cache Stats",
        "tags": [SYSTEM_TAG],
    },
    ("POST", "/debug/cache-disable"): {
        "summary": "Explain Cache Disable Status",
        "tags": [SYSTEM_TAG],
    },
    ("POST", "/debug/cache-enable"): {
        "summary": "Explain Cache Enable Status",
        "tags": [SYSTEM_TAG],
    },
    ("POST", "/debug/cache-clear"): {
        "summary": "Clear Redis Cache",
        "tags": [SYSTEM_TAG],
    },
    ("POST", "/admin/warm/agents"): {
        "summary": "Deprecated Agent Warmup Endpoint",
        "tags": [SYSTEM_TAG],
    },
    ("GET", "/debug/aggregates-meta"): {
        "summary": "Get Aggregates Metadata",
        "tags": [SYSTEM_TAG],
    },
    ("GET", "/debug/background-updater-status"): {
        "summary": "Get Background Updater Status",
        "tags": [SYSTEM_TAG],
    },
    ("GET", "/debug/metagraph-status"): {
        "summary": "Get Metagraph Status",
        "tags": [SYSTEM_TAG],
    },
    ("POST", "/debug/metagraph-force-refresh"): {
        "summary": "Force Metagraph Refresh",
        "tags": [SYSTEM_TAG],
    },
    ("POST", "/api/v1/validator-rounds/auth-check"): {
        "summary": "Validate Validator Auth Headers",
        "description": "Quick authenticated probe used by validators before starting a round lifecycle.",
    },
    ("POST", "/api/v1/validator-rounds/runtime-config"): {
        "summary": "Sync Runtime Round Config",
        "description": "Persists runtime round and season configuration. Only the configured main validator can update the canonical values.",
    },
    ("POST", "/api/v1/validator-rounds/start"): {
        "summary": "Start Validator Round",
        "description": "Registers the validator round and its validator snapshot, enforcing round-window constraints unless testing overrides are enabled.",
    },
    ("POST", "/api/v1/validator-rounds/{validator_round_id}/tasks"): {
        "summary": "Register Round Tasks",
        "description": "Creates or replaces the task set for a validator round after validating ownership and canonical task count constraints.",
    },
    ("POST", "/api/v1/validator-rounds/{validator_round_id}/agent-runs"): {
        "summary": "Start Agent Run",
        "description": "Registers an agent run for a miner inside an existing validator round.",
    },
    ("POST", "/api/v1/validator-rounds/{validator_round_id}/agent-runs/start"): {
        "summary": "Start Agent Run (Legacy Alias)",
        "description": "Legacy alias for starting an agent run. Prefer `/agent-runs` for new integrations.",
    },
    ("POST", "/api/v1/validator-rounds/{validator_round_id}/agent-runs/{agent_run_id}/evaluations/batch"): {
        "summary": "Upload Batch Evaluations",
        "description": "Stores multiple task, solution and evaluation bundles in one request for an existing agent run.",
    },
    ("POST", "/api/v1/validator-rounds/{validator_round_id}/evaluations/batch"): {
        "summary": "Upload Batch Evaluations (Legacy Alias)",
        "description": "Legacy alias for batch evaluation ingestion. Prefer the agent-run scoped path for new integrations.",
    },
    ("POST", "/api/v1/validator-rounds/{validator_round_id}/agent-runs/{agent_run_id}/evaluations"): {
        "summary": "Upload Evaluation",
        "description": "Stores a single task, solution and evaluation bundle for an existing agent run.",
    },
    ("POST", "/api/v1/validator-rounds/{validator_round_id}/evaluations"): {
        "summary": "Upload Evaluation (Legacy Alias)",
        "description": "Legacy alias for single evaluation ingestion. Prefer the agent-run scoped path for new integrations.",
    },
    ("POST", "/api/v1/validator-rounds/{validator_round_id}/finish"): {
        "summary": "Finish Validator Round",
        "description": "Closes the validator round, persists final metrics and stores consensus-related metadata.",
    },
    ("POST", "/api/v1/validator-rounds/{validator_round_id}/round-log"): {
        "summary": "Upload Round Log",
        "description": "Uploads the validator round log to S3 and persists the resulting public URL on the round.",
    },
    ("POST", "/api/v1/validator-rounds/logs/upload"): {
        "summary": "Upload Round Log (Legacy Alias)",
        "description": "Legacy alias for validator round log upload. Prefer `/{validator_round_id}/round-log` for new integrations.",
    },
    ("POST", "/api/v1/task-logs"): {
        "summary": "Upload Task Execution Log",
        "description": "Uploads a per-task execution log to S3 and stores its metadata in PostgreSQL.",
    },
    ("GET", "/api/v1/tasks/with-solutions"): {
        "summary": "Export Tasks With Solutions",
        "description": "Read-only export endpoint with pagination and filters.",
    },
}

VALIDATOR_SECURED_PREFIXES = (
    "/api/v1/validator-rounds",
    "/api/v1/task-logs",
)


def _primary_method(route: APIRoute) -> str | None:
    methods = sorted(method for method in route.methods or set() if method not in {"HEAD", "OPTIONS"})
    return methods[0] if methods else None


def _humanize_name(raw_name: str) -> str:
    name = raw_name.strip().removesuffix("_endpoint")
    name = name.replace("_", " ")
    name = re.sub(r"\s+", " ", name).strip()
    return " ".join(part.capitalize() for part in name.split()) if name else "Endpoint"


def _build_operation_id(method: str, path: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", path).strip("_").lower()
    return f"{method.lower()}_{slug}"


def apply_route_docs(app: FastAPI) -> None:
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue

        method = _primary_method(route)
        if method is None:
            continue

        if route.tags:
            route.tags = [TAG_NAME_MAP.get(tag, tag) for tag in route.tags]

        override = ROUTE_OVERRIDES.get((method, route.path), {})

        if "tags" in override:
            route.tags = override["tags"]

        if "summary" in override:
            route.summary = override["summary"]
        elif not route.summary:
            route.summary = _humanize_name(route.name or route.endpoint.__name__)

        if "description" in override:
            route.description = inspect.cleandoc(override["description"])
        elif not route.description:
            route_doc = inspect.getdoc(route.endpoint)
            if route_doc:
                route.description = route_doc

        route.operation_id = _build_operation_id(method, route.path)

        responses = dict(route.responses or {})
        if route.path.startswith(VALIDATOR_SECURED_PREFIXES):
            responses.setdefault(401, {"description": "Missing or invalid validator authentication headers."})
            responses.setdefault(403, {"description": "Validator stake is below the configured threshold or hotkey is not present in the metagraph."})
            responses.setdefault(503, {"description": "Validator authentication, chain state or runtime configuration is temporarily unavailable."})
        route.responses = responses


def install_custom_openapi(app: FastAPI) -> None:
    def custom_openapi():
        if app.openapi_schema:
            return app.openapi_schema

        openapi_schema = get_openapi(
            title=app.title,
            version=app.version,
            description=OPENAPI_DESCRIPTION,
            routes=app.routes,
            tags=OPENAPI_TAGS,
            contact={
                "name": "Autoppia",
            },
        )

        components = openapi_schema.setdefault("components", {})
        security_schemes = components.setdefault("securitySchemes", {})
        security_schemes.update(
            {
                "ValidatorHotkeyHeader": {
                    "type": "apiKey",
                    "in": "header",
                    "name": "x-validator-hotkey",
                    "description": "Validator hotkey used as the authenticated identity.",
                },
                "ValidatorSignatureHeader": {
                    "type": "apiKey",
                    "in": "header",
                    "name": "x-validator-signature",
                    "description": "Base64 signature of the configured validator auth message.",
                },
            }
        )

        openapi_schema["x-tagGroups"] = TAG_GROUPS

        for path, path_item in openapi_schema.get("paths", {}).items():
            for method, operation in path_item.items():
                if method not in {"get", "post", "put", "patch", "delete"}:
                    continue

                if path.startswith(VALIDATOR_SECURED_PREFIXES):
                    operation["security"] = [
                        {
                            "ValidatorHotkeyHeader": [],
                            "ValidatorSignatureHeader": [],
                        }
                    ]

        app.openapi_schema = openapi_schema
        return app.openapi_schema

    app.openapi = custom_openapi
