FROM python:3.9

WORKDIR /app

# 构建上下文应为仓库根目录：docker build -f api/Dockerfile .
COPY api/requirements.txt ./api/requirements.txt
RUN pip install --no-cache-dir -r api/requirements.txt

COPY api ./api
COPY agentic_rag ./agentic_rag
COPY cppt ./cppt

ENV PYTHONPATH=/app

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
