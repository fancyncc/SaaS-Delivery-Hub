FROM python:3.11-slim
WORKDIR /app
COPY pyproject.toml ./
RUN mkdir -p backend && touch backend/__init__.py
RUN pip install --no-cache-dir .
COPY backend backend
COPY alembic alembic
COPY alembic.ini ./
COPY knowledge knowledge
COPY evaluations evaluations
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
