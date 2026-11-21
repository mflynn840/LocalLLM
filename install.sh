#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$ROOT/.env"
VENV="$ROOT/.venv"
model_specs=()
quant_override=""

fail() {
    echo "Install failed: $*" >&2
    exit 1
}

usage() {
    cat <<'EOF'
Usage:
  ./install.sh [--model ALIAS[:QUANT] ...] [--quant QUANT]

The first --model becomes the persisted startup default. Additional --model
values are downloaded sequentially. Without options, .env's DEFAULT_MODEL and
DEFAULT_QUANT are used.
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --model)
            [[ $# -ge 2 ]] || { usage >&2; exit 2; }
            model_specs+=("$2")
            shift 2
            ;;
        --quant)
            [[ $# -ge 2 ]] || { usage >&2; exit 2; }
            quant_override="$2"
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

if ! command -v docker >/dev/null 2>&1; then
    fail "Docker is required. Install Docker Desktop and enable WSL integration, then rerun ./install.sh."
fi
if ! docker compose version >/dev/null 2>&1; then
    fail "Docker Compose v2 is required."
fi
if ! docker info >/dev/null 2>&1; then
    fail "Docker is not running or is unavailable in this WSL distribution. Start Docker Desktop and enable WSL integration."
fi
if ! command -v python3 >/dev/null 2>&1; then
    fail "Python 3 is required to install and run the Hugging Face CLI."
fi

if [[ ! -f "$ENV_FILE" ]]; then
    cp "$ROOT/.env.example" "$ENV_FILE"
    chmod 600 "$ENV_FILE"
    echo "Created .env from .env.example."
fi

set -a
# shellcheck disable=SC1090
. "$ENV_FILE"
set +a

default_model="${DEFAULT_MODEL-gemma-4-12b}"
default_quant="${DEFAULT_QUANT-Q4_K_M}"
persist_defaults=0
if [[ ${#model_specs[@]} -gt 0 ]]; then
    if [[ "${model_specs[0]}" =~ ^([A-Za-z0-9][A-Za-z0-9._-]*)(:([A-Za-z0-9._-]+))?$ ]]; then
        default_model="${BASH_REMATCH[1]}"
        selected_quant="${BASH_REMATCH[3]:-}"
    else
        fail "Invalid default model spec: ${model_specs[0]}"
    fi
    if [[ -n "$selected_quant" && -n "$quant_override" ]]; then
        fail "Use either a quant in the first --model value or --quant, not both."
    fi
    default_quant="${selected_quant:-$quant_override}"
    persist_defaults=1
elif [[ -n "$quant_override" ]]; then
    default_quant="$quant_override"
    persist_defaults=1
fi

if ! awk -F'|' -v alias="$default_model" '$1 == alias && $1 !~ /^#/ { found=1 } END { exit !found }' "$ROOT/config/models.tsv"; then
    fail "Unknown default model alias: $default_model. See ./scripts/models.sh list."
fi
if [[ -n "$default_quant" && ! "$default_quant" =~ ^[A-Za-z0-9._-]+$ ]]; then
    fail "Invalid quantization: $default_quant"
fi

download_args=(--model "$default_model${default_quant:+:$default_quant}")
for spec in "${model_specs[@]:1}"; do
    download_args+=(--model "$spec")
done

python3 - "$ENV_FILE" "$ROOT" "$default_model" "$default_quant" "$persist_defaults" <<'PY'
import os
import secrets
import sys
from pathlib import Path

env_file = Path(sys.argv[1])
project_root = Path(sys.argv[2])
default_model, default_quant = sys.argv[3:5]
persist_defaults = sys.argv[5] == "1"
lines = env_file.read_text(encoding="utf-8").splitlines()
values = {
    line.split("=", 1)[0]: line.split("=", 1)[1]
    for line in lines
    if "=" in line and not line.lstrip().startswith("#")
}
replacements = {
    "LITELLM_MASTER_KEY": ("sk-local-dev-change-me", "sk-" + secrets.token_urlsafe(32)),
    "SEARXNG_SECRET": ("generate-with-install", secrets.token_urlsafe(32)),
    "WEB_LOADER_API_KEY": ("generate-with-install", secrets.token_urlsafe(32)),
    "OPENHANDS_WORKSPACE_GID": ("1000", str(os.getgid())),
    "OPENHANDS_UID": ("10001", "10001"),
    "OPENHANDS_SANDBOX_LEVEL": ("project", "project"),
    "WEB_SEARCH_ENABLED": ("true", "true"),
}
for key, (placeholder, generated) in replacements.items():
    current = values.get(key, "").strip().strip("\"'")
    if not current or current == placeholder:
        values[key] = generated
        prefix = key + "="
        matching = [i for i, line in enumerate(lines) if line.startswith(prefix)]
        if matching:
            lines[matching[-1]] = prefix + generated
        else:
            lines.append(prefix + generated)

for key, value, placeholder in (
    ("DEFAULT_MODEL", default_model, "gemma-4-12b"),
    ("DEFAULT_QUANT", default_quant, "Q4_K_M"),
):
    current = values.get(key, "").strip().strip("\"'")
    if persist_defaults or not current or current == placeholder:
        prefix = key + "="
        matching = [i for i, line in enumerate(lines) if line.startswith(prefix)]
        if matching:
            lines[matching[-1]] = prefix + value
        else:
            lines.append(prefix + value)

workspace_prefix = "WORKSPACE_DIR="
workspace_lines = [i for i, line in enumerate(lines) if line.startswith(workspace_prefix)]
if not workspace_lines:
    lines.append(workspace_prefix + str(project_root))
elif lines[workspace_lines[-1]].split("=", 1)[1].strip().strip("\"'") == "./workspace":
    lines[workspace_lines[-1]] = workspace_prefix + str(project_root)

env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
os.chmod(env_file, 0o600)
print("Updated local credentials, workspace path, and runtime defaults in .env.")
PY
chmod 600 "$ENV_FILE"

if ! command -v hf >/dev/null 2>&1 && [[ ! -x "$VENV/bin/hf" ]]; then
    if [[ ! -x "$VENV/bin/python" ]]; then
        if ! python3 -m venv "$VENV"; then
            fail "Could not create .venv. Install your distro's Python venv package (for Ubuntu: sudo apt install python3-venv), then rerun ./install.sh."
        fi
    fi
    "$VENV/bin/python" -m pip install --upgrade huggingface_hub
fi

echo "Pulling service images and building the isolated services..."
"$ROOT/scripts/stack.sh" preload

echo "Downloading the default model: $default_model${default_quant:+ ($default_quant)}..."
"$ROOT/scripts/models.sh" download "${download_args[@]}"
echo "Downloading the local embedding model for persistent memory and RAG..."
"$ROOT/scripts/models.sh" download \
    --repo CompendiumLabs/bge-small-en-v1.5-gguf \
    --name bge-small-en-v1.5 \
    --include bge-small-en-v1.5-f16.gguf
echo "Installing Browser MCP into the persistent OpenHands runtime..."
"$ROOT/scripts/mcp.sh" install @browsermcp/mcp@2.5.0

echo
echo "Installation complete."
echo "Start the stack with: ./start.sh"
