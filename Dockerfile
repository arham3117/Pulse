# syntax=docker/dockerfile:1
# One image for every Pulse process; the container command picks which one runs
# (pulse-api, pulse-scheduler, pulse-worker, pulse-target, pulse-maintenance, alembic).
# Base images are pinned by digest; Dependabot (docker ecosystem) proposes updates.

ARG PYTHON_IMAGE=python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b

# ---- builder: resolve and install dependencies into /app/.venv with uv ----------------
FROM ${PYTHON_IMAGE} AS builder
COPY --from=ghcr.io/astral-sh/uv:0.13.9@sha256:d3b601f0c7b118bc1753febf7c41cc80981df678445e0254284eecf84db79a7d /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv
WORKDIR /src
# Dependencies first (cached layer), then the project itself.
COPY app/pyproject.toml app/uv.lock app/.python-version app/README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project
COPY app/src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable

# ---- runtime: no build tools, non-root, read-only-root-filesystem friendly ------------
FROM ${PYTHON_IMAGE} AS runtime
LABEL org.opencontainers.image.title="pulse" \
      org.opencontainers.image.description="Pulse uptime monitoring (api, scheduler, worker, target, maintenance)" \
      org.opencontainers.image.source="https://github.com/YOUR_GITHUB_USER/pulse"
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:${PATH}"
RUN groupadd --system --gid 10001 pulse \
 && useradd --system --uid 10001 --gid 10001 --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin pulse
WORKDIR /app
COPY --from=builder --chown=root:root /app/.venv /app/.venv
COPY --chown=root:root app/alembic.ini ./alembic.ini
COPY --chown=root:root app/migrations ./migrations
# Files are owned by root and not writable by the app user; only /tmp needs to be writable.
USER 10001:10001
EXPOSE 8000 8080 9100
CMD ["pulse-api"]
