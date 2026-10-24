#!/bin/bash
# One-off (12 Oct 2026, 00:25): Saturday and Wednesday with the capped "pass" rows (Monday and Thursday over-learned
# "pass" at 25 rows and stay on their base until the next retraining), then start Tuesday again.
cd "$(dirname "$0")/.."
LOG=data/logs/auto-retrain.log
note() { echo "[$(date '+%Y-%m-%d %H:%M')] resume: $*" | tee -a "$LOG"; }
note "training saturday, wednesday with capped pass rows"
caffeinate -i -s .venv/bin/python train/auto_retrain.py --force --minds saturday,wednesday >> data/logs/train-all-2026-10-11.log 2>&1
note "finished (exit $?)"
launchctl load -w "$HOME/Library/LaunchAgents/com.tuesday.assistant.plist" 2>/dev/null
note "Tuesday service loaded"
