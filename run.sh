#!/usr/bin/env bash
# Entry point for the scheduler: loads .env (email credentials) and runs one scrape.
set -uo pipefail
cd "$(dirname "$0")"
if [ -f .env ]; then
    set -a
    . ./.env
    set +a
fi
mkdir -p logs
# One run at a time: Chrome can't share the profile between two processes.
exec 9>logs/.lock
if ! flock -n 9; then
    echo "$(date '+%Y-%m-%d %H:%M:%S') another run is in progress; skipping" >> logs/run.log
    exit 0
fi
echo "===== $(date '+%Y-%m-%d %H:%M:%S') =====" >> logs/run.log
# Hard limit so a run frozen by laptop sleep can't hold the profile forever.
PYTHONUNBUFFERED=1 timeout --kill-after=60 45m .venv/bin/python main.py "$@" >> logs/run.log 2>&1
status=$?
if [ "$status" -eq 124 ] || [ "$status" -eq 137 ]; then
    echo "$(date '+%Y-%m-%d %H:%M:%S') run timed out after 45 min" >> logs/run.log
    pkill -f -- "--user-data-dir=$HOME/.config/linkedin-posts-profile" || true
    .venv/bin/python -c "from notify import send_alert_email; send_alert_email('Run timed out', 'A run took over 45 minutes and was stopped (laptop asleep mid-run?). The next scheduled run will retry. Log: logs/run.log')" >> logs/run.log 2>&1 || true
fi
exit "$status"
