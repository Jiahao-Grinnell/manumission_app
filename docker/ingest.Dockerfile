FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

WORKDIR /app

RUN groupadd --gid 10001 appuser \
    && useradd --uid 10001 --gid 10001 --create-home --home-dir /home/appuser appuser

COPY requirements/base.txt /tmp/base.txt
COPY requirements/ingest.txt /tmp/requirements-ingest.txt
RUN pip install --no-cache-dir -r /tmp/requirements-ingest.txt

COPY src/shared /app/shared
COPY src/modules/__init__.py /app/modules/__init__.py
COPY src/modules/pdf_ingest /app/modules/pdf_ingest

USER 10001:10001
