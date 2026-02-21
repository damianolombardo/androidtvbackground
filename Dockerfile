FROM python:3.11-slim

WORKDIR /app

# Install cron
RUN apt-get update && apt-get install -y --no-install-recommends cron \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies first for layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source
COPY *.py ./
COPY *.ttf ./
COPY *.png ./
COPY *.svg ./
COPY config/ config/
COPY entrypoint.sh .
RUN chmod +x entrypoint.sh

# Create output and log directories
RUN mkdir -p backgrounds logs

VOLUME ["/app/backgrounds", "/app/logs"]

# CRON_SCHEDULE controls how often main.py runs after the initial startup run.
# Default: every hour on the hour. Override with -e CRON_SCHEDULE="*/30 * * * *" etc.
ENV CRON_SCHEDULE="0 * * * *"

ENTRYPOINT ["./entrypoint.sh"]
