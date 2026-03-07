# How IWAP works

This document explains the data model and runtime flow used by IWAP.

## 1. Runtime authority (`config_app_runtime`)

`config_app_runtime` is a singleton table (`id=1`) that defines the official validator authority:

- `main_validator_uid`
- `main_validator_hotkey`
- `updated_at`

It is used to decide:

- who is main validator,
- who can create official season/round state directly,
- how `main` vs `fallback` authority is tagged.

## 2. Why the schema is split this way

The schema separates:

1. Global official state (one truth for UI/consensus timeline).
2. Per-validator participation (local execution details).
3. Per-miner metrics inside each validator/round.

This avoids mixing local validator internals with global official round/season state.

## 3. Core tables and meaning

### `seasons`

Global season lifecycle.

- identity: `season_id`, `season_number`
- lifecycle: `status`, `start_block`, `end_block`, `start_at`, `end_at`
- leadership summary: `leader_miner_uid`, `leader_reward`, `leader_github_url`
- dethrone rule: `required_improvement_pct`

Constraint: only one active season at a time.

### `rounds`

Global official rounds inside a season.

- identity: `round_id`, `season_id`, `round_number_in_season`
- schedule: `planned_start_block`, `planned_end_block`
- execution: `start_block`, `end_block`, `start_epoch`, `end_epoch`, `started_at`, `ended_at`
- state: `status`, `consensus_status`
- authority trace: `opened_by_validator_uid`, `closed_by_validator_uid`, `authority_mode` (`main` / `fallback`)

Constraint: only one active round per season.

### `round_validators`

One row per validator participating in one round.

- validator identity snapshot: `validator_uid`, `validator_hotkey`, `validator_coldkey`, `name`, `image_url`, `version`, `stake`, `vtrust`
- runtime/json snapshots: `config`, `local_summary_json`, `post_consensus_json`, `post_consensus_summary`
- IPFS data: `ipfs_uploaded`, `ipfs_downloaded`
- role marker: `is_main_validator`

### `round_validator_miners`

Per-miner metrics in context of one validator+round.

- miner identity snapshot + github info
- reuse info (`is_reused`, `reused_from_*`)
- local metrics (`local_*`)
- post-consensus metrics (`post_consensus_*`)
- effective metrics (`effective_*`) used for competition logic
- `weight`, `subnet_price`

### `round_outcomes`

Global post-consensus rollup for a round (official result).

- winner and dethrone decision fields
- aggregate metrics (`validators_count`, `miners_evaluated`, `tasks_evaluated`, etc.)
- canonical summary JSON (`summary_json`, `post_consensus_summary`)
- source trace (`source_round_validator_id`)

### Execution tables (task/run/evaluation pipeline)

- `miner_evaluation_runs`: one run per miner per validator round.
- `tasks`: tasks for the validator round.
- `task_solutions`: miner actions/solutions for tasks.
- `evaluations`: validator scoring output per task solution.
- `evaluation_execution_history`, `evaluation_llm_usage`: execution and cost details.

## 4. Lifecycle flow

1. Validator starts a round (`/validator-rounds/start`).
2. Tasks are stored (`/validator-rounds/{id}/tasks`).
3. Miner run starts (`/validator-rounds/{id}/agent-runs/start`).
4. Evaluations are uploaded.
5. Round is finished (`/validator-rounds/{id}/finish`) with local/post-consensus summaries.
6. Round/season official fields are updated from the main/fallback authority decision.

## 5. Main vs fallback behavior

Default:

- Main validator is authoritative.

Fallback:

- If main is unavailable after grace window, backup validator can open/close round.
- Authority is persisted in `rounds.authority_mode` and opener/closer UID fields.

## 6. Consistency guarantees

IWAP enforces:

- max 1 active season globally,
- max 1 active round per season,
- explicit authority tracking on each round,
- per-validator and per-miner details kept separately from global official outcome.

## 7. Quick checks

```sql
-- main authority config
SELECT * FROM config_app_runtime WHERE id = 1;

-- active season(s)
SELECT season_id, season_number, status
FROM seasons
WHERE lower(status) = 'active';

-- active round(s)
SELECT s.season_number, r.round_number_in_season, r.status, r.consensus_status, r.authority_mode
FROM rounds r
JOIN seasons s ON s.season_id = r.season_id
WHERE lower(r.status) = 'active';
```
