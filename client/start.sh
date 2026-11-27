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
CLIENT_WORKSPACE_HOST_PATH=$ROOT/workspace
EOF
chmod 600 .env
unset api_key local_key

bash "$ROOT/../scripts/openhands-host-terminal.sh" "$ROOT/workspace" start

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
ssh_name = "ssh-hosts"
ssh_path = settings + "/mcp/" + urllib.parse.quote(ssh_name, safe="")
ssh_method = "PATCH" if ssh_name in current else "POST"
req(ssh_path, ssh_method, {
    "command": "/usr/local/bin/python", "args": ["/opt/agent-canvas/ssh-mcp.py"],
    "transport": "stdio", "enabled": True,
    "description": "Named SSH targets and remote commands/file transfers; SSH keys stay on this PC"
})
guidance_marker = "<openhands-host-and-ssh-workflow>"
guidance = (guidance_marker + "\n" +
    "Default to My PC (WSL): read, edit, and execute directly on the host as the WSL user. "
    "Use Docker execution only when the user explicitly asks for it. If the user asks for Jetson work, "
    "use ssh-hosts MCP: list hosts, refresh the remote map with ssh_map_files, search with ssh_search_files, then "
    "use ssh_exec, ssh_read_file, and ssh_write_file with host key jetson. Keep all inspection, edits, and commands "
    "for the SSH task on that host; do not switch to local workspace tools. SSH credentials remain on this PC.\n" +
    "</openhands-host-and-ssh-workflow>")
settings_data = req(settings)
agent_settings = settings_data.get("agent_settings", {})
context = agent_settings.get("agent_context") or {}
suffix = context.get("system_message_suffix") or ""
import re
suffix = re.sub(re.escape(guidance_marker) + r".*?</openhands-host-and-ssh-workflow>", "", suffix, flags=re.S).strip()
context["system_message_suffix"] = (suffix + "\n\n" + guidance).strip()
req(settings, "PATCH", {"agent_settings_diff": {"agent_context": context}})
profiles_url = "http://127.0.0.1:18000/api/agent-profiles"
for profile_info in req(profiles_url).get("profiles", []):
    name = profile_info.get("name")
    if not name:
        continue
    detail = req(profiles_url + "/" + urllib.parse.quote(name, safe=""))
    profile = detail.get("profile", {})
    if profile.get("agent_kind") == "acp":
        continue
    current_suffix = profile.get("system_message_suffix") or ""
    current_suffix = re.sub(re.escape(guidance_marker) + r".*?</openhands-host-and-ssh-workflow>", "", current_suffix, flags=re.S).strip()
    profile["system_message_suffix"] = (current_suffix + "\n\n" + guidance).strip()
    req(profiles_url + "/" + urllib.parse.quote(name, safe=""), "POST", profile)
PY

echo "Client OpenHands is ready at http://localhost:${CLIENT_OPENHANDS_PORT:-8000}/canvas"
echo "Workspace: $ROOT/workspace. My PC (WSL) execution can work across files accessible to your WSL user."
echo "Internet search and guarded page fetch use the backend's local search service."
