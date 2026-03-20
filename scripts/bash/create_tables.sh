#!/usr/bin/env bash
# create_tables.sh — Create the full database schema from scratch (tables, views, triggers, indexes)
# Run this after truncate_all_tables.sh or on a fresh empty database.

set -euo pipefail

# --- Load .env file automatically if present ---
if [[ -f ".env" ]]; then
  echo "📄 Loading environment variables from .env"
  while IFS='=' read -r key value; do
    [[ -z "${key}" || "${key}" =~ ^[[:space:]]*# ]] && continue
    key="${key%%[[:space:]]*}"
    if [[ -z "${!key:-}" ]]; then
      export "${key}=${value}"
    fi
  done < .env
else
  echo "⚠️  No .env file found in current directory. Make sure env vars are set manually."
fi

# --- Determine environment and set variable names ---
ENVIRONMENT="${ENVIRONMENT:-local}"
ENVIRONMENT=$(echo "$ENVIRONMENT" | tr '[:upper:]' '[:lower:]')
ENV_SUFFIX=$(echo "$ENVIRONMENT" | tr '[:lower:]' '[:upper:]')

echo "🔧 Using environment: $ENVIRONMENT"

# --- Get environment-specific variables with fallback to generic ---
_get_var() {
  local base_name=$1
  local specific_var="${base_name}_${ENV_SUFFIX}"
  if [[ -n "${!specific_var:-}" ]]; then
    echo "${!specific_var}"
    return 0
  fi
  if [[ -n "${!base_name:-}" ]]; then
    echo "${!base_name}"
    return 0
  fi
  return 1
}

# --- Resolve database variables ---
POSTGRES_USER=$(_get_var "POSTGRES_USER") || POSTGRES_USER=""
POSTGRES_PASSWORD=$(_get_var "POSTGRES_PASSWORD") || POSTGRES_PASSWORD=""
POSTGRES_HOST=$(_get_var "POSTGRES_HOST") || POSTGRES_HOST="127.0.0.1"
POSTGRES_PORT=$(_get_var "POSTGRES_PORT") || POSTGRES_PORT="5432"
POSTGRES_DB=$(_get_var "POSTGRES_DB") || POSTGRES_DB=""
MAIN_VALIDATOR_UID=$(_get_var "MAIN_VALIDATOR_UID") || MAIN_VALIDATOR_UID="${MAIN_VALIDATOR_UID:-83}"
MAIN_VALIDATOR_HOTKEY=$(_get_var "MAIN_VALIDATOR_HOTKEY") || MAIN_VALIDATOR_HOTKEY="${MAIN_VALIDATOR_HOTKEY:-}"
MINIMUM_VALIDATOR_VERSION=$(_get_var "MINIMUM_VALIDATOR_VERSION") || MINIMUM_VALIDATOR_VERSION="${MINIMUM_VALIDATOR_VERSION:-}"

# --- Validate required vars ---
: "${POSTGRES_USER:?POSTGRES_USER or POSTGRES_USER_${ENV_SUFFIX} is missing}"
: "${POSTGRES_PASSWORD:?POSTGRES_PASSWORD or POSTGRES_PASSWORD_${ENV_SUFFIX} is missing}"
: "${POSTGRES_HOST:?POSTGRES_HOST or POSTGRES_HOST_${ENV_SUFFIX} is missing}"
: "${POSTGRES_PORT:?POSTGRES_PORT or POSTGRES_PORT_${ENV_SUFFIX} is missing}"
: "${POSTGRES_DB:?POSTGRES_DB or POSTGRES_DB_${ENV_SUFFIX} is missing}"
if [[ ! "${MAIN_VALIDATOR_UID}" =~ ^[0-9]+$ ]]; then
  echo "❌ MAIN_VALIDATOR_UID must be a positive integer. Got: '${MAIN_VALIDATOR_UID}'"
  exit 1
fi

export PGPASSWORD="${POSTGRES_PASSWORD}"

PSQL="psql --host=${POSTGRES_HOST} --port=${POSTGRES_PORT} --username=${POSTGRES_USER} --dbname=${POSTGRES_DB} --set=ON_ERROR_STOP=1"

echo "🏗️  Creating schema in '${POSTGRES_DB}' on ${POSTGRES_HOST}:${POSTGRES_PORT}..."

$PSQL <<'SQL'

-- ============================================================
-- FUNCTIONS
-- ============================================================

CREATE OR REPLACE FUNCTION compat_fill_round_validator_id_tasks()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.round_validator_id IS NULL AND NEW.validator_round_id IS NOT NULL THEN
        SELECT rv.round_validator_id INTO NEW.round_validator_id
        FROM round_validators rv WHERE rv.validator_round_id = NEW.validator_round_id LIMIT 1;
    END IF;
    IF NEW.round_validator_id IS NULL THEN
        RAISE EXCEPTION 'tasks.round_validator_id is required (validator_round_id=%)', NEW.validator_round_id;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION compat_fill_round_validator_id_runs()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.round_validator_id IS NULL AND NEW.validator_round_id IS NOT NULL THEN
        SELECT rv.round_validator_id INTO NEW.round_validator_id
        FROM round_validators rv WHERE rv.validator_round_id = NEW.validator_round_id LIMIT 1;
    END IF;
    IF NEW.round_validator_id IS NULL THEN
        RAISE EXCEPTION 'miner_evaluation_runs.round_validator_id is required (validator_round_id=%)', NEW.validator_round_id;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION normalize_round_boundaries()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.start_block IS NOT NULL AND NEW.end_block IS NOT NULL AND NEW.end_block < NEW.start_block THEN
        NEW.end_block := NEW.start_block;
    END IF;
    IF NEW.started_at IS NOT NULL AND NEW.ended_at IS NOT NULL AND NEW.ended_at < NEW.started_at THEN
        NEW.ended_at := NEW.started_at;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION normalize_round_validator_boundaries()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.start_block IS NOT NULL AND NEW.end_block IS NOT NULL AND NEW.end_block < NEW.start_block THEN
        NEW.end_block := NEW.start_block;
    END IF;
    IF NEW.started_at IS NOT NULL AND NEW.finished_at IS NOT NULL AND NEW.finished_at < NEW.started_at THEN
        NEW.finished_at := NEW.started_at;
    END IF;
    IF NEW.started_at IS NOT NULL AND NEW.started_at < TIMESTAMP WITH TIME ZONE '2001-01-01 00:00:00+00' THEN
        NEW.started_at := NULL;
    END IF;
    IF NEW.finished_at IS NOT NULL AND NEW.finished_at < TIMESTAMP WITH TIME ZONE '2001-01-01 00:00:00+00' THEN
        NEW.finished_at := NULL;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION enforce_config_season_round_main_validator()
RETURNS TRIGGER AS $$
DECLARE main_uid INTEGER;
BEGIN
    SELECT main_validator_uid INTO main_uid FROM config_app_runtime WHERE id = 1;
    IF main_uid IS NULL THEN
        RAISE EXCEPTION 'config_season_round write blocked: config_app_runtime.main_validator_uid is NULL';
    END IF;
    IF NEW.updated_by_validator_uid IS NULL THEN
        RAISE EXCEPTION 'config_season_round write blocked: updated_by_validator_uid is required';
    END IF;
    IF NEW.updated_by_validator_uid <> main_uid THEN
        RAISE EXCEPTION 'config_season_round write blocked: uid % is not main validator uid %',
            NEW.updated_by_validator_uid, main_uid;
    END IF;
    NEW.id := 1;
    NEW.updated_at := NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION compat_validator_round_miners_iou()
RETURNS TRIGGER AS $$
DECLARE rvid BIGINT; rid BIGINT; target_id BIGINT;
BEGIN
    IF TG_OP = 'INSERT' OR TG_OP = 'UPDATE' THEN
        IF NEW.miner_uid IS NULL THEN
            RAISE EXCEPTION 'miner_uid is required for validator_round_miners compatibility write';
        END IF;
        SELECT round_validator_id, round_id INTO rvid, rid
        FROM round_validators WHERE validator_round_id = NEW.validator_round_id LIMIT 1;
        IF rvid IS NULL THEN
            RAISE EXCEPTION 'validator_round_id not found: %', NEW.validator_round_id;
        END IF;
        INSERT INTO round_validator_miners (
            round_validator_id, round_id, miner_uid, miner_hotkey, miner_coldkey,
            name, image_url, github_url, is_sota, version, created_at, updated_at
        ) VALUES (
            rvid, rid, NEW.miner_uid, NEW.miner_hotkey, NEW.miner_coldkey,
            NEW.name, NEW.image_url, NEW.github_url, COALESCE(NEW.is_sota, FALSE), NEW.version, NOW(), NOW()
        )
        ON CONFLICT (round_validator_id, miner_uid) DO UPDATE SET
            miner_hotkey = EXCLUDED.miner_hotkey, miner_coldkey = EXCLUDED.miner_coldkey,
            name = EXCLUDED.name, image_url = EXCLUDED.image_url, github_url = EXCLUDED.github_url,
            is_sota = EXCLUDED.is_sota, version = EXCLUDED.version, updated_at = NOW();
        SELECT id INTO target_id FROM round_validator_miners
        WHERE round_validator_id = rvid AND miner_uid = NEW.miner_uid LIMIT 1;
        NEW.id := target_id;
        RETURN NEW;
    ELSIF TG_OP = 'DELETE' THEN
        DELETE FROM round_validator_miners rvm USING round_validators rv
        WHERE rv.round_validator_id = rvm.round_validator_id
          AND rv.validator_round_id = OLD.validator_round_id AND rvm.miner_uid = OLD.miner_uid;
        RETURN OLD;
    END IF;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION compat_validator_round_summary_miners_iou()
RETURNS TRIGGER AS $$
DECLARE rvid BIGINT; rid BIGINT; target_id BIGINT;
BEGIN
    IF TG_OP = 'INSERT' OR TG_OP = 'UPDATE' THEN
        SELECT round_validator_id, round_id INTO rvid, rid
        FROM round_validators WHERE validator_round_id = NEW.validator_round_id LIMIT 1;
        IF rvid IS NULL THEN
            RAISE EXCEPTION 'validator_round_id not found: %', NEW.validator_round_id;
        END IF;
        INSERT INTO round_validator_miners (
            round_validator_id, round_id, miner_uid, miner_hotkey,
            local_avg_reward, local_avg_eval_score, local_avg_eval_time, local_avg_eval_cost,
            local_tasks_received, local_tasks_success,
            post_consensus_rank, post_consensus_avg_reward, post_consensus_avg_eval_score,
            post_consensus_avg_eval_time, post_consensus_avg_eval_cost,
            post_consensus_tasks_received, post_consensus_tasks_success,
            weight, subnet_price, created_at, updated_at
        ) VALUES (
            rvid, rid, NEW.miner_uid, NEW.miner_hotkey,
            NEW.local_avg_reward, NEW.local_avg_eval_score, NEW.local_avg_eval_time, NEW.local_avg_eval_cost,
            NEW.local_tasks_received, NEW.local_tasks_success,
            NEW.post_consensus_rank, NEW.post_consensus_avg_reward, NEW.post_consensus_avg_eval_score,
            NEW.post_consensus_avg_eval_time, NEW.post_consensus_avg_eval_cost,
            NEW.post_consensus_tasks_received, NEW.post_consensus_tasks_success,
            NEW.weight, NEW.subnet_price, NOW(), NOW()
        )
        ON CONFLICT (round_validator_id, miner_uid) DO UPDATE SET
            miner_hotkey = COALESCE(EXCLUDED.miner_hotkey, round_validator_miners.miner_hotkey),
            local_avg_reward = EXCLUDED.local_avg_reward, local_avg_eval_score = EXCLUDED.local_avg_eval_score,
            local_avg_eval_time = EXCLUDED.local_avg_eval_time, local_avg_eval_cost = EXCLUDED.local_avg_eval_cost,
            local_tasks_received = EXCLUDED.local_tasks_received, local_tasks_success = EXCLUDED.local_tasks_success,
            post_consensus_rank = EXCLUDED.post_consensus_rank,
            post_consensus_avg_reward = EXCLUDED.post_consensus_avg_reward,
            post_consensus_avg_eval_score = EXCLUDED.post_consensus_avg_eval_score,
            post_consensus_avg_eval_time = EXCLUDED.post_consensus_avg_eval_time,
            post_consensus_avg_eval_cost = EXCLUDED.post_consensus_avg_eval_cost,
            post_consensus_tasks_received = EXCLUDED.post_consensus_tasks_received,
            post_consensus_tasks_success = EXCLUDED.post_consensus_tasks_success,
            weight = EXCLUDED.weight, subnet_price = EXCLUDED.subnet_price, updated_at = NOW();
        SELECT id INTO target_id FROM round_validator_miners
        WHERE round_validator_id = rvid AND miner_uid = NEW.miner_uid LIMIT 1;
        NEW.id := target_id;
        RETURN NEW;
    ELSIF TG_OP = 'DELETE' THEN
        DELETE FROM round_validator_miners rvm USING round_validators rv
        WHERE rv.round_validator_id = rvm.round_validator_id
          AND rv.validator_round_id = OLD.validator_round_id AND rvm.miner_uid = OLD.miner_uid;
        RETURN OLD;
    END IF;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION compat_validator_round_validators_iou()
RETURNS TRIGGER AS $$
DECLARE rvid BIGINT; cfg_uid INTEGER; cfg_hotkey VARCHAR(128); is_main BOOLEAN;
BEGIN
    SELECT main_validator_uid, main_validator_hotkey INTO cfg_uid, cfg_hotkey
    FROM config_app_runtime WHERE id = 1;
    is_main := (
        (cfg_uid IS NULL AND (cfg_hotkey IS NULL OR cfg_hotkey = ''))
        OR (cfg_uid IS NOT NULL AND NEW.validator_uid = cfg_uid)
        OR (cfg_hotkey IS NOT NULL AND cfg_hotkey <> '' AND NEW.validator_hotkey = cfg_hotkey)
    );
    IF TG_OP = 'INSERT' OR TG_OP = 'UPDATE' THEN
        SELECT round_validator_id INTO rvid FROM round_validators
        WHERE validator_round_id = NEW.validator_round_id LIMIT 1;
        IF rvid IS NULL THEN
            RAISE EXCEPTION 'validator_round_id not found: %', NEW.validator_round_id;
        END IF;
        UPDATE round_validators SET
            validator_uid = NEW.validator_uid, validator_hotkey = NEW.validator_hotkey,
            validator_coldkey = NEW.validator_coldkey, name = NEW.name,
            stake = NEW.stake, vtrust = NEW.vtrust, image_url = NEW.image_url,
            version = NEW.version, config = NEW.config,
            is_main_validator = COALESCE(is_main, is_main_validator), updated_at = NOW()
        WHERE round_validator_id = rvid;
        UPDATE rounds SET
            opened_by_validator_uid = COALESCE(opened_by_validator_uid, NEW.validator_uid),
            authority_mode = COALESCE(authority_mode,
                CASE WHEN COALESCE(is_main, FALSE) THEN 'main' ELSE 'fallback' END),
            updated_at = NOW()
        WHERE round_id = (SELECT round_id FROM round_validators WHERE round_validator_id = rvid);
        IF COALESCE(is_main, FALSE) THEN
            UPDATE round_validators SET is_main_validator = FALSE, updated_at = NOW()
            WHERE round_id = (SELECT round_id FROM round_validators WHERE round_validator_id = rvid)
              AND round_validator_id <> rvid AND is_main_validator = TRUE;
        END IF;
        NEW.id := rvid;
        RETURN NEW;
    ELSIF TG_OP = 'DELETE' THEN
        DELETE FROM round_validators WHERE validator_round_id = OLD.validator_round_id;
        RETURN OLD;
    END IF;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION compat_validator_rounds_iou()
RETURNS TRIGGER AS $$
DECLARE
    sid BIGINT; rid BIGINT; rvid BIGINT;
    ts TIMESTAMPTZ; te TIMESTAMPTZ;
    cfg_uid INTEGER; cfg_hotkey VARCHAR(128);
    cur_uid INTEGER; cur_hotkey VARCHAR(128);
    is_main BOOLEAN;
BEGIN
    SELECT main_validator_uid, main_validator_hotkey INTO cfg_uid, cfg_hotkey
    FROM config_app_runtime WHERE id = 1;
    is_main := FALSE;

    IF TG_OP = 'INSERT' THEN
        IF NEW.season_number IS NULL OR NEW.round_number_in_season IS NULL THEN
            RAISE EXCEPTION 'season_number and round_number_in_season are required';
        END IF;
        ts := CASE WHEN NEW.started_at IS NULL OR NEW.started_at <= 0 THEN NULL ELSE to_timestamp(NEW.started_at) END;
        te := CASE WHEN NEW.ended_at IS NULL OR NEW.ended_at <= 0 THEN NULL ELSE to_timestamp(NEW.ended_at) END;

        SELECT season_id INTO sid FROM seasons WHERE season_number = NEW.season_number LIMIT 1;
        IF sid IS NULL THEN
            INSERT INTO seasons (season_number, status, start_block, end_block, start_at, end_at,
                required_improvement_pct, created_at, updated_at)
            VALUES (NEW.season_number, 'active', NEW.start_block, NEW.end_block, ts, te,
                COALESCE(NEW.required_improvement_pct, 0.05), NOW(), NOW())
            RETURNING season_id INTO sid;
        END IF;

        SELECT round_id INTO rid FROM rounds
        WHERE season_id = sid AND round_number_in_season = NEW.round_number_in_season LIMIT 1;
        IF rid IS NULL THEN
            INSERT INTO rounds (
                season_id, round_number_in_season, start_block, end_block,
                planned_start_block, planned_end_block, start_epoch, end_epoch,
                opened_by_validator_uid, authority_mode, started_at, ended_at,
                status, consensus_status, created_at, updated_at
            ) VALUES (
                sid, NEW.round_number_in_season, NEW.start_block, NEW.end_block,
                NEW.start_block, NEW.end_block, NEW.start_epoch, NEW.end_epoch,
                NULL, NULL, ts, te,
                COALESCE(NEW.status, 'active'),
                CASE WHEN LOWER(COALESCE(NEW.status, '')) IN ('finished', 'evaluating_finished')
                     THEN 'finalized' ELSE 'pending' END,
                NOW(), NOW()
            ) RETURNING round_id INTO rid;
        END IF;

        SELECT round_validator_id INTO rvid FROM round_validators
        WHERE validator_round_id = NEW.validator_round_id LIMIT 1;
        IF rvid IS NULL THEN
            INSERT INTO round_validators (
                round_id, season_number, round_number_in_season,
                start_block, end_block, start_epoch, end_epoch,
                validator_uid, validator_hotkey, validator_round_id,
                started_at, finished_at, post_consensus_json, s3_logs_url,
                is_main_validator, created_at, updated_at
            ) VALUES (
                rid, NEW.season_number, NEW.round_number_in_season,
                NEW.start_block, NEW.end_block, NEW.start_epoch, NEW.end_epoch,
                0, NULL, NEW.validator_round_id, ts, te,
                CASE
                    WHEN jsonb_typeof(NEW.validator_summary->'summary') = 'object' THEN NEW.validator_summary
                    WHEN jsonb_typeof(NEW.validator_summary->'evaluation_post_consensus'->'summary') = 'object'
                        THEN NEW.validator_summary->'evaluation_post_consensus'
                    ELSE NEW.validator_summary
                END,
                NEW.s3_logs_url, FALSE, NOW(), NOW()
            ) RETURNING round_validator_id INTO rvid;
        END IF;
        NEW.id := rvid;
        RETURN NEW;

    ELSIF TG_OP = 'UPDATE' THEN
        ts := CASE WHEN NEW.started_at IS NULL OR NEW.started_at <= 0 THEN NULL ELSE to_timestamp(NEW.started_at) END;
        te := CASE WHEN NEW.ended_at IS NULL OR NEW.ended_at <= 0 THEN NULL ELSE to_timestamp(NEW.ended_at) END;
        SELECT rv.round_validator_id, rv.round_id INTO rvid, rid
        FROM round_validators rv
        WHERE rv.validator_round_id = COALESCE(NEW.validator_round_id, OLD.validator_round_id) LIMIT 1;
        IF rvid IS NULL THEN
            RAISE EXCEPTION 'validator_round_id not found: %', COALESCE(NEW.validator_round_id, OLD.validator_round_id);
        END IF;
        SELECT validator_uid, validator_hotkey INTO cur_uid, cur_hotkey
        FROM round_validators WHERE round_validator_id = rvid LIMIT 1;
        is_main := (
            (cfg_uid IS NULL AND (cfg_hotkey IS NULL OR cfg_hotkey = ''))
            OR (cfg_uid IS NOT NULL AND cur_uid = cfg_uid)
            OR (cfg_hotkey IS NOT NULL AND cfg_hotkey <> '' AND cur_hotkey = cfg_hotkey)
        );
        UPDATE rounds SET
            start_block = CASE WHEN is_main THEN COALESCE(NEW.start_block, start_block) ELSE COALESCE(start_block, NEW.start_block) END,
            end_block = CASE WHEN is_main THEN COALESCE(NEW.end_block, end_block) ELSE COALESCE(end_block, NEW.end_block) END,
            start_epoch = CASE WHEN is_main THEN COALESCE(NEW.start_epoch, start_epoch) ELSE COALESCE(start_epoch, NEW.start_epoch) END,
            end_epoch = CASE WHEN is_main THEN COALESCE(NEW.end_epoch, end_epoch) ELSE COALESCE(end_epoch, NEW.end_epoch) END,
            started_at = CASE WHEN is_main THEN COALESCE(ts, started_at) ELSE COALESCE(started_at, ts) END,
            ended_at = CASE WHEN is_main THEN COALESCE(te, ended_at) ELSE COALESCE(ended_at, te) END,
            status = CASE WHEN is_main THEN COALESCE(NEW.status, status) ELSE status END,
            consensus_status = CASE
                WHEN is_main AND LOWER(COALESCE(NEW.status, status)) IN ('finished', 'evaluating_finished') THEN 'finalized'
                ELSE consensus_status END,
            updated_at = NOW()
        WHERE round_id = rid;
        UPDATE round_validators SET
            round_id = COALESCE(rid, round_id),
            season_number = COALESCE(NEW.season_number, season_number),
            round_number_in_season = COALESCE(NEW.round_number_in_season, round_number_in_season),
            start_block = COALESCE(NEW.start_block, start_block),
            end_block = COALESCE(NEW.end_block, end_block),
            start_epoch = COALESCE(NEW.start_epoch, start_epoch),
            end_epoch = COALESCE(NEW.end_epoch, end_epoch),
            finished_at = COALESCE(te, finished_at),
            post_consensus_json = COALESCE(
                CASE
                    WHEN jsonb_typeof(NEW.validator_summary->'summary') = 'object' THEN NEW.validator_summary
                    WHEN jsonb_typeof(NEW.validator_summary->'evaluation_post_consensus'->'summary') = 'object'
                        THEN NEW.validator_summary->'evaluation_post_consensus'
                    ELSE NEW.validator_summary
                END, post_consensus_json),
            s3_logs_url = COALESCE(NEW.s3_logs_url, s3_logs_url),
            is_main_validator = COALESCE(is_main, is_main_validator),
            updated_at = NOW()
        WHERE round_validator_id = rvid;
        NEW.id := rvid;
        RETURN NEW;

    ELSIF TG_OP = 'DELETE' THEN
        DELETE FROM round_validators WHERE validator_round_id = OLD.validator_round_id;
        RETURN OLD;
    END IF;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

-- ============================================================
-- TABLES
-- ============================================================

CREATE TABLE IF NOT EXISTS config_app_runtime (
    id SMALLINT DEFAULT 1 NOT NULL,
    main_validator_uid INTEGER,
    main_validator_hotkey VARCHAR(128),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    minimum_validator_version VARCHAR(32),
    CONSTRAINT app_runtime_config_singleton CHECK (id = 1),
    CONSTRAINT app_runtime_config_pkey PRIMARY KEY (id)
);

CREATE TABLE IF NOT EXISTS config_season_round (
    id SMALLINT DEFAULT 1 NOT NULL,
    round_size_epochs DOUBLE PRECISION NOT NULL,
    season_size_epochs DOUBLE PRECISION NOT NULL,
    minimum_start_block BIGINT NOT NULL,
    blocks_per_epoch INTEGER DEFAULT 360 NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_by_validator_uid INTEGER,
    CONSTRAINT round_config_singleton CHECK (id = 1),
    CONSTRAINT round_config_pkey PRIMARY KEY (id)
);

CREATE TABLE IF NOT EXISTS seasons (
    season_id BIGSERIAL PRIMARY KEY,
    season_number INTEGER NOT NULL UNIQUE,
    status VARCHAR(32) NOT NULL DEFAULT 'active',
    start_block BIGINT,
    end_block BIGINT,
    start_at TIMESTAMPTZ,
    end_at TIMESTAMPTZ,
    required_improvement_pct DOUBLE PRECISION NOT NULL DEFAULT 0.05,
    leader_miner_uid INTEGER,
    leader_reward DOUBLE PRECISION,
    leader_github_url TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS rounds (
    round_id BIGSERIAL PRIMARY KEY,
    season_id BIGINT NOT NULL REFERENCES seasons(season_id) ON DELETE CASCADE,
    round_number_in_season INTEGER NOT NULL,
    start_block BIGINT,
    end_block BIGINT,
    planned_start_block BIGINT,
    planned_end_block BIGINT,
    start_epoch INTEGER,
    end_epoch INTEGER,
    started_at TIMESTAMPTZ,
    ended_at TIMESTAMPTZ,
    opened_by_validator_uid INTEGER,
    closed_by_validator_uid INTEGER,
    authority_mode VARCHAR(16),
    status VARCHAR(32) NOT NULL DEFAULT 'active',
    consensus_status VARCHAR(32) NOT NULL DEFAULT 'pending',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_rounds_season_round UNIQUE (season_id, round_number_in_season)
);

CREATE TABLE IF NOT EXISTS round_validators (
    round_validator_id BIGSERIAL PRIMARY KEY,
    round_id BIGINT REFERENCES rounds(round_id) ON DELETE CASCADE,
    season_number INTEGER,
    round_number_in_season INTEGER,
    start_block BIGINT,
    end_block BIGINT,
    start_epoch INTEGER,
    end_epoch INTEGER,
    pending_round_link BOOLEAN NOT NULL DEFAULT FALSE,
    validator_uid INTEGER,
    validator_hotkey VARCHAR(128),
    validator_coldkey VARCHAR(128),
    validator_round_id VARCHAR(128),
    name VARCHAR(256),
    image_url TEXT,
    version VARCHAR(64),
    stake DOUBLE PRECISION,
    vtrust DOUBLE PRECISION,
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    config JSONB,
    post_consensus_json JSONB,
    ipfs_uploaded JSONB,
    ipfs_downloaded JSONB,
    s3_logs_url TEXT,
    validator_state JSONB,
    is_main_validator BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS round_validator_miners (
    id BIGSERIAL PRIMARY KEY,
    round_validator_id BIGINT NOT NULL REFERENCES round_validators(round_validator_id) ON DELETE CASCADE,
    round_id BIGINT REFERENCES rounds(round_id) ON DELETE CASCADE,
    miner_uid INTEGER NOT NULL,
    miner_hotkey VARCHAR(128),
    miner_coldkey VARCHAR(128),
    name VARCHAR(256),
    image_url TEXT,
    github_url TEXT,
    is_sota BOOLEAN NOT NULL DEFAULT FALSE,
    version VARCHAR(64),
    local_avg_reward DOUBLE PRECISION,
    local_avg_eval_score DOUBLE PRECISION,
    local_avg_eval_time DOUBLE PRECISION,
    local_tasks_received INTEGER,
    local_tasks_success INTEGER,
    local_avg_eval_cost DOUBLE PRECISION,
    post_consensus_rank INTEGER,
    post_consensus_avg_reward DOUBLE PRECISION,
    post_consensus_avg_eval_score DOUBLE PRECISION,
    post_consensus_avg_eval_time DOUBLE PRECISION,
    post_consensus_tasks_received INTEGER,
    post_consensus_tasks_success INTEGER,
    post_consensus_avg_eval_cost DOUBLE PRECISION,
    best_local_rank INTEGER,
    best_local_reward DOUBLE PRECISION,
    best_local_eval_score DOUBLE PRECISION,
    best_local_eval_time DOUBLE PRECISION,
    best_local_tasks_received INTEGER,
    best_local_tasks_success INTEGER,
    best_local_eval_cost DOUBLE PRECISION,
    weight DOUBLE PRECISION,
    subnet_price DOUBLE PRECISION,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_round_validator_miners_round_validator_miner UNIQUE (round_validator_id, miner_uid)
);

CREATE TABLE IF NOT EXISTS round_summary (
    round_summary_id BIGSERIAL PRIMARY KEY,
    round_id BIGINT NOT NULL UNIQUE REFERENCES rounds(round_id) ON DELETE CASCADE,
    source_round_validator_id BIGINT REFERENCES round_validators(round_validator_id) ON DELETE SET NULL,
    source_validator_uid INTEGER,
    source_is_main_validator BOOLEAN NOT NULL DEFAULT FALSE,
    leader_before_miner_uid INTEGER,
    leader_before_miner_hotkey VARCHAR(128),
    leader_before_github_url TEXT,
    leader_before_reward DOUBLE PRECISION,
    candidate_miner_uid INTEGER,
    candidate_miner_hotkey VARCHAR(128),
    candidate_github_url TEXT,
    candidate_reward DOUBLE PRECISION,
    leader_after_miner_uid INTEGER,
    leader_after_miner_hotkey VARCHAR(128),
    leader_after_github_url TEXT,
    leader_after_reward DOUBLE PRECISION,
    required_improvement_pct DOUBLE PRECISION,
    required_reward_to_dethrone DOUBLE PRECISION,
    dethroned BOOLEAN,
    validators_count INTEGER,
    miners_evaluated INTEGER,
    tasks_evaluated INTEGER,
    tasks_success INTEGER,
    avg_reward DOUBLE PRECISION,
    avg_eval_score DOUBLE PRECISION,
    avg_eval_time DOUBLE PRECISION,
    avg_eval_cost DOUBLE PRECISION,
    leader_after_eval_score DOUBLE PRECISION,
    leader_after_eval_time DOUBLE PRECISION,
    leader_after_eval_cost DOUBLE PRECISION,
    post_consensus_json JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS miner_evaluation_runs (
    id SERIAL PRIMARY KEY,
    agent_run_id VARCHAR(128) NOT NULL UNIQUE,
    validator_round_id VARCHAR(128) NOT NULL,
    round_validator_id BIGINT,
    miner_uid INTEGER,
    miner_hotkey VARCHAR(128),
    started_at DOUBLE PRECISION NOT NULL,
    ended_at DOUBLE PRECISION,
    elapsed_sec DOUBLE PRECISION,
    average_score DOUBLE PRECISION,
    average_execution_time DOUBLE PRECISION,
    average_reward DOUBLE PRECISION,
    total_tasks INTEGER NOT NULL,
    success_tasks INTEGER NOT NULL,
    failed_tasks INTEGER NOT NULL,
    tasks_attempted INTEGER,
    zero_reason VARCHAR(128),
    early_stop_reason VARCHAR(128),
    early_stop_message TEXT,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);

ALTER TABLE miner_evaluation_runs ADD COLUMN IF NOT EXISTS tasks_attempted INTEGER;
ALTER TABLE miner_evaluation_runs ADD COLUMN IF NOT EXISTS early_stop_reason VARCHAR(128);
ALTER TABLE miner_evaluation_runs ADD COLUMN IF NOT EXISTS early_stop_message TEXT;

CREATE TABLE IF NOT EXISTS tasks (
    id SERIAL PRIMARY KEY,
    task_id VARCHAR(128) NOT NULL UNIQUE,
    validator_round_id VARCHAR(128) NOT NULL,
    round_validator_id BIGINT,
    web_project_id VARCHAR(128),
    web_version VARCHAR(64),
    url VARCHAR(1024) NOT NULL,
    prompt TEXT NOT NULL,
    specifications JSONB NOT NULL,
    tests JSONB NOT NULL,
    use_case JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS evaluations (
    id SERIAL PRIMARY KEY,
    evaluation_id VARCHAR(128) NOT NULL UNIQUE,
    validator_round_id VARCHAR(128) NOT NULL,
    agent_run_id VARCHAR(128) NOT NULL REFERENCES miner_evaluation_runs(agent_run_id) ON DELETE CASCADE,
    task_id VARCHAR(128) NOT NULL REFERENCES tasks(task_id) ON DELETE CASCADE,
    task_solution_id VARCHAR(128) NOT NULL,
    miner_uid INTEGER,
    miner_hotkey VARCHAR(128),
    validator_uid INTEGER NOT NULL,
    validator_hotkey VARCHAR(128) NOT NULL,
    evaluation_score DOUBLE PRECISION NOT NULL,
    reward DOUBLE PRECISION NOT NULL,
    evaluation_time DOUBLE PRECISION NOT NULL,
    gif_recording TEXT,
    extra_info JSONB NOT NULL,
    zero_reason VARCHAR(128),
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS evaluations_execution_history (
    id SERIAL PRIMARY KEY,
    evaluation_id VARCHAR(128) NOT NULL REFERENCES evaluations(evaluation_id) ON DELETE CASCADE,
    execution_history JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS task_solutions (
    id SERIAL PRIMARY KEY,
    solution_id VARCHAR(128) NOT NULL UNIQUE,
    task_id VARCHAR(128) NOT NULL REFERENCES tasks(task_id) ON DELETE CASCADE,
    agent_run_id VARCHAR(128) NOT NULL REFERENCES miner_evaluation_runs(agent_run_id) ON DELETE CASCADE,
    validator_round_id VARCHAR(128) NOT NULL,
    validator_uid INTEGER NOT NULL,
    validator_hotkey VARCHAR(128) NOT NULL,
    miner_uid INTEGER,
    miner_hotkey VARCHAR(128),
    actions JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS evaluation_llm_usage (
    id SERIAL PRIMARY KEY,
    evaluation_id VARCHAR(128) NOT NULL REFERENCES evaluations(evaluation_id) ON DELETE CASCADE,
    provider VARCHAR(64),
    model VARCHAR(128),
    tokens INTEGER,
    cost DOUBLE PRECISION,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS task_execution_logs (
    id SERIAL PRIMARY KEY,
    task_id VARCHAR(128) NOT NULL REFERENCES tasks(task_id) ON DELETE CASCADE,
    agent_run_id VARCHAR(128) NOT NULL REFERENCES miner_evaluation_runs(agent_run_id) ON DELETE CASCADE,
    validator_round_id VARCHAR(128) NOT NULL,
    validator_uid INTEGER,
    miner_uid INTEGER,
    season INTEGER,
    round_in_season INTEGER,
    payload_ref VARCHAR(512) NOT NULL,
    payload_size INTEGER,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT uq_task_execution_log UNIQUE (task_id, agent_run_id)
);

-- ============================================================
-- INDEXES
-- ============================================================

CREATE UNIQUE INDEX IF NOT EXISTS ux_round_validators_validator_round_id ON round_validators(validator_round_id);
CREATE INDEX IF NOT EXISTS ix_round_validators_round_id ON round_validators(round_id);
CREATE INDEX IF NOT EXISTS ix_round_validators_uid ON round_validators(validator_uid);
CREATE INDEX IF NOT EXISTS ux_round_validators_round_uid ON round_validators(round_id, validator_uid);
CREATE INDEX IF NOT EXISTS ix_round_validators_season_round ON round_validators(season_number, round_number_in_season);
CREATE INDEX IF NOT EXISTS ix_round_validators_pending_link ON round_validators(pending_round_link);
CREATE INDEX IF NOT EXISTS ix_rounds_season_id ON rounds(season_id);
CREATE INDEX IF NOT EXISTS ix_rounds_season_round ON rounds(season_id, round_number_in_season);
CREATE INDEX IF NOT EXISTS ix_rounds_status ON rounds(status);
CREATE INDEX IF NOT EXISTS ix_round_summary_leader_after_miner_uid ON round_summary(leader_after_miner_uid);
CREATE INDEX IF NOT EXISTS ix_round_validator_miners_round_id ON round_validator_miners(round_id);
CREATE INDEX IF NOT EXISTS ix_round_validator_miners_miner_uid ON round_validator_miners(miner_uid);
CREATE INDEX IF NOT EXISTS ix_round_validator_miners_round_validator_id ON round_validator_miners(round_validator_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_rounds_one_active_per_season ON rounds(season_id) WHERE LOWER(COALESCE(status, '')) = 'active';
CREATE UNIQUE INDEX IF NOT EXISTS uq_seasons_one_active_global ON seasons((1)) WHERE LOWER(COALESCE(status, '')) = 'active';
CREATE INDEX IF NOT EXISTS ix_miner_evaluation_runs_agent_run_id ON miner_evaluation_runs(agent_run_id);
CREATE INDEX IF NOT EXISTS ix_miner_evaluation_runs_miner_uid ON miner_evaluation_runs(miner_uid);
CREATE INDEX IF NOT EXISTS ix_miner_evaluation_runs_miner_hotkey ON miner_evaluation_runs(miner_hotkey);
CREATE INDEX IF NOT EXISTS ix_miner_evaluation_runs_validator_round_id ON miner_evaluation_runs(validator_round_id);
CREATE INDEX IF NOT EXISTS ix_miner_eval_runs_round_validator_id ON miner_evaluation_runs(round_validator_id);
CREATE INDEX IF NOT EXISTS ix_agent_run_round ON miner_evaluation_runs(validator_round_id, agent_run_id);
CREATE INDEX IF NOT EXISTS ix_tasks_task_id ON tasks(task_id);
CREATE INDEX IF NOT EXISTS ix_tasks_validator_round_id ON tasks(validator_round_id);
CREATE INDEX IF NOT EXISTS ix_task_round ON tasks(validator_round_id, task_id);
CREATE INDEX IF NOT EXISTS ix_tasks_round_validator_id ON tasks(round_validator_id);
CREATE INDEX IF NOT EXISTS ix_evaluations_evaluation_id ON evaluations(evaluation_id);
CREATE INDEX IF NOT EXISTS ix_evaluations_agent_run_id ON evaluations(agent_run_id);
CREATE INDEX IF NOT EXISTS ix_evaluations_task_id ON evaluations(task_id);
CREATE INDEX IF NOT EXISTS ix_evaluations_task_solution_id ON evaluations(task_solution_id);
CREATE INDEX IF NOT EXISTS ix_evaluations_miner_uid ON evaluations(miner_uid);
CREATE INDEX IF NOT EXISTS ix_evaluations_miner_hotkey ON evaluations(miner_hotkey);
CREATE INDEX IF NOT EXISTS ix_evaluations_validator_uid ON evaluations(validator_uid);
CREATE INDEX IF NOT EXISTS ix_evaluations_validator_hotkey ON evaluations(validator_hotkey);
CREATE INDEX IF NOT EXISTS ix_evaluations_validator_round_id ON evaluations(validator_round_id);
CREATE INDEX IF NOT EXISTS ix_evaluation_round ON evaluations(validator_round_id, evaluation_id);
CREATE INDEX IF NOT EXISTS ix_evaluations_execution_history_evaluation_id ON evaluations_execution_history(evaluation_id);
CREATE INDEX IF NOT EXISTS ix_task_solutions_solution_id ON task_solutions(solution_id);
CREATE INDEX IF NOT EXISTS ix_task_solutions_task_id ON task_solutions(task_id);
CREATE INDEX IF NOT EXISTS ix_task_solutions_agent_run_id ON task_solutions(agent_run_id);
CREATE INDEX IF NOT EXISTS ix_task_solutions_miner_uid ON task_solutions(miner_uid);
CREATE INDEX IF NOT EXISTS ix_task_solutions_miner_hotkey ON task_solutions(miner_hotkey);
CREATE INDEX IF NOT EXISTS ix_task_solutions_validator_uid ON task_solutions(validator_uid);
CREATE INDEX IF NOT EXISTS ix_task_solutions_validator_hotkey ON task_solutions(validator_hotkey);
CREATE INDEX IF NOT EXISTS ix_task_solutions_validator_round_id ON task_solutions(validator_round_id);
CREATE INDEX IF NOT EXISTS ix_solution_task ON task_solutions(task_id, solution_id);
CREATE INDEX IF NOT EXISTS ix_eval_llm_usage_eval_id ON evaluation_llm_usage(evaluation_id);
CREATE INDEX IF NOT EXISTS ix_task_execution_logs_task_id ON task_execution_logs(task_id);
CREATE INDEX IF NOT EXISTS ix_task_execution_logs_agent_run_id ON task_execution_logs(agent_run_id);
CREATE INDEX IF NOT EXISTS ix_task_execution_logs_round ON task_execution_logs(validator_round_id);
CREATE INDEX IF NOT EXISTS ix_task_execution_logs_miner_uid ON task_execution_logs(miner_uid);
CREATE INDEX IF NOT EXISTS ix_task_execution_logs_validator_uid ON task_execution_logs(validator_uid);
CREATE INDEX IF NOT EXISTS ix_task_execution_logs_season ON task_execution_logs(season);
CREATE INDEX IF NOT EXISTS ix_task_execution_logs_round_in_season ON task_execution_logs(round_in_season);

-- ============================================================
-- VIEWS
-- ============================================================

CREATE OR REPLACE VIEW round_outcomes AS
SELECT round_summary_id AS round_outcome_id, round_id,
    leader_after_miner_uid AS winner_miner_uid, leader_after_reward AS winner_score,
    leader_before_miner_uid AS reigning_miner_uid_before_round, leader_before_reward AS reigning_score_before_round,
    candidate_miner_uid AS top_candidate_miner_uid, candidate_reward AS top_candidate_score,
    required_improvement_pct, dethroned, validators_count, miners_evaluated,
    tasks_evaluated, tasks_success, avg_reward, avg_eval_score, avg_eval_time,
    NULL::TIMESTAMPTZ AS computed_at, post_consensus_json,
    NULL::BIGINT AS source_round_validator_id, created_at, updated_at
FROM round_summary;

CREATE OR REPLACE VIEW validator_rounds AS
SELECT rv.round_validator_id AS id, rv.validator_round_id::TEXT AS validator_round_id,
    COALESCE(s.season_number, rv.season_number) AS season_number,
    COALESCE(r.round_number_in_season, rv.round_number_in_season) AS round_number_in_season,
    COALESCE(r.start_block, rv.start_block, 0) AS start_block,
    COALESCE(r.end_block, rv.end_block) AS end_block,
    COALESCE(r.start_epoch::INTEGER, rv.start_epoch, 0) AS start_epoch,
    COALESCE(r.end_epoch::INTEGER, rv.end_epoch) AS end_epoch,
    COALESCE(EXTRACT(EPOCH FROM r.started_at)::DOUBLE PRECISION, EXTRACT(EPOCH FROM rv.started_at)::DOUBLE PRECISION, 0.0) AS started_at,
    COALESCE(EXTRACT(EPOCH FROM r.ended_at)::DOUBLE PRECISION, EXTRACT(EPOCH FROM rv.finished_at)::DOUBLE PRECISION) AS ended_at,
    COALESCE(t.tasks_count, 0) AS n_tasks,
    COALESCE(r.status, 'active')::VARCHAR(32) AS status,
    rv.post_consensus_json AS validator_summary, rv.s3_logs_url,
    NULL::INTEGER AS winner_uid, NULL::DOUBLE PRECISION AS winner_score,
    NULL::INTEGER AS reigning_uid_before_round, NULL::DOUBLE PRECISION AS reigning_score_before_round,
    NULL::INTEGER AS top_candidate_uid, NULL::DOUBLE PRECISION AS top_candidate_score,
    NULL::DOUBLE PRECISION AS required_improvement_pct, NULL::BOOLEAN AS dethroned,
    rv.created_at, rv.updated_at
FROM round_validators rv
LEFT JOIN rounds r ON r.round_id = rv.round_id
LEFT JOIN seasons s ON s.season_id = r.season_id
LEFT JOIN (SELECT round_validator_id, COUNT(*)::INTEGER AS tasks_count FROM tasks GROUP BY round_validator_id) t
    ON t.round_validator_id = rv.round_validator_id;

CREATE OR REPLACE VIEW validator_round_validators AS
SELECT round_validator_id AS id, validator_round_id::TEXT AS validator_round_id,
    validator_uid, validator_hotkey, validator_coldkey, name, stake, vtrust,
    image_url, version, config, created_at, updated_at
FROM round_validators;

CREATE OR REPLACE VIEW validator_round_miners AS
SELECT rvm.id, rv.validator_round_id::TEXT AS validator_round_id,
    rvm.miner_uid, rvm.miner_hotkey, rvm.miner_coldkey,
    COALESCE(rvm.name, CONCAT('miner ', rvm.miner_uid)::VARCHAR(256))::VARCHAR(256) AS name,
    rvm.image_url, rvm.github_url, COALESCE(rvm.is_sota, FALSE) AS is_sota,
    rvm.version, rvm.created_at, rvm.updated_at
FROM round_validator_miners rvm
JOIN round_validators rv ON rv.round_validator_id = rvm.round_validator_id;

CREATE OR REPLACE VIEW validator_round_summary_miners AS
SELECT rvm.id, rv.validator_round_id::TEXT AS validator_round_id,
    rvm.miner_uid, rvm.miner_hotkey,
    rvm.local_avg_reward, rvm.local_avg_eval_score, rvm.local_avg_eval_time, rvm.local_avg_eval_cost,
    rvm.local_tasks_received, rvm.local_tasks_success,
    rvm.post_consensus_rank, rvm.post_consensus_avg_reward, rvm.post_consensus_avg_eval_score,
    rvm.post_consensus_avg_eval_time, rvm.post_consensus_avg_eval_cost,
    rvm.post_consensus_tasks_received, rvm.post_consensus_tasks_success,
    rvm.weight, rvm.subnet_price, rvm.created_at, rvm.updated_at
FROM round_validator_miners rvm
JOIN round_validators rv ON rv.round_validator_id = rvm.round_validator_id;

-- ============================================================
-- TRIGGERS
-- ============================================================

DROP TRIGGER IF EXISTS trg_normalize_round_boundaries ON rounds;
CREATE TRIGGER trg_normalize_round_boundaries
BEFORE INSERT OR UPDATE ON rounds FOR EACH ROW EXECUTE FUNCTION normalize_round_boundaries();

DROP TRIGGER IF EXISTS trg_normalize_round_validator_boundaries ON round_validators;
CREATE TRIGGER trg_normalize_round_validator_boundaries
BEFORE INSERT OR UPDATE ON round_validators FOR EACH ROW EXECUTE FUNCTION normalize_round_validator_boundaries();

DROP TRIGGER IF EXISTS trg_config_season_round_enforce_main_validator ON config_season_round;
CREATE TRIGGER trg_config_season_round_enforce_main_validator
BEFORE INSERT OR UPDATE ON config_season_round FOR EACH ROW EXECUTE FUNCTION enforce_config_season_round_main_validator();

DROP TRIGGER IF EXISTS trg_compat_fill_round_validator_id_tasks ON tasks;
CREATE TRIGGER trg_compat_fill_round_validator_id_tasks
BEFORE INSERT OR UPDATE OF round_validator_id, validator_round_id ON tasks
FOR EACH ROW EXECUTE FUNCTION compat_fill_round_validator_id_tasks();

DROP TRIGGER IF EXISTS trg_compat_fill_round_validator_id_runs ON miner_evaluation_runs;
CREATE TRIGGER trg_compat_fill_round_validator_id_runs
BEFORE INSERT OR UPDATE OF round_validator_id, validator_round_id ON miner_evaluation_runs
FOR EACH ROW EXECUTE FUNCTION compat_fill_round_validator_id_runs();

DROP TRIGGER IF EXISTS trg_compat_validator_round_miners_iou ON validator_round_miners;
CREATE TRIGGER trg_compat_validator_round_miners_iou
INSTEAD OF INSERT OR UPDATE OR DELETE ON validator_round_miners
FOR EACH ROW EXECUTE FUNCTION compat_validator_round_miners_iou();

DROP TRIGGER IF EXISTS trg_compat_validator_round_summary_miners_iou ON validator_round_summary_miners;
CREATE TRIGGER trg_compat_validator_round_summary_miners_iou
INSTEAD OF INSERT OR UPDATE OR DELETE ON validator_round_summary_miners
FOR EACH ROW EXECUTE FUNCTION compat_validator_round_summary_miners_iou();

DROP TRIGGER IF EXISTS trg_compat_validator_round_validators_iou ON validator_round_validators;
CREATE TRIGGER trg_compat_validator_round_validators_iou
INSTEAD OF INSERT OR UPDATE OR DELETE ON validator_round_validators
FOR EACH ROW EXECUTE FUNCTION compat_validator_round_validators_iou();

DROP TRIGGER IF EXISTS trg_compat_validator_rounds_iou ON validator_rounds;
CREATE TRIGGER trg_compat_validator_rounds_iou
INSTEAD OF INSERT OR UPDATE OR DELETE ON validator_rounds
FOR EACH ROW EXECUTE FUNCTION compat_validator_rounds_iou();

SQL

echo "✅ Schema created successfully in '${POSTGRES_DB}'."

# Bootstrap config_app_runtime
psql \
  --host="${POSTGRES_HOST}" \
  --port="${POSTGRES_PORT}" \
  --username="${POSTGRES_USER}" \
  --dbname="${POSTGRES_DB}" \
  --set=ON_ERROR_STOP=1 \
  --set=main_uid="${MAIN_VALIDATOR_UID}" \
  --set=main_hotkey="${MAIN_VALIDATOR_HOTKEY}" \
  --set=min_validator_version="${MINIMUM_VALIDATOR_VERSION}" \
<<'SQL'
INSERT INTO config_app_runtime (
  id, main_validator_uid, main_validator_hotkey, minimum_validator_version, created_at, updated_at
) VALUES (
  1, :main_uid, NULLIF(:'main_hotkey', ''), NULLIF(:'min_validator_version', ''), NOW(), NOW()
)
ON CONFLICT (id) DO UPDATE SET
  main_validator_uid = EXCLUDED.main_validator_uid,
  main_validator_hotkey = COALESCE(EXCLUDED.main_validator_hotkey, config_app_runtime.main_validator_hotkey),
  minimum_validator_version = COALESCE(EXCLUDED.minimum_validator_version, config_app_runtime.minimum_validator_version),
  updated_at = NOW();
SQL

echo "✅ config_app_runtime bootstrapped (main_uid=${MAIN_VALIDATOR_UID})."
echo "ℹ️  config_season_round is left empty; main validator will sync it via the API."
