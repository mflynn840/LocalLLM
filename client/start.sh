#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"
server_url="${MODEL_SERVER_URL:-}"
api_key="${MODEL_API_KEY:-}"

usage() {
    cat <<'EOF'
Usage: ./start.sh [--server-url http://backend:4000] [--api-key personal-key]
The API key is prompted securely when omitted. It is saved only in this folder's .env.
EOF
}
while [[ $# -gt 0 ]]; do
    case "$1" in
        --server-url) [[ $# -ge 2 ]] || { usage >&2; exit 2; }; server_url="$2"; shift 2 ;;
        --api-key) [[ $# -ge 2 ]] || { usage >&2; exit 2; }; api_key="$2"; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
    esac
done

if [[ -z "$server_url" ]]; then
    read -r -p "Backend URL (for example http://192.168.4.40:4000): " server_url
fi
[[ "$server_url" =~ ^https?://[A-Za-z0-9.-]+(:[0-9]{1,5})?$ ]] || {
    echo "Use a backend URL such as http://192.168.4.40:4000 (no path)." >&2; exit 2;
}
if [[ -z "$api_key" ]]; then
    read -r -s -p "Your personal model API key: " api_key
    printf '\n'
fi
[[ "$api_key" =~ ^[A-Za-z0-9._-]{12,256}$ ]] || { echo "The API key has an invalid format." >&2; exit 2; }

mkdir -p workspace
umask 077
local_key=""
if [[ -f .env ]]; then
    local_key="$(sed -n 's/^LOCAL_BACKEND_API_KEY=//p' .env | tail -n1)"
fi
if [[ ! "$local_key" =~ ^[A-Za-z0-9_-]{32,}$ ]]; then
    local_key="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
fi
cat > .env <<EOF
MODEL_SERVER_URL=$server_url
MODEL_API_KEY=$api_key
LOCAL_BACKEND_API_KEY=$local_key
CLIENT_OPENHANDS_PORT=${CLIENT_OPENHANDS_PORT:-8000}
EOF
chmod 600 .env
unset api_key local_key

docker compose --env-file .env -f compose.yaml up -d --wait
docker compose --env-file .env -f compose.yaml exec -T openhands python - <<'PY'
import json, os, time, urllib.error, urllib.parse, urllib.request
base = "http://127.0.0.1:18000/api/profiles"
key = os.environ["LOCAL_BACKEND_API_KEY"]
headers = {"X-Session-API-Key": key}
def req(url, method="GET", payload=None):
    data = None if payload is None else json.dumps(payload).encode()
    h = dict(headers)
    if data is not None: h["Content-Type"] = "application/json"
    with urllib.request.urlopen(urllib.request.Request(url, data=data, headers=h, method=method), timeout=10) as r:
        return json.load(r)
for i in range(30):
    try:
        profiles = req(base); break
    except (urllib.error.URLError, TimeoutError):
        if i == 29: raise
        time.sleep(1)
default_exists = any(p.get("name") == "default" for p in profiles.get("profiles", []))
if default_exists:
    detail = req(base + "/default")
    llm = detail.get("config", {}).get("llm", {})
else:
    detail, llm = {}, {}
if not default_exists or llm.get("base_url") != os.environ["LLM_BASE_URL"] or not detail.get("api_key_set"):
    req(base + "/default", "POST", {"llm": {
        "model": os.environ["LLM_MODEL"], "base_url": os.environ["LLM_BASE_URL"],
        "api_key": os.environ["LLM_API_KEY"], "auth_type": "api_key"
    }, "include_secrets": True})
profiles = req(base)
if profiles.get("active_profile") is None or profiles.get("active_profile") == "default":
    req(base + "/default/activate", "POST")
settings = "http://127.0.0.1:18000/api/settings"
current_settings = req(settings)
agent_settings = current_settings.get("agent_settings", {})
if agent_settings.get("allow_commands") is not True or agent_settings.get("require_approval") is not False:
    req(settings, "PATCH", {"agent_settings_diff": {"allow_commands": True, "require_approval": False}})
current = agent_settings.get("mcp_config", {})
name = "local-web-search"
path = settings + "/mcp/" + urllib.parse.quote(name, safe="")
method = "PATCH" if name in current else "POST"
req(path, method, {"url": os.environ["REMOTE_WEB_SEARCH_MCP_URL"],
                   "transport": "streamable-http", "enabled": True,
                   "description": "Backend SearXNG search and guarded public-page fetch"})
PY

echo "Client OpenHands is ready at http://localhost:${CLIENT_OPENHANDS_PORT:-8000}/canvas"
echo "Workspace: $ROOT/workspace (only this folder is mounted into the agent)."
echo "Internet search and guarded page fetch use the backend's local search service."
