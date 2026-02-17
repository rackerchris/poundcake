#  ___                        _  ____      _
# |  _ \ ___  _   _ _ __   __| |/ ___|__ _| | _____
# | |_) / _ \| | | | '_ \ / _` | |   / _` | |/ / _ \
# |  __/ (_) | |_| | | | | (_| | |__| (_| |   <  __/
# |_|   \___/ \__,_|_| |_|\__,_|\____\__,_|_|\_\___|
#

# ============================================================================
# Stage 1: Builder - Install dependencies in a virtual environment
# ============================================================================
FROM python:3.11-slim AS builder

WORKDIR /build

# Install build dependencies (needed for compiling Python packages)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    default-libmysqlclient-dev \
    pkg-config \
    && rm -rf /var/lib/apt/lists/*

# Create virtual environment
RUN python -m venv /opt/venv

# Activate venv and upgrade pip
ENV PATH="/opt/venv/bin:$PATH"
RUN pip install --no-cache-dir --upgrade pip setuptools wheel

# Install Python dependencies into the venv
COPY requirements-prod.txt .
RUN pip install --no-cache-dir -r requirements-prod.txt

# ============================================================================
# Stage 2: Runtime - Minimal production image
# ============================================================================
FROM python:3.11-slim

WORKDIR /app

# Install only runtime dependencies (no build tools)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN useradd -m -u 1000 appuser

# Copy the virtual environment from builder
COPY --from=builder /opt/venv /opt/venv

# Copy only the Python application code (not tests, helm, docs, ui, etc.)
COPY --chown=appuser:appuser api/ /app/api/
COPY --chown=appuser:appuser kitchen/ /app/kitchen/
COPY --chown=appuser:appuser docker/scripts/ /app/scripts/

# Make scripts executable
RUN chmod +x /app/api/scripts/entrypoint-auto-migrate.sh \
    && chmod +x /app/scripts/automated-setup.sh

# Switch to non-root user
USER appuser

# Use the venv
ENV PATH="/opt/venv/bin:$PATH"
ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

# Default command (Uvicorn fallback)
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
