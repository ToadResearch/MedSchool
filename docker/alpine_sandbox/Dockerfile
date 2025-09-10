FROM alpine:3.20

# ---- base OS deps ----
RUN set -eux; \
    apk add --no-cache python3 bash curl ca-certificates jq; \
    # Build deps only if wheels aren’t available; safe to keep for numpy/pandas/scipy but removed later
    apk add --no-cache --virtual .build-deps build-base python3-dev; \
    ln -sf python3 /usr/bin/python

# ---- install uv (standalone) ----
ENV UV_INSTALL_DIR=/usr/local/bin
RUN set -eux; \
    curl -LsSf https://astral.sh/uv/install.sh | sh

# ---- copy requirements early for layer caching ----
COPY requirements.txt /tmp/requirements.txt

# ---- create global venv and install deps with uv (no cache) ----
ENV PATH="/opt/venv/bin:${PATH}"
RUN set -eux; \
    python3 -m venv /opt/venv; \
    # Disable uv cache so it doesn't inflate the final image
    UV_NO_CACHE=1 /usr/local/bin/uv pip install \
      --python /opt/venv/bin/python \
      -r /tmp/requirements.txt; \
    rm -f /tmp/requirements.txt; \
    apk del .build-deps

# ---- non-root user "sandbox" ----
RUN adduser -D -s /bin/bash sandbox && \
    mkdir -p /home/sandbox && \
    chown -R sandbox:sandbox /home/sandbox

WORKDIR /home/sandbox

# default shell and user
ENV SHELL="/bin/bash"
USER sandbox

CMD ["/bin/bash"]