FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir fastapi uvicorn jinja2 httpx python-multipart

COPY prism/ ./prism/

ENV PRISM_DB_PATH=/data/prism.db
ENV PYTHONUNBUFFERED=1

EXPOSE 8080

CMD ["python", "-m", "uvicorn", "prism.app:app", "--host", "0.0.0.0", "--port", "8080"]
