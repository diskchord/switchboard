FROM python:3.12-slim

ARG SWITCHBOARD_UID=10001
ARG SWITCHBOARD_GID=10001
ARG DEBIAN_FRONTEND=noninteractive

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TEXTING_HOST=0.0.0.0 \
    TEXTING_PORT=8766 \
    TEXTING_DATA_DIR=/data \
    TEXTING_DB=/data/switchboard.sqlite \
    TEXTING_MEDIA_DIR=/data/media \
    TEXTING_PUBLIC_UPLOAD_DIR=/data/public-uploads

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        ffmpeg \
        gosu \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid "${SWITCHBOARD_GID}" switchboard \
    && useradd --uid "${SWITCHBOARD_UID}" --gid switchboard --home-dir /app --shell /usr/sbin/nologin --no-create-home switchboard

WORKDIR /app

COPY requirements.txt ./
RUN python -m pip install --no-cache-dir --root-user-action=ignore -r requirements.txt

COPY server.py ./
COPY texting_app ./texting_app
COPY static ./static
COPY docker/entrypoint.sh /usr/local/bin/switchboard-entrypoint

RUN chmod +x /usr/local/bin/switchboard-entrypoint \
    && mkdir -p /data \
    && chown -R switchboard:switchboard /app /data

VOLUME ["/data"]
EXPOSE 8766

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"TEXTING_PORT\", \"8766\")}/api/health', timeout=3).read()"

ENTRYPOINT ["switchboard-entrypoint"]
CMD ["python", "server.py"]
