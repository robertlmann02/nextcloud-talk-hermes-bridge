FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1     PYTHONUNBUFFERED=1     TALK_BRIDGE_BIND=0.0.0.0     TALK_BRIDGE_PORT=8788     HERMES_PROFILE=default     HERMES_HOME_DIR=/app/hermes-home     TALK_CONTEXT_DIR=/nc_app_hermes_talk_bridge_data/context     TALK_BRIDGE_LOG=/nc_app_hermes_talk_bridge_data/bridge.log

RUN apt-get update     && apt-get install -y --no-install-recommends curl ca-certificates git ffmpeg     && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY nextcloud_talk_hermes_bridge ./nextcloud_talk_hermes_bridge
RUN python -m pip install --no-cache-dir --upgrade pip     && python -m pip install --no-cache-dir . hermes-agent

RUN mkdir -p /app/hermes-home /nc_app_hermes_talk_bridge_data
EXPOSE 8788
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3   CMD python -c "import os, urllib.request; port=os.environ.get('APP_PORT') or os.environ.get('TALK_BRIDGE_PORT','8788'); urllib.request.urlopen(f'http://127.0.0.1:{port}/heartbeat', timeout=3).read()"

ENTRYPOINT ["python", "-m", "nextcloud_talk_hermes_bridge.bridge"]
