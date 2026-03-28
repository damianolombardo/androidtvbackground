#!/bin/sh

# Load CRON_SCHEDULE from .env using python-dotenv so cron expressions with
# spaces and wildcards are handled correctly (plain shell sourcing breaks them).
ENV_FILE="${DOTENV_PATH:-/app/.env}"
if [ -f "$ENV_FILE" ]; then
    _cron=$(python3 - "$ENV_FILE" <<'EOF'
import sys
from dotenv import dotenv_values
v = dotenv_values(sys.argv[1])
s = v.get("CRON_SCHEDULE", "")
if s:
    print(s)
EOF
)
    if [ -n "$_cron" ]; then
        CRON_SCHEDULE="$_cron"
    fi
fi

LOG_DIR="${LOG_DIR:-/config/logs}"
export LOG_DIR
mkdir -p "$LOG_DIR"

BACKGROUNDS_BASE_DIR="${BACKGROUNDS_BASE_DIR:-/backgrounds}"
export BACKGROUNDS_BASE_DIR

CRON_SCHEDULE="${CRON_SCHEDULE:-0 * * * *}"

# ── 1. Run immediately at container startup ───────────────────────────────────
echo "==> [entrypoint] Initial run at $(date)"
python /app/androidtvbackground/main.py || echo "==> [entrypoint] Initial run failed (exit $?), continuing to schedule"

# ── 2. Loop: use croniter to sleep until the next scheduled time ──────────────
echo "==> [entrypoint] Cron schedule: ${CRON_SCHEDULE}"
while true; do
    sleep_secs=$(python3 -c "
import math, sys
from datetime import datetime
try:
    from croniter import croniter
    c = croniter('$CRON_SCHEDULE', datetime.now())
    secs = (c.get_next(datetime) - datetime.now()).total_seconds()
    print(max(1, math.ceil(secs)))
except Exception as e:
    print('ERROR: ' + str(e), file=sys.stderr)
    sys.exit(1)
")
    echo "==> [scheduler] Next run in ${sleep_secs}s"
    sleep "$sleep_secs"
    echo "==> [cron] Run at $(date)"
    python /app/androidtvbackground/main.py || echo "==> [cron] Run failed (exit $?)"
done
