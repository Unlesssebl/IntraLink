FROM python:3.13-slim AS runtime

WORKDIR /workspace

# Install uv for fast package installation
COPY --from=ghcr.io/astral-sh/uv:0.6.0 /uv /uvx /bin/

# Copy workspace specifications
COPY pyproject.toml uv.lock ./
COPY core/ ./core/
COPY worker/ ./worker/

RUN uv sync --frozen --no-dev --package intralink-worker

CMD ["uv", "run", "--package", "intralink-worker", "python", "-m", "worker.src.main"]
