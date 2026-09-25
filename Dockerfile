FROM python:3.11-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr tesseract-ocr-chi-sim tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*
ARG PIP_INDEX_URL=https://pypi.org/simple
ARG PIP_DEFAULT_TIMEOUT=120
ARG PIP_RETRIES=10
WORKDIR /app
COPY pyproject.toml ./
RUN mkdir -p backend && touch backend/__init__.py
RUN --mount=type=cache,target=/root/.cache/pip,sharing=locked \
    python -m pip install --disable-pip-version-check .
COPY backend backend
COPY alembic alembic
COPY alembic.ini ./
COPY knowledge knowledge
COPY evaluations evaluations
RUN groupadd --system app && useradd --system --gid app --home-dir /app --no-create-home app
USER app
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
