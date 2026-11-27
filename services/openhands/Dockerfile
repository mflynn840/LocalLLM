FROM ghcr.io/openhands/agent-canvas:1.43.0
USER root
RUN apt-get update && apt-get install -y --no-install-recommends ripgrep && rm -r /var/lib/apt/lists/*
COPY static-server.mjs /opt/agent-canvas/static-server.mjs
COPY execution-mode.mjs /opt/agent-canvas/execution-mode.mjs
COPY model-manager.mjs /opt/agent-canvas/model-manager.mjs
COPY host-terminal.py /opt/agent-canvas/host-terminal.py
COPY jetson-mcp.py /opt/agent-canvas/jetson-mcp.py
COPY install-execution-mode.py /tmp/install-execution-mode.py
COPY local_execution.py /tmp/local_execution.py
COPY frontend/ /tmp/frontend/
RUN python /tmp/install-execution-mode.py && \
    printf '%s\n' '#!/bin/sh' 'exec python /opt/agent-canvas/host-terminal.py client "$@"' > /usr/local/bin/openhands-host-terminal && \
    chmod 0755 /usr/local/bin/openhands-host-terminal /opt/agent-canvas/host-terminal.py && \
    rm -r /tmp/install-execution-mode.py /tmp/local_execution.py /tmp/frontend
