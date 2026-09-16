# BioRx web app.
#
# Deliberately does NOT install requirements.txt: that pulls the desktop GUI
# toolkit, which a headless container has no use for.

FROM python:3.12-slim

# gosu drops privileges in the entrypoint; curl is for the healthcheck.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl gosu \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependencies first, so a source change does not reinstall them.
COPY requirements-web.txt .
RUN pip install --no-cache-dir -r requirements-web.txt

COPY src/ ./src/
COPY web/ ./web/
COPY agents/ ./agents/
COPY llm_config.yaml sources_config.yaml filters.json ./
COPY docker-entrypoint.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

# The database and any downloaded PDFs live on the mounted volume, never in an
# image layer — a container filesystem does not survive a redeploy.
ENV DATA_DIR=/data \
    PYTHONUNBUFFERED=1 \
    PORT=8000
RUN mkdir -p /data

RUN useradd --create-home --uid 10001 biorx && chown -R biorx /app /data

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fsS "http://127.0.0.1:${PORT}/healthz" || exit 1

# The entrypoint starts as root only long enough to make the mounted volume
# writable, then drops to the unprivileged user. A chown at build time cannot
# do this: mounting a volume at /data replaces that directory, ownership and
# all, so the build-time chown is simply not there at runtime.
ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["sh", "-c", "exec uvicorn web.app:app --host 0.0.0.0 --port ${PORT:-8000}"]
