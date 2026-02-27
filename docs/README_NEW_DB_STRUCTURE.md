# New DB Structure (Current Canonical Model)

This document describes the **current database structure** in `public` used for season/round canonical data.

## Source of truth rule

- `round_validators.is_main_validator = true` defines which validator is the canonical source.
- Canonical round outcomes are stored in `round_outcomes` and must reference that source via `source_round_validator_id`.
- `seasons` leadership is updated from canonical `round_outcomes` (`source_round_outcome_id`).

## Tables

### 1) `seasons`
Canonical season registry.

Main fields:
- `season_number`
- `start_block`, `end_block`, `start_at`, `end_at`
- `status`
- `required_improvement_pct`
- `leader_miner_uid`, `leader_reward`, `leader_github_url`
- `source_round_outcome_id`

### 2) `rounds`
Canonical rounds within a season.

Main fields:
- `season_id`, `round_number_in_season`
- `start_block`, `end_block`, `start_epoch`, `end_epoch`
- `started_at`, `ended_at`
- `status`
- `consensus_status` (`pending | failed | finalized`)

### 3) `round_validators`
Validator participation per round.

Main fields:
- validator identity (`validator_uid`, hotkey, version, etc.)
- `is_main_validator`
- `ipfs_uploaded` (JSONB)
- `ipfs_downloaded` (JSONB)
- `config` (JSONB)
- `local_summary_json` (JSONB)
- `post_consensus_json` (JSONB)

### 4) `round_validator_miners`
Per-miner row for each `round_validator`.

Main fields:
- miner identity + reuse flags
- local metrics: `local_*`
- post-consensus metrics: `post_consensus_*`
- effective metrics: `effective_*`
- cost metrics: `local_avg_eval_cost`, `post_consensus_avg_eval_cost`, `effective_eval_cost`

### 5) `round_outcomes`
Canonical round decision/outcome (round-level).

Main fields:
- `source_round_validator_id`
- winner: `winner_miner_uid`, `winner_score`
- dethrone rule:
  - `reigning_miner_uid_before_round`, `reigning_score_before_round`
  - `top_candidate_miner_uid`, `top_candidate_score`
  - `required_improvement_pct`, `dethroned`
- rollups: miners/tasks/avg metrics

### 6) Operational execution tables
- `miner_evaluation_runs`
- `tasks`
- `task_solutions`
- `task_execution_logs`
- `evaluations`
- `evaluations_execution_history`
- `evaluation_llm_usage`

These store execution/evaluation detail and diagnostics. Canonical season/round state should be derived through `round_outcomes` + `round_validator_miners` under the main-validator rule.

## Canonical population flow

1. Write validator-local execution data (`miner_evaluation_runs`, `tasks`, `task_solutions`, `evaluations`, usage/logs).
2. Write validator snapshots in `round_validators` (`local_summary_json`, `post_consensus_json`, IPFS JSONs).
3. Populate `round_validator_miners` (local + post + effective metrics).
4. Compute and upsert `round_outcomes` from the **main validator**.
5. Update `seasons.leader_*` from latest canonical `round_outcomes`.
