#  ___                        _  ____      _
# |  _ \ ___  _   _ _ __   __| |/ ___|__ _| | _____
# | |_) / _ \| | | | '_ \ / _` | |   / _` | |/ / _ \
# |  __/ (_) | |_| | | | | (_| | |__| (_| |   <  __/
# |_|   \___/ \__,_|_| |_|\__,_|\____\__,_|_|\_\___|
#

# ============================================================================
# Stage 1: Shared Python builder base
# ============================================================================
FROM python:3.11-slim AS python-builder-base

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    default-libmysqlclient-dev \
    pkg-config \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
RUN pip install --no-cache-dir --upgrade pip setuptools wheel

# API dependency set
FROM python-builder-base AS api-deps
COPY requirements.txt /build/requirements.txt
RUN pip install --no-cache-dir -r /build/requirements.txt

# ============================================================================
# Stage 2: Shared runtime base
# ============================================================================
FROM python:3.11-slim AS python-runtime-base

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    git \
    && rm -rf /var/lib/apt/lists/*

RUN useradd -m -u 1000 appuser

ENV PYTHONPATH=/app \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    POUNDCAKE_ENABLED_PLUGINS=dummy \
    POUNDCAKE_AUTH_ENABLED=false \
    POUNDCAKE_APP_VERSION=1.0.0 \
    DISHWASHER_INTERVAL=30 \
    EXECUTION_RUNNER_INTERVAL=2

# ============================================================================
# Stage 3A: API runtime image
# ============================================================================
FROM python-runtime-base AS api-runtime

COPY --from=api-deps /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY --chown=appuser:appuser api/ /app/api/
COPY --chown=appuser:appuser shared/ /app/shared/
COPY --chown=appuser:appuser kitchen/ /app/kitchen/
COPY --chown=appuser:appuser config/ /app/config/

USER appuser

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
