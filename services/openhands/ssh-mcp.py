"""General SSH tools backed by the private WSL host bridge."""
import json
import socket
from typing import Any

from mcp.server.mcpserver import MCPServer

SOCKET_PATH = "/run/openhands-host/terminal.sock"
server = MCPServer("SSH hosts")


def host_request(request: dict[str, Any]) -> dict[str, Any]:
    payload = json.dumps(request, ensure_ascii=False).encode() + b"\n"
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(24 * 60 * 60)
        connection.connect(SOCKET_PATH)
        connection.sendall(payload)
        response = bytearray()
        while len(response) <= 256 * 1024 * 1024:
            chunk = connection.recv(65536)
            if not chunk:
                break
            response.extend(chunk)
            if b"\n" in chunk:
                break
    if not response or len(response) > 256 * 1024 * 1024:
        raise RuntimeError("The SSH host bridge returned an empty or oversized response.")
    return json.loads(bytes(response).split(b"\n", 1)[0])


@server.tool()
def ssh_hosts() -> dict[str, Any]:
    """List saved SSH targets by key name, including their non-secret metadata."""
    return host_request({"operation": "ssh_hosts"})


@server.tool()
def ssh_save_host(key: str, target: str, port: int = 22, description: str = "", metadata: str = "{}") -> dict[str, Any]:
    """Save or update a named SSH target. target is an SSH config alias or user@host; private keys stay on the host."""
    if not key.strip() or len(key) > 64:
        return {"exit_code": 2, "stderr": "key must contain 1-64 characters"}
    try:
        parsed = json.loads(metadata)
        if not isinstance(parsed, dict):
            raise ValueError()
    except (ValueError, TypeError):
        return {"exit_code": 2, "stderr": "metadata must be a JSON object"}
    return host_request({"operation": "ssh_save_host", "key": key, "target": target, "port": port,
                         "description": description, "metadata": parsed})


@server.tool()
def ssh_remove_host(key: str) -> dict[str, Any]:
    """Remove a saved SSH target by key name."""
    return host_request({"operation": "ssh_remove_host", "key": key})


@server.tool()
def ssh_exec(host: str, command: str, timeout_seconds: int = 3600) -> dict[str, Any]:
    """Run a shell command on a saved SSH host, returning exit code/stdout/stderr. Commands may run up to 24 hours."""
    if not command.strip():
        return {"exit_code": 2, "stderr": "command cannot be empty"}
    if timeout_seconds < 1 or timeout_seconds > 86400:
        return {"exit_code": 2, "stderr": "timeout_seconds must be 1-86400"}
    return host_request({"operation": "ssh_exec", "host": host, "command": command,
                         "timeout_seconds": timeout_seconds})


@server.tool()
def ssh_map_files(host: str, root: str, refresh: bool = True, offset: int = 0, page_size: int = 80) -> dict[str, Any]:
    """Build or retrieve a persistent file map for a remote project. Use it to orient before editing remote code."""
    return host_request({"operation": "ssh_map_files", "host": host, "root": root, "refresh": refresh,
                         "offset": offset, "page_size": page_size})


@server.tool()
def ssh_search_files(host: str, root: str, query: str, search_kind: str = "content", glob: str = "*") -> dict[str, Any]:
    """Search filenames or remote file contents in an SSH project, excluding common generated directories."""
    if not query.strip() or len(query) > 512:
        return {"exit_code": 2, "stderr": "query must contain 1-512 characters"}
    if search_kind not in {"content", "filename"}:
        return {"exit_code": 2, "stderr": "search_kind must be content or filename"}
    return host_request({"operation": "ssh_search_files", "host": host, "root": root,
                         "query": query, "search_kind": search_kind, "glob": glob})


@server.tool()
def ssh_read_file(host: str, path: str, offset: int = 0, max_bytes: int = 20000) -> dict[str, Any]:
    """Read a remote UTF-8 file range. Use offset/max_bytes to page through large files."""
    if offset < 0 or max_bytes < 1 or max_bytes > 48000:
        return {"exit_code": 2, "stderr": "offset must be nonnegative and max_bytes must be 1-48000"}
    return host_request({"operation": "ssh_read_file", "host": host, "path": path,
                         "offset": offset, "max_bytes": max_bytes})


@server.tool()
def ssh_write_file(host: str, path: str, content: str, mode: str = "0644", append: bool = False) -> dict[str, Any]:
    """Write a remote UTF-8 file, creating parent directories. Append chunks for files larger than one tool call."""
    if len(content.encode("utf-8")) > 32 * 1024 * 1024:
        return {"exit_code": 2, "stderr": "chunk exceeds 32 MiB; write the file in ordered chunks and set append=true after the first"}
    return host_request({"operation": "ssh_write_file", "host": host, "path": path,
                         "content": content, "mode": mode, "append": append})


if __name__ == "__main__":
    server.run(transport="stdio")
