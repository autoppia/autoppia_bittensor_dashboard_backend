# Cómo testear zero_reason (motivo de score 0)

## 1. Tests automáticos (backend)

Con la base de datos configurada y accesible:

```bash
cd autoppia_bittensor_dashboard_backend
# Test que persiste zero_reason en finish_round
pytest tests/test_agent_run_and_metrics.py::test_finish_round_persists_zero_reason -v

# Tests de validator y agent runs (incluyen finish_round)
pytest tests/test_agent_run_and_metrics.py tests/test_validator_endpoints.py -v --tb=short
```

## 2. Comprobar migración de la BD

Al arrancar la app, `init_db` en `app/db/session.py` ejecuta:

```sql
ALTER TABLE miner_evaluation_runs ADD COLUMN IF NOT EXISTS zero_reason VARCHAR(128) NULL;
```

Si usas migraciones manuales, asegúrate de que esa columna exista.

## 3. Prueba manual rápida

### Backend (API)

1. Arranca el backend y crea un round + agent run (o usa uno existente).
2. Llama a `POST /api/v1/validator-rounds/{round_id}/finish` con cuerpo por ejemplo:

```json
{
  "status": "completed",
  "ended_at": 1700001000.0,
  "agent_runs": [
    {
      "agent_run_id": "<id_del_run>",
      "rank": 1,
      "zero_reason": "over_cost_limit"
    }
  ]
}
```

3. Consulta el run: `GET /api/v1/agent-runs/{run_id}` o el detalle completo `GET /api/v1/agent-runs/{run_id}/get-agent-run`.
   En la respuesta debe aparecer `zeroReason` en `info` (endpoint completo) o en el run.

### Frontend

1. Abre un **agent run** que tenga score 0 y `zero_reason` guardado en BD.
   Deberías ver el banner: *"Reason for zero score: Over Cost Limit"* (o el motivo que sea).
2. En la página de un **round**, en la lista de miners del validator, un miner con reward 0 y `zero_reason` debe mostrar debajo del UID: *"Reason: Over Cost Limit"*.

## 4. Flujo completo (validator → IWAP → dashboard)

- En el **validator** (subnet), cuando un run termina con score 0, se asigna `zero_reason` en `_finalize_agent` (p. ej. `over_cost_limit`, `deploy_failed`, `all_tasks_failed`).
- En **finish_round** el validator envía `agent_runs[].zero_reason` a IWAP.
- El **backend IWAP** guarda ese valor en `miner_evaluation_runs.zero_reason` y lo devuelve en las APIs de agent run y en el round detail (miners).
- El **frontend** muestra el motivo en la página del agent run y en la vista del round para miners con score 0.
