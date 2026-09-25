FROM python:3.13-slim AS runtime

WORKDIR /workspace

# Install uv for fast package installation
COPY --from=ghcr.io/astral-sh/uv:0.6.0 /uv /uvx /bin/

# Copy workspace specifications and packages
COPY pyproject.toml uv.lock ./
COPY core/ ./core/
COPY api/ ./api/
COPY worker/ ./worker/
COPY config/ ./config/

RUN uv sync --frozen --no-dev --package intralink-api

EXPOSE 8000

CMD ["uv", "run", "--package", "intralink-api", "uvicorn", "api.src.main:app", "--host", "0.0.0.0", "--port", "8000"]
