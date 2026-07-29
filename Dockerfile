FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpango-1.0-0 libpangocairo-1.0-0 libgdk-pixbuf-2.0-0 \
    libcairo2 libffi8 shared-mime-info fonts-wqy-microhei \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir fastapi uvicorn jinja2 httpx python-multipart weasyprint fastembed

COPY prism/ ./prism/

ENV PRISM_DB_PATH=/data/prism.db
ENV PYTHONUNBUFFERED=1

EXPOSE 8080

CMD ["python", "-m", "uvicorn", "prism.app:app", "--host", "0.0.0.0", "--port", "8080"]
