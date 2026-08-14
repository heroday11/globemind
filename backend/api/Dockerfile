FROM python:3.11-slim-bookworm

WORKDIR /app

# Build from backend/, not the repository root:
#   docker build -f api/Dockerfile .
# The backend/.dockerignore allow-list keeps credentials and mutable artifacts
# out of both the build context and the resulting image.
COPY api/requirements.txt /tmp/api-requirements.txt
RUN python -m pip install --no-cache-dir --requirement /tmp/api-requirements.txt \
    && rm -f /tmp/api-requirements.txt

COPY api ./api
COPY agentic_rag ./agentic_rag
COPY cppt ./cppt
COPY runtime_control ./runtime_control
COPY cc_integration.py ./cc_integration.py

ENV PYTHONPATH=/app

# The API does not need root privileges at runtime.
RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin appuser \
    && chown -R appuser:appuser /app
USER appuser

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
