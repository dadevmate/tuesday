#!/bin/bash
# One-off (12 Oct 2026): after Monday and Thursday, retrain Tuesday again, now with multi-turn conversations (Friday gets them at the weekly retrain).
cd "$(dirname "$0")/.."
LOG=data/logs/auto-retrain.log
while pgrep -f "retrain-2026-10-12(\.sh|-b\.sh)" >/dev/null || pgrep -f "auto_retrain.py --force" >/dev/null; do sleep 30; done
sleep 30
echo "[$(date '+%Y-%m-%d %H:%M')] one-off: retraining tuesday with multi-turn conversations" >> "$LOG"
caffeinate -i -s .venv/bin/python train/auto_retrain.py --force --minds tuesday >> data/logs/train-2026-10-12.log 2>&1
echo "[$(date '+%Y-%m-%d %H:%M')] one-off: tuesday (multi-turn) done (exit $?)" >> "$LOG"
