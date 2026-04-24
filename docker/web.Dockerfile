FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

WORKDIR /app

RUN groupadd --gid 10001 appuser \
    && useradd --uid 10001 --gid 10001 --create-home --home-dir /home/appuser appuser

COPY requirements /tmp/requirements
RUN pip install --no-cache-dir -r /tmp/requirements/base.txt \
    && pip install --no-cache-dir -r /tmp/requirements/ingest.txt \
    && pip install --no-cache-dir -r /tmp/requirements/ocr.txt

COPY config /app/config
COPY src/shared /app/shared
COPY src/modules /app/modules
COPY src/orchestrator /app/orchestrator

USER 10001:10001
