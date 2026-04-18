FROM python:3.11-slim

# HiGHS/CVXPY requièrent libgomp (OpenMP) + BLAS/LAPACK
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgomp1 \
        libblas3 \
        liblapack3 \
    && rm -rf /var/lib/apt/lists/*

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Copie des sources et du package
COPY pyproject.toml ./
COPY src/ ./src/

# Install des dépendances (pas les extras dev)
RUN uv pip install --system --no-cache .

# Fichiers de configuration et migrations
COPY config.yaml ./
COPY alembic.ini ./
COPY alembic/ ./alembic/

# Utilisateur non-root
RUN useradd -u 1000 -m optimizer \
    && chown -R optimizer:optimizer /app
USER 1000:1000

EXPOSE 8080

CMD ["uvicorn", "optimizer.main:app", "--host", "0.0.0.0", "--port", "8080"]
