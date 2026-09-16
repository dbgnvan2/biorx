# BioRx web app.
#
# Deliberately does NOT install requirements.txt: that pulls PyQt6, the desktop
# GUI, which a headless container has no use for.

FROM python:3.12-slim

# pdfplumber needs no system packages; curl is here only for the healthcheck.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependencies first, so a source change does not reinstall them.
COPY requirements-web.txt .
RUN pip install --no-cache-dir -r requirements-web.txt

COPY src/ ./src/
COPY web/ ./web/
COPY agents/ ./agents/
COPY llm_config.yaml sources_config.yaml filters.json ./

# The database and any downloaded PDFs live on the mounted volume, never in the
# image layer — a container filesystem does not survive a redeploy.
ENV DATA_DIR=/data \
    PYTHONUNBUFFERED=1 \
    PORT=8000
RUN mkdir -p /data

# Run as a non-root user, and make the volume writable by it.
RUN useradd --create-home --uid 10001 biorx && chown -R biorx /data /app
USER biorx

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fsS "http://127.0.0.1:${PORT}/healthz" || exit 1

# $PORT is set by most platforms; the default matches EXPOSE for a local run.
CMD ["sh", "-c", "exec uvicorn web.app:app --host 0.0.0.0 --port ${PORT:-8000}"]
