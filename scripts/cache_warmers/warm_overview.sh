#!/bin/bash
# Cache Warmer: Overview Metrics
# Frequency: Every 5 minutes
# Purpose: Pre-warms overview/homepage critical endpoints

BASE='http://localhost:8080/api/v1/overview'
LOG_DIR="${LOG_DIR:-/var/log/autoppia}"
mkdir -p "$LOG_DIR"

# Overview endpoints (critical for homepage)
curl -s "$BASE/metrics" > /dev/null &
curl -s "$BASE/validators?limit=6" > /dev/null &
curl -s "$BASE/leaderboard?timeRange=15R" > /dev/null &
curl -s "$BASE/network-status" > /dev/null &
curl -s "$BASE/statistics" > /dev/null &
curl -s "$BASE/rounds/current" > /dev/null &

wait
echo "$(date +%T): Overview warmed (6 endpoints)" >> "$LOG_DIR/overview_warmer.log"



