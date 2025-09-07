FROM python:3.11-slim

# System deps (jq optional). Add build tools so pip can compile if needed.
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
      jq wget unzip build-essential gcc libc6-dev libffi-dev && \
    rm -rf /var/lib/apt/lists/*

# Make Python unbuffered so you see logs immediately
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DEFAULT_TIMEOUT=100

WORKDIR /app

# Layer caching: install deps first
COPY requirements.txt ./
RUN python -m pip install --upgrade pip && \
    pip install --prefer-binary -r requirements.txt

# Now copy the app
COPY bulk_uploader.py /app/bulk_uploader.py

# -u ensures unbuffered output; redundant with env but safe
CMD ["python", "-u", "bulk_uploader.py"]