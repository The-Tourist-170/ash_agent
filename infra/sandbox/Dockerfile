# Sandbox image for Phase 4 reproduction.
#
# Build:
#   docker build -t ash-agent-sandbox:py312 infra/sandbox
#
# The container runs as the host UID/GID at runtime; no secrets are baked in.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_PYTHON=3.12 \
    UV_PYTHON_DOWNLOADS=never

RUN pip install --no-cache-dir uv pytest

WORKDIR /workspace
