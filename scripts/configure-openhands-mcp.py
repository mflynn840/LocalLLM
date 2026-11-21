import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


BASE_URL = "http://127.0.0.1:18000"
SESSION_KEY_PATH = Path("/home/openhands/.openhands/agent-canvas/api-key.txt")
NPM_BIN_DIR = Path("/home/openhands/.local/npm/bin").resolve()
MCP_MANIFEST = Path("/home/openhands/.local/mcp-packages.json")

session_key = SESSION_KEY_PATH.read_text(encoding="utf-8").strip()
headers = {"X-Session-API-Key": session_key}


def request_json(path, *, method="GET", payload=None):
    data = None if payload is None else json.dumps(payload).encode()
    request_headers = dict(headers)
    if data is not None:
        request_headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        f"{BASE_URL}{path}",
        data=data,
        headers=request_headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        raise RuntimeError(
            f"OpenHands API returned HTTP {error.code} for {method} {path}."
        ) from error


def current_servers():
    settings = request_json("/api/settings")
    return settings.get("agent_settings", {}).get("mcp_config", {})


def test_server(name, server, *, wait=False):
    test_spec = dict(server)
    test_spec["type"] = test_spec.pop("transport")
    attempts = 20 if wait else 1
    for attempt in range(attempts):
        result = request_json(
            "/api/mcp/test",
            method="POST",
            payload={"name": name, "server": test_spec, "timeout": 15},
        )
        if result.get("ok"):
            return result.get("tools", [])
        if attempt + 1 < attempts:
            time.sleep(1)
    raise RuntimeError(f"Could not connect to MCP server {name}: {result.get('error', 'unknown error')}")


def save_server(name, server):
    tools = test_server(name, server, wait=server["transport"] != "stdio")
    encoded_name = urllib.parse.quote(name, safe="")
    existing = current_servers()
    method = "PATCH" if name in existing else "POST"
    request_json(
        f"/api/settings/mcp/{encoded_name}",
        method=method,
        payload=server,
    )
    print(f"OpenHands MCP {name}: {', '.join(tools)}")


def set_enabled(name, enabled):
    if name not in current_servers():
        return
    request_json(
        f"/api/settings/mcp/{urllib.parse.quote(name, safe='')}",
        method="PATCH",
        payload={"enabled": enabled},
    )


def load_installed_servers():
    if not MCP_MANIFEST.exists():
        return []
    records = json.loads(MCP_MANIFEST.read_text(encoding="utf-8"))
    if not isinstance(records, list):
        raise RuntimeError("OpenHands MCP package manifest must contain a list.")
    servers = []
    for record in records:
        if not isinstance(record, dict):
            raise RuntimeError("OpenHands MCP package manifest contains an invalid entry.")
        name = record.get("id")
        command = record.get("command")
        args = record.get("args", [])
        if not isinstance(name, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,62}", name):
            raise RuntimeError("OpenHands MCP package manifest contains an invalid server ID.")
        if not isinstance(command, str) or not isinstance(args, list) or not all(
            isinstance(arg, str) for arg in args
        ):
            raise RuntimeError(f"OpenHands MCP package manifest entry {name} is invalid.")
        binary = Path(command)
        if not binary.is_absolute() or binary.parent != NPM_BIN_DIR or not binary.is_file():
            raise RuntimeError(f"Installed MCP executable for {name} is missing or outside the npm prefix.")
        servers.append(
            {
                "id": name,
                "server": {
                    "command": str(binary),
                    "args": args,
                    "transport": "stdio",
                    "enabled": True,
                    "description": str(record.get("package", name)),
                },
            }
        )
    return servers


def configure():
    save_server(
        "local-memory",
        {
            "url": "http://memory-mcp:8000/mcp",
            "transport": "streamable-http",
            "enabled": True,
            "description": "Shared local persistent project memory",
        },
    )

    search_enabled = os.environ.get("OPENHANDS_WEB_SEARCH_MCP_ENABLED", "true").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    if search_enabled:
        save_server(
            "local-web-search",
            {
                "url": "http://web-search-mcp:8000/mcp",
                "transport": "streamable-http",
                "enabled": True,
                "description": "Local SearXNG search and guarded page fetching",
            },
        )
    else:
        set_enabled("local-web-search", False)
        print("OpenHands web-search MCP is disabled.")

    for record in load_installed_servers():
        save_server(record["id"], record["server"])


configure()
