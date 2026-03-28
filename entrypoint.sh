#!/bin/sh

CRON_SCHEDULE="${CRON_SCHEDULE:-0 * * * *}"

# ── 1. Run immediately at container startup ───────────────────────────────────
echo "==> [entrypoint] Initial run at $(date)"
python /app/androidtvbackground/main.py || echo "==> [entrypoint] Initial run failed (exit $?), continuing to schedule cron"

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
printf '%s root . /tmp/env.sh && echo "==> [cron] Run at $(date)" && python /app/androidtvbackground/main.py >> /proc/1/fd/1 2>> /proc/1/fd/2\n' \
    "$CRON_SCHEDULE" > /etc/cron.d/bg-generator
chmod 0644 /etc/cron.d/bg-generator

echo "==> [entrypoint] Cron schedule: ${CRON_SCHEDULE}"
exec cron -f
