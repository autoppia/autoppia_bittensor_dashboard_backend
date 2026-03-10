#!/bin/bash
# Cache Warmer: Recent Rounds (previous 3)
# Frequency: Every 5 minutes
# Purpose: Pre-warms the 3 rounds before current (stable, completed rounds)

BASE='http://localhost:8080'
LOG_DIR="${LOG_DIR:-/var/log/autoppia}"
mkdir -p "$LOG_DIR"

# Get current round
CURRENT=$(curl -s "$BASE/api/v1/overview/rounds/current" | python3 -c "import sys, json; data=json.load(sys.stdin); print(data.get('data', {}).get('round', {}).get('roundNumber', 16))" 2>/dev/null || echo "16")

# Pre-warm the 3 previous rounds
for i in 1 2 3; do
    R=$((CURRENT - i))
    if [ "$R" -gt 0 ]; then
        curl -s "$BASE/api/v1/rounds/$R" > /dev/null &
        curl -s "$BASE/api/v1/rounds/$R/basic" > /dev/null &
    fi
done

wait
echo "$(date +%T): Recent rounds $((CURRENT-3))-$((CURRENT-1)) warmed" >> "$LOG_DIR/recent_rounds_warmer.log"
