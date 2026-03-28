FROM python:3.11-slim

WORKDIR /app

# Install Python dependencies first for layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source
COPY *.py ./
COPY *.ttf ./
COPY *.png ./
COPY *.svg ./
COPY androidtvbackground/ androidtvbackground/
COPY config/ config/
COPY entrypoint.sh .
RUN chmod +x entrypoint.sh

# Create fallback output directory (used when BACKGROUNDS_BASE_DIR is not set)
RUN mkdir -p backgrounds && chmod 777 backgrounds

# Cron schedule for recurring runs. Default: top of every hour.
ENV CRON_SCHEDULE="0 * * * *"

ENTRYPOINT ["./entrypoint.sh"]
