#!/bin/bash
# One-off (night of 12 Oct 2026): retrain Tuesday and Friday on clean data (no ingest template answers). Tuesday
# becomes an adapter on Tuesday Core (exam incl. privacy veto); Friday stays a fused Ministral on Ollama.
cd "$(dirname "$0")/.."
LOG=data/logs/auto-retrain.log
echo "[$(date '+%Y-%m-%d %H:%M')] one-off: retraining tuesday and friday on clean data" >> "$LOG"
caffeinate -i -s .venv/bin/python train/auto_retrain.py --force --minds tuesday,friday >> data/logs/train-2026-10-12.log 2>&1
echo "[$(date '+%Y-%m-%d %H:%M')] one-off: done (exit $?)" >> "$LOG"
launchctl unload "$HOME/Library/LaunchAgents/com.tuesday.retrain-once.plist" 2>/dev/null
rm -f "$HOME/Library/LaunchAgents/com.tuesday.retrain-once.plist"
