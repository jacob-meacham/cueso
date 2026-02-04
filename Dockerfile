# Stage 1: Build frontend
FROM node:22-slim AS frontend-build
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ .
RUN npm run build

# Stage 2: Backend + built frontend
FROM python:3.13-slim
WORKDIR /app

RUN apt-get update && apt-get install -y \
    curl \
    && rm -rf /var/lib/apt/lists/*

RUN pip install uv

COPY backend/pyproject.toml backend/uv.lock ./
RUN touch README.md && uv sync --frozen --no-dev

COPY backend/ .
COPY --from=frontend-build /app/dist /app/static

RUN mkdir -p /app/data

EXPOSE 8484

HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8484/health || exit 1

CMD ["uv", "run", "python", "main.py"]
