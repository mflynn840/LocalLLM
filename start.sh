#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

requested_mode="${MODE:-backend}"
forwarded=()
while [[ $# -gt 0 ]]; do
    case "$1" in
        --mode)
            [[ $# -ge 2 ]] || { echo "--mode requires backend or client." >&2; exit 2; }
            requested_mode="$2"
            shift 2
            ;;
        *) forwarded+=("$1"); shift ;;
    esac
done
set -- "${forwarded[@]}"
if [[ "$requested_mode" == "client" ]]; then
    exec "$ROOT/client/start.sh" "$@"
elif [[ "$requested_mode" != "backend" ]]; then
    echo "--mode must be backend or client." >&2
    exit 2
fi

if [[ -f "$ROOT/.env" ]]; then
    set -a
    # shellcheck disable=SC1090
    . "$ROOT/.env"
    set +a
fi

backend="${BACKEND:-cuda}"
agent="${AGENT:-openhands}"
model="${MODEL:-${DEFAULT_MODEL:-gemma-4-12b}}"
quant_explicit=0
if [[ -n "${QUANT:-}" ]]; then
    quant="$QUANT"
    quant_explicit=1
elif [[ -n "${MODEL:-}" ]]; then
    quant=""
else
    quant="${DEFAULT_QUANT-Q4_K_M}"
fi
context="${CONTEXT_SIZE_OVERRIDE:-}"
web_search=""
sandbox_level=""

usage() {
    cat <<'EOF'
Usage:
  ./start.sh [--mode backend|client] [--backend cuda|metal] [--agent openhands|opencode]
             [--model ALIAS] [--quant QUANT] [--context TOKENS]
             [--web-search|--no-web-search] [--sandbox-level restricted|read-only|read-write|full-control|project|networked]

Defaults: CUDA, OpenHands, and DEFAULT_MODEL/DEFAULT_QUANT from .env.
Deployment mode defaults to backend. Use --mode client to start the portable local agent.
Local web search is enabled by default; use --no-web-search to disable it.
Sandbox defaults to OPENHANDS_SANDBOX_LEVEL from .env (full-control if unset).
EOF
}

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
            if [[ "$quant_explicit" == "0" ]]; then
                quant=""
            fi
            shift 2
            ;;
        --quant)
            [[ $# -ge 2 ]] || { usage >&2; exit 2; }
            quant="$2"
            quant_explicit=1
            shift 2
            ;;
        --context)
            [[ $# -ge 2 ]] || { usage >&2; exit 2; }
            context="$2"
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
            exit 0
            ;;
        *)
            echo "Unknown option: $1" >&2
            usage >&2
            exit 2
            ;;
    esac
done

if [[ "$agent" == "opencode" ]] && ! command -v opencode >/dev/null 2>&1; then
    echo "OpenCode CLI is not installed. Install it while online with: npm install -g @opencode/cli" >&2
    exit 1
fi

args=(--backend "$backend" --agent "$agent" --model "$model")
[[ -n "$quant" ]] && args+=(--quant "$quant")
[[ -n "$context" ]] && args+=(--context "$context")
[[ "$web_search" == "1" ]] && args+=(--web-search)
[[ "$web_search" == "0" ]] && args+=(--no-web-search)
[[ -n "$sandbox_level" ]] && args+=(--sandbox-level "$sandbox_level")
"$ROOT/scripts/stack.sh" up "${args[@]}"

if [[ -f "$ROOT/.env" ]]; then
    set -a
    # shellcheck disable=SC1090
    . "$ROOT/.env"
    set +a
fi
web_search="${web_search:-${WEB_SEARCH_ENABLED:-true}}"

cat <<EOF

Getting started:
  OpenHands:   http://localhost:${OPENHANDS_PORT:-8000}/canvas
  Open WebUI:  http://localhost:${OPEN_WEBUI_PORT:-3000}
  User signup: ${BACKEND_PUBLIC_URL:-http://localhost:${LITELLM_PORT:-4000}}/register
  Workspace:   ${WORKSPACE_DIR:-$ROOT/workspace} (mounted into OpenHands as /projects)
  Give OpenHands a small task, review its plan and changes, then ask it to test.

Download and switch models:
  ./scripts/models.sh list
  ./scripts/models.sh download --model qwen3-coder-30b-a3b:Q3_K_M
  ./start.sh --model qwen3-coder-30b-a3b --quant Q3_K_M

Switch coding agents:
  ./start.sh --agent opencode
  cd /path/to/your/project && "$ROOT/scripts/code.sh"
  ./start.sh --agent openhands
EOF

if [[ "${web_search,,}" == "true" || "$web_search" == "1" || "${web_search,,}" == "yes" || "${web_search,,}" == "on" ]]; then
    cat <<'EOF'

Web search and local MCP tools are enabled by default.
EOF
fi
