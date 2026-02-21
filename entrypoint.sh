#!/bin/sh
set -e

CRON_SCHEDULE="${CRON_SCHEDULE:-0 * * * *}"

# ── 1. Run immediately at container startup ───────────────────────────────────
echo "==> [entrypoint] Initial run at $(date)"
python /app/androidtvbackground/main.py

# ── 2. Persist the container environment for cron ────────────────────────────
# cron spawns a minimal shell that does not inherit Docker env vars, so we
# dump them to a file the cron command will source before executing.
# Using Python shlex.quote for safe quoting of all values.
python3 -c "
import os, shlex
lines = []
for k, v in os.environ.items():
    lines.append('export {}={}'.format(k, shlex.quote(v)))
print('\n'.join(lines))
" > /tmp/env.sh

# ── 3. Install the cron job ───────────────────────────────────────────────────
mkdir -p /app/logs
printf '%s root . /tmp/env.sh && python /app/androidtvbackground/main.py >> /app/logs/cron.log 2>&1\n' \
    "$CRON_SCHEDULE" > /etc/cron.d/bg-generator
chmod 0644 /etc/cron.d/bg-generator

# ── 4. Truncate cron log if it exceeds 10 MB ─────────────────────────────────
python3 -c "
import os
log = '/app/logs/cron.log'
try:
    if os.path.getsize(log) > 10 * 1024 * 1024:
        open(log, 'w').close()
        print('[entrypoint] cron.log truncated (>10 MB)')
except FileNotFoundError:
    pass
"

echo "==> [entrypoint] Cron schedule: ${CRON_SCHEDULE}"
exec cron -f
