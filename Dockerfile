# Hardened Multi-Stage Dockerfile for PMGA Platform
# Principles: Non-root user, minimal attack surface (ISO 27001 A.8.2 / KRITIS)

FROM python:3.12-slim AS builder

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# Final Runtime Image
FROM python:3.12-slim AS runtime

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    APP_ENV=production \
    APP_HOST=0.0.0.0 \
    APP_PORT=8000

# Create non-privileged system user
RUN groupadd -g 1001 pmgagroup && \
    useradd -u 1001 -g pmgagroup -m -s /bin/bash pmgauser

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Copy application files
COPY . .

# Ensure storage directories exist with proper permissions
RUN mkdir -p storage/uploads storage/exports && \
    chown -R pmgauser:pmgagroup /app

USER pmgauser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health')" || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
