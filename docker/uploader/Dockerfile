FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl unzip ca-certificates && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY upload_synthea.py run_uploader.sh ./

ENV PYTHONUNBUFFERED=1
ENTRYPOINT ["/app/run_uploader.sh"]
