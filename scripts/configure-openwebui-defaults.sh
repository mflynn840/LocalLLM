#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
web_search="${1:?Pass 1 to enable web search or 0 to disable it.}"

result="$(
    docker compose --env-file "$ROOT/.env" exec -T open-webui python - "$web_search" <<'PY'
import asyncio
import json
import os
import secrets
import sqlite3
import sys
import time
import uuid
from datetime import datetime, timezone

enabled = sys.argv[1] == "1"
db_path = "/app/backend/data/webui.db"
search_url = os.environ.get("SEARXNG_QUERY_URL", "http://searxng:8080/search")
loader_url = os.environ.get("EXTERNAL_WEB_LOADER_URL", "http://web-loader:8001/load")
loader_key = os.environ.get("EXTERNAL_WEB_LOADER_API_KEY", "")

for attempt in range(30):
    try:
        db = sqlite3.connect(db_path, timeout=5)
        tables = {
            row[0]
            for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        if {"config", "user", "auth"} <= tables:
            break
        db.close()
    except sqlite3.OperationalError:
        if attempt == 29:
            raise
    time.sleep(1)
else:
    raise RuntimeError("Open WebUI config table was not initialized.")

changed = False

def decode(value, default):
    if value is None:
        return default
    return json.loads(value) if isinstance(value, str) else value

def set_config(key, value):
    global changed
    encoded = json.dumps(value, separators=(",", ":"))
    row = db.execute("SELECT value FROM config WHERE key=?", (key,)).fetchone()
    if row is None or decode(row[0], None) != value:
        db.execute(
            """
            INSERT INTO config (key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value=excluded.value,
                updated_at=excluded.updated_at
            """,
            (key, encoded, datetime.now(timezone.utc).isoformat()),
        )
        changed = True

def configure_noauth_identity():
    global changed
    if os.environ.get("WEBUI_AUTH", "true").lower() not in {"0", "false", "no", "off"}:
        return

    system_email = "admin@localhost"
    system_users = db.execute(
        "SELECT id, role FROM user WHERE lower(email)=?",
        (system_email,),
    ).fetchall()
    if len(system_users) > 1:
        raise RuntimeError("Open WebUI has multiple system accounts; refusing an unsafe no-auth migration.")

    if system_users:
        user_id, role = system_users[0]
        migrated = False
    else:
        admins = db.execute(
            "SELECT id, email, role FROM user WHERE role='admin'"
        ).fetchall()
        user_count = db.execute("SELECT COUNT(*) FROM user").fetchone()[0]
        if user_count == 0:
            return
        if len(admins) != 1:
            raise RuntimeError(
                "No-auth Open WebUI setup requires exactly one existing administrator "
                "when user data is present; refusing to change accounts."
            )
        user_id, _, role = admins[0]
        migrated = True

    auth = db.execute(
        "SELECT email, password, active FROM auth WHERE id=?",
        (user_id,),
    ).fetchone()
    if auth is None:
        raise RuntimeError("Open WebUI administrator has no matching authentication record.")

    auth_email, password_hash, active = auth
    needs_password = migrated or not password_hash
    if not needs_password:
        os.environ["WEBUI_SECRET_KEY"] = (
            os.environ.get("WEBUI_SECRET_KEY") or secrets.token_urlsafe(48)
        )
        from open_webui.utils.auth import verify_password

        needs_password = not asyncio.run(verify_password("admin", password_hash))

    if needs_password:
        os.environ["WEBUI_SECRET_KEY"] = (
            os.environ.get("WEBUI_SECRET_KEY") or secrets.token_urlsafe(48)
        )
        from open_webui.utils.auth import get_password_hash

        password_hash = asyncio.run(get_password_hash("admin"))

    if migrated or role != "admin":
        db.execute(
            "UPDATE user SET email=?, role='admin', updated_at=? WHERE id=?",
            (system_email, int(time.time()), user_id),
        )
        changed = True

    if (
        migrated
        or auth_email != system_email
        or needs_password
        or not active
    ):
        db.execute(
            """
            UPDATE auth
            SET email=?, password=?, active=1, session_stamp=?
            WHERE id=?
            """,
            (system_email, password_hash, str(uuid.uuid4()), user_id),
        )
        changed = True

def configure_authenticated_admin():
    global changed
    if os.environ.get("WEBUI_AUTH", "true").lower() in {"0", "false", "no", "off"}:
        return
    password = os.environ.get("WEBUI_ADMIN_PASSWORD", "")
    if not password:
        raise RuntimeError("OPEN_WEBUI_ADMIN_PASSWORD is required when WebUI authentication is enabled.")
    admins = db.execute("SELECT id, email FROM user WHERE role='admin'").fetchall()
    if not admins:
        # Fresh Open WebUI databases create the first admin through the signup page.
        return
    system_admin = [row for row in admins if row[1].lower() == "admin@localhost"]
    if len(system_admin) == 1:
        user_id, email = system_admin[0]
    elif len(admins) == 1:
        user_id, email = admins[0]
    else:
        raise RuntimeError("Multiple Open WebUI administrators found; refusing to choose one for password setup.")
    auth = db.execute("SELECT password, active FROM auth WHERE id=?", (user_id,)).fetchone()
    if auth is None:
        raise RuntimeError("Open WebUI administrator has no matching authentication record.")
    password_hash, active = auth
    from open_webui.utils.auth import get_password_hash, verify_password
    needs_password = not password_hash or not asyncio.run(verify_password(password, password_hash))
    if needs_password or not active:
        new_hash = asyncio.run(get_password_hash(password)) if needs_password else password_hash
        db.execute(
            "UPDATE auth SET password=?, active=1, session_stamp=? WHERE id=?",
            (new_hash, str(uuid.uuid4()), user_id),
        )
        changed = True

configure_authenticated_admin()
configure_noauth_identity()

rows = {
    "ui.enable_signup": True,
    "web.search.enable": enabled,
    "web.search.engine": "searxng",
    "web.search.result_count": 5,
    "web.search.confirmation.enable": True,
    "web.search.searxng_query_url": search_url,
    "web.loader.engine": "external",
    "web.loader.external_web_loader_url": loader_url,
    "web.loader.external_web_loader_api_key": loader_key,
}
for key, value in rows.items():
    set_config(key, value)

connections_row = db.execute(
    "SELECT value FROM config WHERE key='tool_server.connections'"
).fetchone()
connections = decode(connections_row[0] if connections_row else None, [])
if not isinstance(connections, list):
    raise RuntimeError("Open WebUI tool_server.connections is not a list.")

def ensure_mcp_connection(name, url, enabled):
    global changed
    connection = next(
        (
            item
            for item in connections
            if isinstance(item, dict)
            and item.get("type") == "mcp"
            and item.get("url") == url
        ),
        None,
    )
    if connection is None:
        connection = {
            "url": url,
            "path": "",
            "type": "mcp",
            "auth_type": "none",
            "forward_cookies": False,
            "headers": None,
            "key": None,
            "config": {"enable": enabled, "access_grants": []},
            "info": {"id": name, "name": name},
        }
        connections.append(connection)
        changed = True
        return

    config = connection.get("config")
    if not isinstance(config, dict):
        config = {}
        connection["config"] = config
    if config.get("enable") != enabled:
        config["enable"] = enabled
        changed = True
    if not connection.get("info"):
        connection["info"] = {"id": name, "name": name}
        changed = True

ensure_mcp_connection(
    "local-memory",
    "http://memory-mcp:8000/mcp",
    True,
)
ensure_mcp_connection(
    "local-web-search",
    "http://web-search-mcp:8000/mcp",
    enabled,
)
set_config("tool_server.connections", connections)

db.commit()
db.close()
print("changed" if changed else "unchanged")
PY
)"

if [[ "$result" == "changed" ]]; then
    echo "Applying Open WebUI search and local MCP defaults..."
    docker compose --env-file "$ROOT/.env" restart open-webui
    for attempt in $(seq 1 30); do
        if docker compose --env-file "$ROOT/.env" exec -T open-webui \
            python -c 'import urllib.request; urllib.request.urlopen("http://127.0.0.1:8080/", timeout=2)' \
            >/dev/null 2>&1; then
            break
        fi
        if [[ "$attempt" == 30 ]]; then
            echo "Open WebUI did not become ready after applying its defaults." >&2
            exit 1
        fi
        sleep 1
    done
    echo "Open WebUI defaults are active."
else
    echo "Open WebUI search and local MCP defaults are already current."
fi
