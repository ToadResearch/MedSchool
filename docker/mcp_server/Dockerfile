FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .

# no curl or wget... so use pip :)
RUN pip install --no-cache-dir uv \
    && uv pip install --system --no-cache-dir -r requirements.txt

# Ship the whole package (tools, utils, config, main, tools.yaml)
COPY . /app/mcp_server
ENV PYTHONPATH=/app

# Don't buffer stdout for realtime logs
ENV PYTHONUNBUFFERED=1
EXPOSE 8000

# Defaults
ENV MCP_HOST=${MCP_HOST}
ENV MCP_PORT=${MCP_PORT}
ENV MCP_HTTP_PATH=${MCP_HTTP_PATH}

# -m used to run the server.py as a module so that relative imports work
CMD ["python", "-u", "-m", "mcp_server.server"]