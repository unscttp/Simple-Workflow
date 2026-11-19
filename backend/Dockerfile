# Build from the repository root: docker build -f backend/Dockerfile .
FROM python:3.11-slim

WORKDIR /app

COPY backend/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

# The NLG workflow framework (git submodule) and the application.
COPY third_party/nlg /app/third_party/nlg
COPY backend /app/backend

ENV PYTHONPATH=/app/third_party/nlg \
    SW_DATA_DIR=/data \
    SW_REPORTS_ROOT=/data/reports

WORKDIR /app/backend
EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
