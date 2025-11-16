"""Cache TTL configuration shared across services (Redis-only)."""

from __future__ import annotations

# This module intentionally contains only TTL constants.
# All caching must go through Redis utilities in `app.services.redis_cache`.

CACHE_TTL = {
    "overview_metrics": 300,  # 5 minutes - overview data changes slowly
    "validators_list": 600,  # 10 minutes - validator list rarely changes
    "rounds_list": 180,  # 3 minutes - rounds list changes occasionally
    "round_detail": 300,  # 5 minutes - round details are static once created
    "round_miners": 120,  # 2 minutes - miner data changes more frequently
    "round_validators": 300,  # 5 minutes - validator data per round
    "round_statistics": 180,  # 3 minutes - statistics change moderately
    "round_detail_final": 86400,  # 24 hours - finalised rounds are immutable
    "round_miners_final": 86400,  # 24 hours - finalised miner stats are immutable
    "round_validators_final": 86400,  # 24 hours - finalised validator stats are immutable
    "round_statistics_final": 86400,  # 24 hours - finalised round statistics are immutable
    "current_round": 60,  # 1 minute - current round changes more frequently
    "network_status": 120,  # 2 minutes - network status changes moderately
    "leaderboard": 300,  # 5 minutes - leaderboard data changes slowly
    "agents_list": 600,  # 10 minutes - agents list rarely changes
    "miner_list": 180,  # 3 minutes - miner list changes moderately
    "miner_detail": 300,  # 5 minutes - individual miner details change slowly
    # Agent runs cache TTLs
    "agent_run_detail": 60,  # 1 minute - agent run details change frequently
    "agent_run_personas": 300,  # 5 minutes - personas data changes slowly
    "agent_run_stats": 120,  # 2 minutes - statistics change moderately
    "agent_run_summary": 60,  # 1 minute - summary changes frequently
    "agent_run_tasks": 30,  # 30 seconds - tasks data changes frequently
    "agent_runs_by_agent": 60,  # 1 minute - agent runs list changes moderately
    "agent_runs_by_round": 60,  # 1 minute - agent runs list changes moderately
    "agent_runs_by_validator": 60,  # 1 minute - agent runs list changes moderately
    "agent_run_timeline": 0,  # No caching - timeline is real-time
    "agent_run_logs": 0,  # No caching - logs are real-time
    "agent_run_metrics": 30,  # 30 seconds - metrics change frequently
}

__all__ = ["CACHE_TTL"]


