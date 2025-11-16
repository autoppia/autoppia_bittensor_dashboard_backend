#!/bin/bash
# Setup Cache Warmers
# Master script to install and configure all cache warming cron jobs
#
# This script sets up a multi-frequency cache warming system:
# - Every 2 minutes: Current round (most dynamic)
# - Every 5 minutes: Recent rounds (3 previous rounds)
# - Every 5 minutes: Overview metrics
# - Every 10 minutes: General lists
#
# Usage: ./setup_cache_warmers.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="/root/cache_warmers"
LOG_DIR="/var/log/autoppia"

echo "=" * 80
echo "🚀 Autoppia Cache Warmers - Setup"
echo "=" * 80
echo

# Create directories
echo "📁 Creating directories..."
mkdir -p "$INSTALL_DIR"
mkdir -p "$LOG_DIR"
echo "  ✅ $INSTALL_DIR"
echo "  ✅ $LOG_DIR"
echo

# Copy scripts
echo "📋 Installing cache warmer scripts..."
cp "$SCRIPT_DIR/warm_current_round.sh" "$INSTALL_DIR/"
cp "$SCRIPT_DIR/warm_recent_rounds.sh" "$INSTALL_DIR/"
cp "$SCRIPT_DIR/warm_overview.sh" "$INSTALL_DIR/"
cp "$SCRIPT_DIR/warm_lists.sh" "$INSTALL_DIR/"

# Make executable
chmod +x "$INSTALL_DIR"/*.sh
echo "  ✅ 4 scripts installed and made executable"
echo

# Configure cron
echo "⏰ Configuring cron jobs..."
cat > /tmp/autoppia_cache_cron.txt << 'CRONEOF'
# Autoppia Cache Warmers - Multi-frequency approach
# Different data types have different refresh rates

# Current round: Every 2 minutes (most dynamic)
*/2 * * * * /root/cache_warmers/warm_current_round.sh

# Recent rounds (previous 3): Every 5 minutes
*/5 * * * * /root/cache_warmers/warm_recent_rounds.sh

# Overview metrics: Every 5 minutes
*/5 * * * * /root/cache_warmers/warm_overview.sh

# General lists: Every 10 minutes
*/10 * * * * /root/cache_warmers/warm_lists.sh

# Health check: Every 5 minutes
*/5 * * * * /root/check_and_restart_if_needed.sh
CRONEOF

crontab /tmp/autoppia_cache_cron.txt
rm /tmp/autoppia_cache_cron.txt
echo "  ✅ Cron jobs configured"
echo

# Show current crontab
echo "📅 Active cron jobs:"
crontab -l | grep -E "cache_warmers|check_and_restart"
echo

# Run initial cache warming
echo "🔄 Running initial cache warming..."
echo "  (This will take ~30 seconds)"
"$INSTALL_DIR/warm_overview.sh" &
"$INSTALL_DIR/warm_current_round.sh" &
"$INSTALL_DIR/warm_recent_rounds.sh" &
"$INSTALL_DIR/warm_lists.sh" &
wait
echo "  ✅ Initial cache populated"
echo

# Show status
echo "=" * 80
echo "✅ Cache Warmers Setup Complete"
echo "=" * 80
echo
echo "📊 Configuration:"
echo "  - Current round:   Every 2 minutes"
echo "  - Recent rounds:   Every 5 minutes (3 previous)"
echo "  - Overview:        Every 5 minutes"
echo "  - Lists:           Every 10 minutes"
echo
echo "📁 Locations:"
echo "  - Scripts:  $INSTALL_DIR/"
echo "  - Logs:     $LOG_DIR/*_warmer.log"
echo
echo "📝 Monitor logs:"
echo "  tail -f $LOG_DIR/*_warmer.log"
echo
echo "🔄 Manual execution:"
echo "  $INSTALL_DIR/warm_current_round.sh"
echo



