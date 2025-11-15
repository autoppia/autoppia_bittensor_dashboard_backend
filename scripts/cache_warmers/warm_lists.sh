#!/bin/bash
# Cache Warmer: General Lists
# Frequency: Every 10 minutes
# Purpose: Pre-warms general list endpoints (less critical, less frequent)

BASE='http://localhost:8080/api/v1'
LOG_DIR="${LOG_DIR:-/var/log/autoppia}"
mkdir -p "$LOG_DIR"

# General lists
curl -s "$BASE/rounds?page=1&limit=20" > /dev/null &
curl -s "$BASE/miner-list?limit=100" > /dev/null &
curl -s "$BASE/agent-runs?page=1&limit=20" > /dev/null &
curl -s "$BASE/agents?limit=20" > /dev/null &

wait
echo "$(date +%T): Lists warmed (4 endpoints)" >> "$LOG_DIR/lists_warmer.log"

