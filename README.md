# Autoppia IWAP Backend

Backend FastAPI para IWAP y el dashboard de Autoppia en Subnet 36. Sirve dos familias de API muy distintas:

- `UI API`: endpoints de lectura para frontend, leaderboard, rounds, tasks, evaluations y analytics.
- `Validator API`: endpoints autenticados que usan los validators para subir rounds, tareas, agent runs, evaluaciones y logs.

## Documentacion

Con el backend levantado:

- Swagger UI: `http://localhost:8080/docs`
- ReDoc: `http://localhost:8080/redoc`
- OpenAPI JSON: `http://localhost:8080/openapi.json`

Swagger queda organizado por tags:

- `UI - ...`
- `Validator - ...`
- `External - ...`
- `System`

## Quickstart

1. Requisitos: Python 3.11+, PostgreSQL y opcionalmente Redis.
2. Ajusta `.env` segun tu entorno.
3. Instala dependencias:

```bash
python3 -m venv venv
source venv/bin/activate
pip install -e .
```

4. Levanta Redis si quieres cache local:

```bash
docker compose -f docker-compose.yml up -d redis
```

5. Arranca el backend:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8080
```

## Mapa rapido

| Familia | Prefijo principal | Auth | Uso |
| --- | --- | --- | --- |
| UI | `/api/v1/...` | No | Lectura para dashboard y vistas internas |
| Validator | `/api/v1/validator-rounds`, `/api/v1/task-logs` | Si | Ingestion del flujo IWAP |
| External | `/api/v1/tasks/with-solutions` | No | Export externo |
| System | `/health`, `/debug/...`, `/admin/...` | No | Salud, debug y operacion |

## UI Endpoints

Todos los endpoints de esta seccion son de lectura salvo comparadores y la subida de GIF en evaluations.

### UI - Overview

| Metodo | Endpoint | Descripcion |
| --- | --- | --- |
| `GET` | `/api/v1/overview/metrics` | KPIs principales del dashboard |
| `GET` | `/api/v1/overview/validators` | Lista paginada de validators |
| `GET` | `/api/v1/overview/validators/filter` | Datos para filtros de validators |
| `GET` | `/api/v1/overview/validators/{validator_id}` | Detalle de un validator |
| `GET` | `/api/v1/overview/rounds/current` | Round actual |
| `GET` | `/api/v1/overview/rounds` | Lista de rounds para overview |
| `GET` | `/api/v1/overview/rounds/{validator_round_id}` | Detalle resumido de round |
| `GET` | `/api/v1/overview/leaderboard` | Leaderboard principal |
| `GET` | `/api/v1/overview/statistics` | Estadisticas agregadas |
| `GET` | `/api/v1/overview/network-status` | Estado de red y disponibilidad |
| `GET` | `/api/v1/overview/recent-activity` | Actividad reciente |
| `GET` | `/api/v1/overview/performance-trends` | Tendencias temporales |

### UI - Rounds

| Metodo | Endpoint | Descripcion |
| --- | --- | --- |
| `GET` | `/api/v1/rounds/ids` | Lista de IDs de rounds |
| `GET` | `/api/v1/rounds/seasons` | Temporadas disponibles |
| `GET` | `/api/v1/rounds/` | Lista paginada de rounds |
| `GET` | `/api/v1/rounds/current` | Round actual con payload detallado |
| `GET` | `/api/v1/rounds/{season}/{round}` | Round por temporada y numero |
| `GET` | `/api/v1/rounds/{season}/{round}/progress` | Progreso del round por temporada/numero |
| `GET` | `/api/v1/rounds/{season}/{round}/status` | Estado resumido del round |
| `GET` | `/api/v1/rounds/{season}/{round}/season-summary` | Resumen de temporada relativo al round |
| `GET` | `/api/v1/rounds/{season}/{round}/validators` | Validators del round por temporada/numero |
| `GET` | `/api/v1/rounds/{round_id}` | Detalle de round por ID |
| `GET` | `/api/v1/rounds/{round_id}/basic` | Payload reducido del round |
| `GET` | `/api/v1/rounds/{round_id}/statistics` | Estadisticas del round |
| `GET` | `/api/v1/rounds/{round_id}/miners` | Miners del round |
| `GET` | `/api/v1/rounds/{round_id}/miners/top` | Top miners del round |
| `GET` | `/api/v1/rounds/{round_id}/miners/{uid}` | Detalle de un miner en el round |
| `GET` | `/api/v1/rounds/{round_id}/validators` | Validators del round |
| `GET` | `/api/v1/rounds/{round_id}/validators/{validator_id}` | Detalle de validator dentro del round |
| `GET` | `/api/v1/rounds/{round_id}/activity` | Actividad del round |
| `GET` | `/api/v1/rounds/{round_id}/progress` | Progreso del round por ID |
| `GET` | `/api/v1/rounds/{round_id}/timeline` | Timeline del round |
| `GET` | `/api/v1/rounds/{round_id}/summary` | Resumen ejecutivo del round |
| `POST` | `/api/v1/rounds/compare` | Comparativa entre rounds |

Nota: existen aliases internos `by-id` para validators que estan ocultos en Swagger y no deberian usarse en integraciones nuevas.

### UI - Agents

| Metodo | Endpoint | Descripcion |
| --- | --- | --- |
| `GET` | `/api/v1/agents` | Catalogo de agentes |
| `GET` | `/api/v1/agents/latest-round-top-miner` | Round/miner de referencia para redirect inicial |
| `GET` | `/api/v1/agents/rounds` | Rounds disponibles y miners asociados |
| `GET` | `/api/v1/agents/seasons/{season_ref}/rank` | Ranking de una season o `latest` |
| `GET` | `/api/v1/agents/round-details` | Detalle de un miner en un round concreto |
| `GET` | `/api/v1/agents/{miner_uid}/historical` | Historico de un miner |
| `GET` | `/api/v1/agents/{agent_id}` | Detalle principal del agente |
| `GET` | `/api/v1/agents/{agent_id}/performance` | Serie de performance del agente |
| `GET` | `/api/v1/agents/{agent_id}/runs-by-round` | Runs agrupadas por round |
| `GET` | `/api/v1/agents/{agent_id}/runs` | Lista paginada de runs de un agente |
| `GET` | `/api/v1/agents/{agent_id}/activity` | Feed de actividad del agente |

### UI - Agent Runs

| Metodo | Endpoint | Descripcion |
| --- | --- | --- |
| `GET` | `/api/v1/agent-runs` | Listado paginado de agent runs |
| `GET` | `/api/v1/agent-runs/{run_id}` | Detalle principal de una run |
| `GET` | `/api/v1/agent-runs/{run_id}/get-agent-run` | Payload completo en una sola llamada |
| `GET` | `/api/v1/agent-runs/{run_id}/personas` | Personas |
| `GET` | `/api/v1/agent-runs/{run_id}/stats` | Estadisticas |
| `GET` | `/api/v1/agent-runs/{run_id}/summary` | Resumen |
| `GET` | `/api/v1/agent-runs/{run_id}/tasks` | Tareas de la run |
| `GET` | `/api/v1/agent-runs/{run_id}/timeline` | Timeline |
| `GET` | `/api/v1/agent-runs/{run_id}/logs` | Logs |
| `GET` | `/api/v1/agent-runs/{run_id}/metrics` | Metricas |
| `POST` | `/api/v1/agent-runs/compare` | Comparativa entre runs |

### UI - Tasks

| Metodo | Endpoint | Descripcion |
| --- | --- | --- |
| `GET` | `/api/v1/tasks` | Catalogo de tasks con filtros |
| `GET` | `/api/v1/tasks/search` | Variante de busqueda de tasks |
| `GET` | `/api/v1/tasks/analytics` | Analytics agregados de tasks |
| `GET` | `/api/v1/tasks/{task_id}` | Detalle principal de task |
| `GET` | `/api/v1/tasks/{task_id}/details` | Detalle extendido |
| `GET` | `/api/v1/tasks/{task_id}/personas` | Personas |
| `GET` | `/api/v1/tasks/{task_id}/statistics` | Estadisticas |
| `GET` | `/api/v1/tasks/{task_id}/actions` | Acciones |
| `GET` | `/api/v1/tasks/{task_id}/screenshots` | Screenshots |
| `GET` | `/api/v1/tasks/{task_id}/results` | Resultados |
| `GET` | `/api/v1/tasks/{task_id}/logs` | Logs |
| `GET` | `/api/v1/tasks/{task_id}/timeline` | Timeline |
| `GET` | `/api/v1/tasks/{task_id}/metrics` | Metricas |
| `POST` | `/api/v1/tasks/compare` | Comparativa entre tasks |

### UI - Evaluations

| Metodo | Endpoint | Descripcion |
| --- | --- | --- |
| `GET` | `/api/v1/evaluations` | Catalogo de evaluations |
| `GET` | `/api/v1/evaluations/export` | Export por season |
| `GET` | `/api/v1/evaluations/{evaluation_id}` | Detalle principal |
| `GET` | `/api/v1/evaluations/{evaluation_id}/get-evaluation` | Payload completo en una sola llamada |
| `GET` | `/api/v1/evaluations/{evaluation_id}/task-details` | Evaluation en formato task details |
| `GET` | `/api/v1/evaluations/{evaluation_id}/personas` | Personas |
| `GET` | `/api/v1/evaluations/{evaluation_id}/results` | Resultados |
| `GET` | `/api/v1/evaluations/{evaluation_id}/actions` | Acciones |
| `GET` | `/api/v1/evaluations/{evaluation_id}/screenshots` | Screenshots |
| `GET` | `/api/v1/evaluations/{evaluation_id}/logs` | Logs |
| `GET` | `/api/v1/evaluations/{evaluation_id}/timeline` | Timeline |
| `GET` | `/api/v1/evaluations/{evaluation_id}/metrics` | Metricas |
| `GET` | `/api/v1/evaluations/{evaluation_id}/statistics` | Estadisticas |
| `POST` | `/api/v1/evaluations/{evaluation_id}/gif` | Sube un GIF de la evaluacion |

### UI - Miners, Validators y Subnets

| Metodo | Endpoint | Descripcion |
| --- | --- | --- |
| `GET` | `/api/v1/miners` | Lista de miners |
| `GET` | `/api/v1/miners/{uid}` | Detalle de miner |
| `GET` | `/api/v1/miners/{uid}/performance` | Performance de miner |
| `GET` | `/api/v1/miner-list` | Directorio ligero de miners |
| `GET` | `/api/v1/miner-list/{uid}` | Lookup ligero de miner |
| `GET` | `/api/v1/validators/{uid}/details` | Detalle de validator |
| `GET` | `/api/v1/subnets/{subnet_id}/timeline` | Timeline animado de subnet |

## Validator Endpoints

Estos endpoints escriben en base de datos y almacenamiento. Requieren firma valida de validator.

### Auth requerida

Headers obligatorios:

```http
x-validator-hotkey: <ss58-hotkey>
x-validator-signature: <base64-signature>
```

### Flujo recomendado de ingestion

| Paso | Metodo | Endpoint | Descripcion |
| --- | --- | --- | --- |
| 1 | `POST` | `/api/v1/validator-rounds/auth-check` | Comprueba que la auth del validator es valida |
| 2 | `POST` | `/api/v1/validator-rounds/runtime-config` | Sincroniza config runtime de season/round |
| 3 | `POST` | `/api/v1/validator-rounds/start` | Abre un validator round |
| 4 | `POST` | `/api/v1/validator-rounds/{validator_round_id}/tasks` | Registra las tasks del round |
| 5 | `POST` | `/api/v1/validator-rounds/{validator_round_id}/agent-runs` | Registra una agent run |
| 6 | `POST` | `/api/v1/validator-rounds/{validator_round_id}/agent-runs/{agent_run_id}/evaluations` | Sube una evaluacion |
| 7 | `POST` | `/api/v1/validator-rounds/{validator_round_id}/agent-runs/{agent_run_id}/evaluations/batch` | Sube evaluaciones en batch |
| 8 | `POST` | `/api/v1/validator-rounds/{validator_round_id}/round-log` | Sube el log global del round |
| 9 | `POST` | `/api/v1/task-logs` | Sube logs por task |
| 10 | `POST` | `/api/v1/validator-rounds/{validator_round_id}/finish` | Cierra el round y persiste metricas finales |

### Aliases legacy soportados

| Metodo | Endpoint | Estado |
| --- | --- | --- |
| `POST` | `/api/v1/validator-rounds/{validator_round_id}/agent-runs/start` | Alias de `.../agent-runs` |
| `POST` | `/api/v1/validator-rounds/{validator_round_id}/evaluations` | Alias de evaluacion unitaria |
| `POST` | `/api/v1/validator-rounds/{validator_round_id}/evaluations/batch` | Alias de batch evaluations |
| `POST` | `/api/v1/validator-rounds/logs/upload` | Alias de round log |

## External Endpoint

| Metodo | Endpoint | Auth | Descripcion |
| --- | --- | --- | --- |
| `GET` | `/api/v1/tasks/with-solutions` | No | Export publico de tasks con solutions y filtros |

## System Endpoints

| Metodo | Endpoint | Descripcion |
| --- | --- | --- |
| `GET` | `/health` | Health check |
| `GET` | `/debug/idempotency-stats` | Estado de idempotencia |
| `GET` | `/debug/cache-stats` | Estado del cache Redis |
| `POST` | `/debug/cache-disable` | Endpoint informativo; no desactiva Redis |
| `POST` | `/debug/cache-enable` | Endpoint informativo; Redis se usa si esta disponible |
| `POST` | `/debug/cache-clear` | Limpia el cache Redis |
| `POST` | `/admin/warm/agents` | Endpoint deprecado |
| `GET` | `/debug/aggregates-meta` | Metadata de agregados |
| `GET` | `/debug/background-updater-status` | Estado del updater externo |
| `GET` | `/debug/metagraph-status` | Alias de estado de metagraph |
| `POST` | `/debug/metagraph-force-refresh` | Fuerza refresh del metagraph |

## Datos y entorno

- Base de datos: PostgreSQL.
- Cache: Redis opcional pero recomendado.
- Configuracion principal: `app/config.py`.
- Auth de validators: controlada por `AUTH_DISABLED`, `MIN_VALIDATOR_STAKE`, `VALIDATOR_AUTH_MESSAGE` y settings de subtensor.

## Tests

```bash
pytest -q
```

Los tests que dependen de red Bittensor solo corren si defines `RUN_LIVE_TESTS=1`.
