FROM node:22-alpine AS frontend
WORKDIR /build/frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim AS app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY src/ ./src/
COPY --from=frontend /build/frontend/dist/ ./frontend/dist/
RUN pip install --no-cache-dir uv && uv sync --frozen --no-dev
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/openapi.json', timeout=4)"
CMD ["/app/.venv/bin/surebet-web", "serve", "--db", "/data/surebet.db", "--host", "0.0.0.0", "--port", "8000"]
