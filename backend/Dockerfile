# Optional containerized backend (Fly.io / VPS, or Render with runtime: docker).
# Render's native Python runtime (render.yaml) does NOT need this file.
FROM python:3.12-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# Install deps first for layer caching.
COPY requirements.txt .
RUN pip install -r requirements.txt
# For PostgreSQL instead of SQLite, also: RUN pip install "asyncpg>=0.29"

COPY . .

EXPOSE 8000

# Migrate, then serve. Single worker (in-memory rate limiter + SQLite single-writer).
# --proxy-headers so request.client.host is the real client IP behind the platform proxy.
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1 --proxy-headers --forwarded-allow-ips=*"]
