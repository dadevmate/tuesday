#!/bin/bash
# One-off (12 Oct 2026): after Tuesday and Friday, retrain Monday and Thursday with the capped "pass" rows.
cd "$(dirname "$0")/.."
LOG=data/logs/auto-retrain.log
while pgrep -f "auto_retrain.py --force --minds tuesday,friday" >/dev/null; do sleep 20; done
sleep 30   # let the service come back and settle; auto_retrain pauses it again
echo "[$(date '+%Y-%m-%d %H:%M')] one-off: retraining monday and thursday" >> "$LOG"
caffeinate -i -s .venv/bin/python train/auto_retrain.py --force --minds monday,thursday >> data/logs/train-2026-10-12.log 2>&1
echo "[$(date '+%Y-%m-%d %H:%M')] one-off: monday and thursday done (exit $?)" >> "$LOG"
