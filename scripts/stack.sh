#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNTIME_DIR="$ROOT/runtime"
PID_FILE="$RUNTIME_DIR/llama-server.pid"
MODE_FILE="$RUNTIME_DIR/active-agent"

usage() {
    cat <<'EOF'
Usage:
  scripts/stack.sh preload
  scripts/stack.sh up --backend metal|cuda --agent openhands|opencode --model ALIAS [--quant QUANT] [--context TOKENS] [--gpu-layers N] [--web-search|--no-web-search] [--sandbox-level restricted|read-only|read-write|full-control|project|networked]
  scripts/stack.sh status
  scripts/stack.sh down

Examples:
  scripts/stack.sh up --backend metal --agent openhands --model qwen3-coder-30b-a3b --quant Q3_K_M
  scripts/stack.sh up --backend cuda --agent opencode --model gemma-4-12b
  scripts/stack.sh up --backend cuda --agent openhands --model gemma-4-12b --sandbox-level networked
EOF
}

ensure_env() {
    if [[ ! -f "$ROOT/.env" ]]; then
        (umask 077 && cp "$ROOT/.env.example" "$ROOT/.env")
        echo "Created .env from .env.example."
    fi
    python3 - "$ROOT/.env" <<'PY'
import os
import secrets
import sys
from pathlib import Path

path = Path(sys.argv[1])
lines = path.read_text(encoding="utf-8").splitlines()
values = {
    line.split("=", 1)[0]: line.split("=", 1)[1]
    for line in lines
    if "=" in line and not line.lstrip().startswith("#")
}
placeholders = {
    "LITELLM_MASTER_KEY": {"sk-local-dev-change-me"},
    "SEARXNG_SECRET": {"generate-with-install"},
    "WEB_LOADER_API_KEY": {"generate-with-install"},
}
for name in (
    "LITELLM_MASTER_KEY", "LOCAL_BACKEND_API_KEY", "OPENHANDS_LAN_AUTH_PASSWORD",
    "LITELLM_DB_PASSWORD", "LITELLM_SALT_KEY", "MODEL_SIGNUP_CODE",
    "WEBUI_ADMIN_PASSWORD", "WEBUI_SECRET_KEY", "SEARXNG_SECRET", "WEB_LOADER_API_KEY",
):
    value = values.get(name, "").strip().strip("\"'")
    if value and value not in placeholders.get(name, {"generate-with-install"}):
        continue
    value = ("sk-" if name == "LITELLM_MASTER_KEY" else "") + secrets.token_urlsafe(32)
    prefix = name + "="
    indices = [index for index, line in enumerate(lines) if line.startswith(prefix)]
    if indices:
        lines[indices[-1]] = prefix + value
    else:
        lines.append(prefix + value)
for name, default in (("BACKEND_BIND_ADDRESS", "0.0.0.0"), ("OPENHANDS_BIND_ADDRESS", "127.0.0.1")):
    prefix = name + "="
    if not any(line.startswith(prefix) for line in lines):
        lines.append(prefix + default)
lines = [
    "OPENHANDS_BIND_ADDRESS=127.0.0.1" if line.startswith("OPENHANDS_BIND_ADDRESS=") else line
    for line in lines
]
path.write_text("\n".join(lines) + "\n", encoding="utf-8")
os.chmod(path, 0o600)
PY
}

stop_local_model() {
    [[ -f "$PID_FILE" ]] || return 0
    local pid command_line
    pid="$(<"$PID_FILE")"
    if [[ ! "$pid" =~ ^[0-9]+$ ]] || ! kill -0 "$pid" 2>/dev/null; then
        rm -f "$PID_FILE"
        return 0
    fi
    if ! command_line="$(ps -p "$pid" -o command= 2>/dev/null)"; then
        echo "Could not verify PID $pid; leaving it running and keeping its PID file." >&2
        return 1
    fi
    if [[ "$command_line" == *llama-server* ]]; then
        kill "$pid"
    else
        echo "Not stopping PID $pid: it is no longer the recorded llama-server process." >&2
    fi
    rm -f "$PID_FILE"
}

stop_cuda_model() {
    local running
    running="$(docker compose --env-file "$ROOT/.env" --profile cuda ps --status running --services)"
    if printf '%s\n' "$running" | grep -qx llama-server; then
        docker compose --env-file "$ROOT/.env" --profile cuda stop llama-server
    fi
}

check_agent_lock() {
    if [[ -d "$RUNTIME_DIR/opencode.lock" ]]; then
        local pid command_line
        if [[ -f "$RUNTIME_DIR/opencode.lock/pid" ]]; then
            pid="$(<"$RUNTIME_DIR/opencode.lock/pid")"
            if [[ "$pid" =~ ^[0-9]+$ ]] && kill -0 "$pid" 2>/dev/null; then
                if ! command_line="$(ps -p "$pid" -o command= 2>/dev/null)"; then
                    echo "Could not verify the OpenCode lock PID $pid; leaving the lock intact." >&2
                    exit 1
                fi
                if [[ "$command_line" == *scripts/code.sh* ]]; then
                    echo "OpenCode is currently running in this workspace. Wait for it to exit before switching agents." >&2
                    exit 1
                fi
            fi
        fi
        rm -f "$RUNTIME_DIR/opencode.lock/pid"
        rmdir "$RUNTIME_DIR/opencode.lock"
    fi
}

preload() {
    ensure_env
    docker compose --env-file "$ROOT/.env" pull litellm litellm-db open-webui openhands qdrant embedding-server
    docker compose --env-file "$ROOT/.env" build ingress memory-mcp file-edit-mcp web-search-mcp web-loader model-portal
    docker compose --env-file "$ROOT/.env" --profile cuda pull llama-server
    docker compose --env-file "$ROOT/.env" --profile web-search pull searxng
    docker compose --env-file "$ROOT/.env" --profile mcp-install pull mcp-installer
}

up() {
    local backend="" agent="" model="" quant="" context="" gpu_layers="auto" web_search="" sandbox_level=""
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --backend)
                [[ $# -ge 2 ]] || { usage >&2; exit 2; }
                backend="$2"
                shift 2
                ;;
            --agent)
                [[ $# -ge 2 ]] || { usage >&2; exit 2; }
                agent="$2"
                shift 2
                ;;
            --model)
                [[ $# -ge 2 ]] || { usage >&2; exit 2; }
                model="$2"
                shift 2
                ;;
            --quant)
                [[ $# -ge 2 ]] || { usage >&2; exit 2; }
                quant="$2"
                shift 2
                ;;
            --context)
                [[ $# -ge 2 ]] || { usage >&2; exit 2; }
                context="$2"
                shift 2
                ;;
            --gpu-layers)
                [[ $# -ge 2 ]] || { usage >&2; exit 2; }
                gpu_layers="$2"
                shift 2
                ;;
            --web-search)
                web_search=1
                shift
                ;;
            --no-web-search)
                web_search=0
                shift
                ;;
            --sandbox-level)
                [[ $# -ge 2 ]] || { usage >&2; exit 2; }
                sandbox_level="$2"
                shift 2
                ;;
            -h|--help)
                usage
                return 0
                ;;
            *)
                echo "Unknown option: $1" >&2
                usage >&2
                exit 2
                ;;
        esac
    done

    [[ "$backend" == "metal" || "$backend" == "cuda" ]] || {
        echo "--backend must be metal or cuda." >&2
        exit 2
    }
    [[ "$agent" == "openhands" || "$agent" == "opencode" ]] || {
        echo "--agent must be openhands or opencode." >&2
        exit 2
    }
    [[ -n "$model" ]] || {
        echo "--model is required." >&2
        exit 2
    }
    check_agent_lock
    ensure_env
    mkdir -p "$RUNTIME_DIR"
    set -a
    # shellcheck disable=SC1090
    . "$ROOT/.env"
    set +a
    context="${context:-${CONTEXT_SIZE:-8192}}"
    sandbox_level="${sandbox_level:-${OPENHANDS_SANDBOX_LEVEL:-full-control}}"
    case "$sandbox_level" in
        restricted)
            export OPENHANDS_WORKSPACE_MODE=ro
            export OPENHANDS_HOST_FILESYSTEM_MODE=ro
            export OPENHANDS_EGRESS_ISOLATED=true
            export OPENHANDS_ALLOW_COMMANDS=false
            export OPENHANDS_READ_ONLY_ROOT=true
            ;;
        read-only)
            export OPENHANDS_WORKSPACE_MODE=ro
            export OPENHANDS_HOST_FILESYSTEM_MODE=ro
            export OPENHANDS_EGRESS_ISOLATED=true
            export OPENHANDS_ALLOW_COMMANDS=false
            export OPENHANDS_READ_ONLY_ROOT=false
            ;;
        read-write|project)
            export OPENHANDS_WORKSPACE_MODE=rw
            export OPENHANDS_HOST_FILESYSTEM_MODE=ro
            export OPENHANDS_EGRESS_ISOLATED=true
            export OPENHANDS_ALLOW_COMMANDS=false
            export OPENHANDS_READ_ONLY_ROOT=false
            ;;
        full-control|networked)
            export OPENHANDS_WORKSPACE_MODE=rw
            export OPENHANDS_HOST_FILESYSTEM_MODE=rw
            export OPENHANDS_EGRESS_ISOLATED=false
            export OPENHANDS_ALLOW_COMMANDS=true
            export OPENHANDS_READ_ONLY_ROOT=false
            ;;
        *)
            echo "--sandbox-level must be restricted, read-only, read-write, or full-control (project and networked are aliases)." >&2
            exit 2
            ;;
    esac
    export OPENHANDS_SANDBOX_LEVEL="$sandbox_level"
    if [[ -z "$web_search" ]]; then
        local configured_web_search="${WEB_SEARCH_ENABLED:-true}"
        case "${configured_web_search,,}" in
            true|1|yes|on) web_search=1 ;;
            false|0|no|off) web_search=0 ;;
            *)
                echo "WEB_SEARCH_ENABLED must be true or false." >&2
                exit 2
                ;;
        esac
    fi

    local embedding_model="$ROOT/models/bge-small-en-v1.5/bge-small-en-v1.5-f16.gguf"
    if [[ ! -f "$embedding_model" ]]; then
        echo "Missing local embedding model: $embedding_model" >&2
        echo "Run ./install.sh or download it with scripts/models.sh before starting the stack." >&2
        exit 1
    fi

    local model_path
    model_path="$("$ROOT/scripts/models.sh" resolve "$model" "$quant")"
    export MODEL_PATH="$model_path"
    export CONTEXT_SIZE="$context"
    export GPU_LAYERS="$gpu_layers"
    export LLAMA_API_BASE

    if [[ "$backend" == "metal" ]]; then
        stop_cuda_model
        stop_local_model
        if curl --silent --fail --max-time 2 http://127.0.0.1:8080/health >/dev/null; then
            echo "A model server is already responding on port 8080; stop it before starting this stack." >&2
            exit 1
        fi
        LLAMA_API_BASE="http://host.docker.internal:8080/v1"
        export LLAMA_API_BASE
        local serve_args=("$model")
        [[ -n "$quant" ]] && serve_args+=(--quant "$quant")
        serve_args+=(--context "$context" --gpu-layers "$gpu_layers")
        "$ROOT/scripts/serve-model.sh" "${serve_args[@]}" >"$RUNTIME_DIR/llama-server.log" 2>&1 &
        echo "$!" > "$PID_FILE"

        local ready=0 attempt
        for attempt in $(seq 1 90); do
            if curl --silent --fail --max-time 2 http://127.0.0.1:8080/health >/dev/null; then
                ready=1
                break
            fi
            if ! kill -0 "$(<"$PID_FILE")" 2>/dev/null; then
                echo "llama-server exited during startup. Log: $RUNTIME_DIR/llama-server.log" >&2
                tail -n 40 "$RUNTIME_DIR/llama-server.log" >&2
                rm -f "$PID_FILE"
                exit 1
            fi
            sleep 1
        done
        if [[ "$ready" != "1" ]]; then
            stop_local_model
            echo "Timed out waiting for llama-server. Log: $RUNTIME_DIR/llama-server.log" >&2
            exit 1
        fi
    else
        stop_local_model
        LLAMA_API_BASE="http://llama-server:8080/v1"
        export LLAMA_API_BASE
    fi

    local profiles=()
    local services=(litellm litellm-db model-portal open-webui ingress)
    [[ "$backend" == "cuda" ]] && profiles+=(--profile cuda)
    if [[ "$agent" == "openhands" ]]; then
        profiles+=(--profile openhands)
        services+=(openhands)
        bash "$ROOT/scripts/prepare-openhands-workspace.sh"
    fi
    [[ "$backend" == "cuda" ]] && services+=(llama-server)
    if [[ "$web_search" == "1" ]]; then
        profiles+=(--profile web-search)
        services+=(searxng web-search-mcp web-loader)
        export ENABLE_WEB_SEARCH=true
    else
        export ENABLE_WEB_SEARCH=false
        docker compose --env-file "$ROOT/.env" --profile web-search \
            stop searxng web-search-mcp web-loader
    fi

    if [[ "$agent" == "opencode" ]]; then
        local running
        running="$(docker compose --env-file "$ROOT/.env" --profile openhands ps --status running --services)"
        if printf '%s\n' "$running" | grep -qx openhands; then
            docker compose --env-file "$ROOT/.env" --profile openhands stop openhands
        fi
    fi

    docker compose --env-file "$ROOT/.env" build ingress model-portal
    if ! docker compose --env-file "$ROOT/.env" "${profiles[@]}" up -d --wait "${services[@]}"; then
        stop_local_model
        exit 1
    fi
    if [[ "$agent" == "openhands" ]]; then
        bash "$ROOT/scripts/configure-openhands-defaults.sh"
    fi
    bash "$ROOT/scripts/configure-openwebui-defaults.sh" "$web_search"
    printf '%s\n' "$agent" > "$MODE_FILE"
    echo "Stack ready. Model: $model${quant:+ ($quant)}; agent mode: $agent."
    echo "Open WebUI: http://localhost:${OPEN_WEBUI_PORT:-3000}"
    echo "Open WebUI uses accounts/sign-up; a generated owner password is in the private .env."
    echo "Model accounts: ${BACKEND_PUBLIC_URL:-http://localhost:${LITELLM_PORT:-4000}}/register"
    if [[ "$agent" == "openhands" ]]; then
        echo "OpenHands sandbox: $sandbox_level"
        echo "OpenHands:   http://localhost:${OPENHANDS_PORT:-8000}/canvas"
        echo "Memory, file-editing, and search MCPs are provisioned automatically."
    else
        echo "OpenCode runs on the host and is not constrained by OpenHands sandbox settings."
        echo "OpenCode CLI: run scripts/code.sh from your project directory."
    fi
}

status() {
    ensure_env
    docker compose --env-file "$ROOT/.env" --profile cuda --profile openhands --profile web-search ps
    if [[ -f "$MODE_FILE" ]]; then
        printf 'Selected agent mode: %s\n' "$(<"$MODE_FILE")"
    fi
}

down() {
    check_agent_lock
    ensure_env
    docker compose --env-file "$ROOT/.env" --profile cuda --profile openhands --profile web-search --profile mcp-install down
    stop_local_model
    rm -f "$MODE_FILE"
}

command="${1:-}"
[[ -n "$command" ]] || { usage >&2; exit 2; }
shift

case "$command" in
    preload)
        [[ $# -eq 0 ]] || { usage >&2; exit 2; }
        preload
        ;;
    up)
        up "$@"
        ;;
    status)
        status
        ;;
    down)
        down
        ;;
    -h|--help)
        usage
        ;;
    *)
        echo "Unknown command: $command" >&2
        usage >&2
        exit 2
        ;;
esac
