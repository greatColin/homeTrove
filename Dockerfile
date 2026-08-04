# syntax=docker/dockerfile:1

# ---------- build stage: backend ----------
FROM python:3.12-slim AS base
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# ---------- frontend build ----------
FROM node:20-alpine AS frontend-build
WORKDIR /web
COPY web/package.json web/package-lock.json* ./
# Note: keep install lean; M0 has no real npm deps yet.
COPY web/ ./
RUN npm install --no-audit --no-fund || true

# ---------- runtime ----------
FROM base AS runtime
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml alembic.ini ./
COPY alembic ./alembic
COPY hometrove ./hometrove

RUN pip install --no-cache-dir -e . \
    && pip install --no-cache-dir uv

# Copy static frontend build if it exists (M1 will wire a real build).
COPY --from=frontend-build /web/dist /app/web/dist

EXPOSE 8080

ENV HOMETROVE_DATA_DIR=/data
ENV HOMETROVE_MEDIA_ROOTS=/media

# Default entrypoint: API only. The worker is launched separately via
# ``hometrove worker`` (see docker-compose).
ENTRYPOINT ["hometrove", "api"]
