# Modelo simplificado: season, rounds, líder y consensus

## 1. Objeto “estado del líder” al final de cada round (lo que guarda el validator)

Esto es **exactamente** lo que quieres persistir por round (por validator y luego unificado por consensus):

```ts
// Tipo: estado de liderazgo al cierre del round
interface SeasonLeaderSnapshot {
  // Líder de la season ANTES de este round (con quien comparamos)
  previous_leader: {
    uid: number;
    hotkey: string;
    avg_reward: number;
  } | null;   // null si la season acaba de empezar (primera round)

  // Mejor candidato de ESTE round (el que podría destronar)
  candidate_to_dethrone: {
    uid: number;
    hotkey: string;
    avg_reward: number;
  } | null;

  // Líder de la season DESPUÉS de este round
  // = previous_leader si no hay dethrone O candidate no supera barrera
  // = candidate_to_dethrone si supera la barrera (required_improvement_pct)
  current_leader_after_round: {
    uid: number;
    hotkey: string;
    avg_reward: number;
  };

  // Metadato de la regla
  required_improvement_pct: number;
  dethroned: boolean;
}
```

En la base de datos **ya tienes** el equivalente pero sin hotkeys en las tablas:

- **validator_rounds**: `reigning_uid_before_round`, `reigning_score_before_round` (previous), `top_candidate_uid`, `top_candidate_score` (candidate), `winner_uid`, `winner_score` (current), `required_improvement_pct`, `dethroned`.
- **round_outcomes**: `reigning_miner_uid_before_round`, `reigning_score_before_round`, `top_candidate_miner_uid`, `top_candidate_score`, `winner_miner_uid`, `winner_score`, etc.

Las **hotkeys** se pueden resolver siempre desde:
- `round_validator_miners` (miner_uid, miner_hotkey) para ese round, o
- `validator_round_miners` / snapshots del validator.

Si quieres “pintar” el objeto tal cual en API/UI, basta un DTO que rellene `uid` + `hotkey` (desde miner_snapshots o round_validator_miners) + `avg_reward` (desde score/reward que ya guardas).

---

## 2. Flujo que describes vs lo que hay en la BD

| Concepto tuyo | En la BD actual |
|---------------|------------------|
| Season con tasks y rounds | `seasons` + `rounds` (round = agrupar por tiempo/weights). Tasks por round: en `validator_rounds` → `tasks` (por validator_round_id). |
| Round: validators generan tasks, handshake, evalúan | Cada validator tiene un `validator_round_id` → `validator_rounds` + `miner_evaluation_runs` + `evaluations` + `task_solutions`. |
| Miner agent run → evaluations (task, task_solution, llm_usage), GIF en S3, logs en S3 | `miner_evaluation_runs`, `evaluations`, `task_solutions`, `evaluation_llm_usage`; logs/GIF por referencia S3. |
| Ranking local al final del round (avg reward, time, cost, score) | En `validator_round_summary_miners` (local_*) y en `round_validator_miners` (local_*). |
| Consensus: subir avg_reward, avg_time, avg_score, avg_cost; ponderado por stake; todos bajan y setean weights | Post-consensus en `validator_round_summary_miners.post_consensus_*` y en `round_validator_miners.post_consensus_*`. |
| Previous leader / candidate / current leader | En `validator_rounds` y en `round_outcomes` (uid + score; hotkey se resuelve de miners). |
| Current leader de la season para la siguiente round | `seasons.leader_miner_uid`, `leader_reward`, `leader_github_url` (falta leader_hotkey si lo quieres en seasons). |
| is_reused: mismo github_url no re-evaluar, competir con best reward | `miner_evaluation_runs.is_reused`, `reused_from_agent_run_id`; en canónico `round_validator_miners.is_reused`, etc. |

Conclusión: el **flujo** que describes ya está cubierto; la complejidad está en **duplicación de conceptos** y en **métricas que no necesitas para “quién es el líder” y “cómo voy en la season”**.

---

## 3. Qué sobra o se puede simplificar

### 3.1 Dos “mundos” de tablas

- **Mundo A (por validator)**: `validator_rounds`, `validator_round_validators`, `validator_round_miners`, `validator_round_summary_miners`, y de ahí cuelgan `miner_evaluation_runs`, `tasks`, `task_solutions`, `evaluations`.
- **Mundo B (canónico season/round)**: `seasons`, `rounds`, `round_validators`, `round_validator_miners`, `round_outcomes`.

Para “saber en todo momento quién es el líder de la season” y “cómo voy en la season” (best reward), la **fuente de verdad** debería ser **una**: el modelo canónico (seasons + rounds + round_outcomes + round_validator_miners). El mundo “validator_rounds” es ejecución detallada por validator; el canónico es lo que todos se bajan y con lo que setean weights. Simplificación: **clarificar que “líder de la season” y “ranking en la season” se leen de seasons + round_outcomes + round_validator_miners**, y que validator_rounds es solo detalle de ejecución (logs, evaluaciones, GIFs, etc.).

### 3.2 Rankings que “a la gente le da igual”

- **local_rank** (ranking virtual “en esta round con este github_url”): lo has dicho tú: no hace falta para la UI de “cómo voy en la season”. Se puede seguir calculando internamente si algún validator lo usa, pero **no hace falta exponerlo ni priorizarlo** en el modelo de “leaderboard de la season”.
- **best_local_rank / best_local_reward / effective_*** en `round_validator_miners`: son derivados (competir con best reward). Si “cómo voy en la season” = “mi best reward en la season”, ese “best” se puede derivar de `post_consensus_avg_reward` por miner a lo largo de rounds (o de un campo explícito “best_reward_in_season” si lo añades). Los campos effective_* duplican lógica; se pueden considerar **redundantes** si ya tienes post_consensus y la regla “compites con tu mejor”.

Propuesta:
- **No eliminar** de golpe `local_rank` ni `effective_*` por si el validator los usa internamente, pero **no basar** la UI de “leaderboard season” en ellos. La UI: “current leader” (seasons + round_outcomes) + “miners en la season” con su **best reward** (derivado de post_consensus por round).

### 3.3 Redundancia winner/reigning en dos sitios

- `validator_rounds`: winner_uid, reigning_uid_before_round, top_candidate_uid, etc.
- `round_outcomes`: winner_miner_uid, reigning_miner_uid_before_round, top_candidate_miner_uid, etc.

Tiene sentido: un validator guarda su decisión local en su `validator_round` y luego el **canon** (round_outcomes) es el que cuenta para la season (p. ej. desde el main validator). Simplificación: **documentar** que la fuente de verdad para “current leader after this round” y para actualizar `seasons.leader_*` es **round_outcomes** (y opcionalmente seasons); lo que está en validator_rounds es reflejo local o input al consensus.

### 3.4 Hotkeys en el objeto líder

- En `round_outcomes` y en `validator_rounds` solo guardas uid + score. Para pintar el objeto con hotkey hay que unir con miners. Opción simple: **añadir en round_outcomes** (y si quieres en validator_rounds) columnas opcionales `winner_hotkey`, `reigning_hotkey_before_round`, `top_candidate_hotkey` para no tener que hacer JOIN cada vez. Si prefieres no duplicar, el DTO de API puede hacer el JOIN con round_validator_miners / miner_snapshots y devolver el objeto `SeasonLeaderSnapshot` completo.

### 3.5 Tabla “seasons”

- Ya tiene `leader_miner_uid`, `leader_reward`, `leader_github_url`. Solo falta **leader_hotkey** si quieres tener el “current leader” completo en una sola fila. Añadir `leader_hotkey` (nullable) simplifica “quién es el líder de la season” a una lectura directa de `seasons`.

---

## 4. Esquema mínimo mental (sin tocar aún la BD)

1. **Season**: tiene N rounds; cada round es “cuando seteas weights”. Tasks de la season: asociadas a rounds (como ahora).
2. **Por round**: cada validator tiene su ejecución (validator_round_id → runs, evaluations, task_solutions, logs/GIF en S3). Al acabar, ranking local por avg_reward; guardas previous_leader, candidate_to_dethrone, current_leader_after_round (objeto con uid, hotkey, avg_reward).
3. **Consensus**: cada validator sube por miner avg_reward, avg_time, avg_score, avg_cost; se agrega ponderado por stake; todos bajan el mismo post_consensus y actualizan su estado; el **current leader de la season** es el que salga de ese consensus (y se persiste en seasons + round_outcomes).
4. **is_reused**: mismo github_url en la season → no re-evaluar; ese miner compite con su best reward (la que ya tenías). Best reward = máximo post_consensus_avg_reward que haya tenido ese miner en la season (o un campo “best_reward_in_season” si lo materializas).
5. **UI**: “Quién es el líder ahora” → seasons.leader_* (y opcionalmente último round_outcomes). “Cómo voy en la season” → por miner, best reward en la season (y posición respecto al líder). No hace falta destacar “local rank en la round” ni “ranking virtual por github en la round”.

---

## 5. Resumen de cambios sugeridos (prioridad)

| Acción | Motivo |
|--------|--------|
| Definir un único DTO `SeasonLeaderSnapshot` (previous_leader, candidate_to_dethrone, current_leader_after_round con uid + hotkey + avg_reward) y usarlo en API/UI | Para no duplicar lógica y tener un solo “objeto” que pintar. |
| Considerar añadir `leader_hotkey` en `seasons` y, opcional, hotkeys en `round_outcomes` | Evitar JOINs solo para mostrar el líder. |
| Tratar `round_outcomes` + `seasons` como fuente de verdad del “líder de la season” y del “current leader after round” | Menos ambigüedad; validator_rounds = detalle de ejecución. |
| No exponer ni basar la leaderboard en `local_rank` ni en “ranking virtual por round” | Alineado con “a la gente le da igual”. |
| Revisar si `effective_*` en round_validator_miners se puede derivar siempre de post_consensus + “best in season”; si sí, no añadir más lógica encima | Simplificar modelo conceptual. |
| Documentar en código que “best reward in season” = máximo de post_consensus_avg_reward del miner en esa season (o campo explícito si lo añadís) | Para is_reused y “cómo voy en la season”. |

Con esto el modelo queda alineado con tu relato: season → rounds → consensus → current leader; is_reused y best reward; y una sola cosa que “pintar” para el líder (el objeto de la sección 1).
