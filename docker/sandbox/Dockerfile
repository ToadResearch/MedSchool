# docker/sandbox/Dockerfile
FROM python:3.11-slim

WORKDIR /app

# --- Docker CLI (client only) for launching ephemeral runs ---
RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates curl gnupg jq bash \
  && install -m 0755 -d /etc/apt/keyrings \
  && curl -fsSL https://download.docker.com/linux/debian/gpg \
       | gpg --dearmor -o /etc/apt/keyrings/docker.gpg \
  && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
       https://download.docker.com/linux/debian $(. /etc/os-release; echo $VERSION_CODENAME) stable" \
       > /etc/apt/sources.list.d/docker.list \
  && apt-get update && apt-get install -y --no-install-recommends docker-ce-cli \
  && rm -rf /var/lib/apt/lists/*

# --- Install uv (Astral) and put it on PATH ---
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
# uv installs to ~/.local/bin on Linux
ENV PATH="/root/.local/bin:${PATH}"
# sanity check
RUN uv --version

# --- Python deps via uv (system install, no venv) ---
# RUN uv pip install --system --no-cache-dir fastapi uvicorn


# --- Python deps via uv (system install, no venv) ---
COPY requirements.txt /app/requirements.txt
RUN uv pip install --system --no-cache-dir -r /app/requirements.txt

# headless plotting + safe caches (read-only rootfs in runtime containers)
ENV MPLBACKEND=Agg \
    MPLCONFIGDIR=/tmp \
    XDG_CACHE_HOME=/tmp

# App
COPY execd.py /app/execd.py

ENV PYTHONUNBUFFERED=1
EXPOSE 8088

CMD ["uvicorn", "execd:app", "--host", "0.0.0.0", "--port", "8088"]
