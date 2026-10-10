# Perfect Engine backend — single requirements file, no compiler toolchain.
ARG PYTHON_VERSION=3.11-slim
FROM python:${PYTHON_VERSION}

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
  && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

# Fail-closed production boot is enforced by app/config.py
# (AUTH_ENABLED, JWT_SECRET_KEY, REDIS_URL, ALLOWED_ORIGINS).
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
