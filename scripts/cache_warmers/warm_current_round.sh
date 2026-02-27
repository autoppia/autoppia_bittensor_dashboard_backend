#!/bin/bash
# Cache Warmer: Current Round
# Frequency: Every 2 minutes
# Purpose: Pre-warms the most dynamic data (current round and related endpoints)

BASE='http://localhost:8080'
LOG_DIR="${LOG_DIR:-/var/log/autoppia}"
mkdir -p "$LOG_DIR"

# Get current round number
CURRENT=$(curl -s "$BASE/api/v1/overview/rounds/current" | python3 -c "import sys, json; data=json.load(sys.stdin); print(data.get('data', {}).get('round', {}).get('roundNumber', 16))" 2>/dev/null || echo "16")

# Pre-warm current round and all its related data
curl -s "$BASE/api/v1/rounds/$CURRENT" > /dev/null &
curl -s "$BASE/api/v1/rounds/$CURRENT/basic" > /dev/null &
curl -s "$BASE/api/v1/rounds/$CURRENT/miners" > /dev/null &
curl -s "$BASE/api/v1/rounds/$CURRENT/validators" > /dev/null &
curl -s "$BASE/api/v1/miner-list?limit=100&round=$CURRENT" > /dev/null &
curl -s "$BASE/api/v1/agent-runs?roundId=$CURRENT&page=1&limit=20" > /dev/null &

wait
echo "$(date +%T): Current round $CURRENT warmed" >> "$LOG_DIR/current_round_warmer.log"
