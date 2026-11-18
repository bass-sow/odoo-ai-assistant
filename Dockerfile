FROM python:3.11-slim

WORKDIR /srv
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

COPY pyproject.toml README.md ./
COPY app ./app
COPY evals ./evals
RUN pip install --no-cache-dir ".[pgvector]"

COPY data/docs ./data/docs
COPY data/samples ./data/samples

RUN useradd --create-home appuser && chown -R appuser /srv
USER appuser

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
