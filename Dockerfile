# --- Stage 1: build the React front end ---
FROM node:22-alpine AS frontend
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# --- Stage 2: Python app (used by both the api and worker containers) ---
FROM python:3.12-slim
ARG APP_VERSION=0.0.0-dev
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH" \
    APP_VERSION=$APP_VERSION
COPY --from=ghcr.io/astral-sh/uv:0.12 /uv /usr/local/bin/uv

WORKDIR /app
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev
# Download the OCR models at build time so scanned documents work without internet access.
RUN python -c "from rapidocr import RapidOCR; RapidOCR()"

COPY backend/ ./
COPY --from=frontend /frontend/dist ./static

RUN useradd --create-home --uid 1000 app \
    && mkdir -p /data/uploads /data/updates \
    && chown -R app:app /data
USER app

EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers"]
