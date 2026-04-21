FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

WORKDIR /app

RUN groupadd --gid 10001 appuser \
    && useradd --uid 10001 --gid 10001 --create-home --home-dir /home/appuser appuser

COPY requirements/base.txt /tmp/requirements-base.txt
RUN pip install --no-cache-dir -r /tmp/requirements-base.txt

COPY config /app/config
COPY src/shared /app/shared
COPY src/modules/__init__.py /app/modules/__init__.py
COPY src/modules/normalizer /app/modules/normalizer
COPY src/modules/page_classifier /app/modules/page_classifier
COPY src/modules/name_extractor /app/modules/name_extractor
COPY src/modules/metadata_extractor /app/modules/metadata_extractor
COPY src/modules/place_extractor /app/modules/place_extractor

USER 10001:10001
