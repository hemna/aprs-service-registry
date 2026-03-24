# APRS Service Registry — production image
# Python 3.11+ to match requires-python in pyproject.toml
FROM python:3.11-slim-bookworm AS builder

WORKDIR /build

# Install build dependencies if any (e.g. for wheels)
# git is required to install aprsd from GitHub
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    git \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml .
COPY aprs_service_registry/ aprs_service_registry/

# Install the application and its dependencies (from pyproject.toml).
# main.py imports aprsd (not in pyproject.toml); install it for the container to run.
RUN pip install --no-cache-dir --no-warn-script-location .

# -----------------------------------------------------------------------------
FROM python:3.11-slim-bookworm

# Install tini as init (reaps zombies, forwards signals)
RUN apt-get update && apt-get install -y --no-install-recommends \
    tini \
    wget \
    && rm -rf /var/lib/apt/lists/*

# Non-root user
RUN useradd -r -m -s /bin/false app

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin/aprs-service-registry /usr/local/bin/aprs-service-registry

# Default config: bind to all interfaces, port 80
RUN mkdir -p /app/config \
    && printf '%s\n' '[registry]' 'web_ip = 0.0.0.0' 'web_port = 80' > /app/config/registry.conf \
    && chown -R app:app /app

USER app

EXPOSE 80

# Healthcheck: HTTP GET /
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD wget -q -O- http://127.0.0.1:80/ || exit 1

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["aprs-service-registry", "server", "-c", "/app/config/registry.conf"]
