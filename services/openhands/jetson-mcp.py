"""Expose the host's configured Jetson SSH alias to OpenHands over its private socket."""
import json
import socket
from typing import Any

from mcp.server.mcpserver import MCPServer

SOCKET_PATH = "/run/openhands-host/terminal.sock"
MAX_FILE_BYTES = 2_000_000
server = MCPServer("Jetson host SSH")


def host_request(request: dict[str, Any]) -> dict[str, Any]:
    payload = json.dumps(request, ensure_ascii=False).encode() + b"\n"
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(150)
        connection.connect(SOCKET_PATH)
        connection.sendall(payload)
        response = bytearray()
        while len(response) <= 140_000:
            chunk = connection.recv(4096)
            if not chunk:
                break
            response.extend(chunk)
            if b"\n" in chunk:
                break
    if not response or len(response) > 140_000:
        raise RuntimeError("The host Jetson bridge returned an empty or oversized response.")
    return json.loads(bytes(response).split(b"\n", 1)[0])


@server.tool()
def jetson_exec(command: str) -> dict[str, Any]:
    """Run a command on the Jetson when the user explicitly requests Jetson work.

    The SSH alias and key stay on the host and are never exposed to this container.
    The SSH account is michael and has full sudo access. Preserve user data and SSD
    contents unless the user explicitly requests a destructive storage operation.
    """
    if not isinstance(command, str) or not command.strip() or len(command) > 4096:
        return {"exit_code": 2, "stdout": "", "stderr": "command must contain 1-4096 characters"}

    return host_request({"operation": "jetson_exec", "command": command})


@server.tool()
def jetson_write_file(path: str, content: str) -> dict[str, Any]:
    """Write a UTF-8 project file directly to /home/michael/Code/task1 on the Jetson.

    Use this for source, documentation, configuration, and test files requested on
    the Jetson. The host bridge transfers the content over SSH; do not use local cp,
    scp, or ssh from the OpenHands workspace. Parent directories are created.
    """
    if not isinstance(path, str) or not path.startswith("/home/michael/Code/task1/"):
        return {"exit_code": 2, "stdout": "", "stderr": "path must be inside /home/michael/Code/task1"}
    if any(part in ("", ".", "..") for part in path.removeprefix("/home/michael/Code/task1/").split("/")):
        return {"exit_code": 2, "stdout": "", "stderr": "path must be a normalized file path inside task1"}
    if not isinstance(content, str):
        return {"exit_code": 2, "stdout": "", "stderr": "content must be a string"}
    if len(content.encode("utf-8")) > MAX_FILE_BYTES:
        return {"exit_code": 2, "stdout": "", "stderr": f"content exceeds {MAX_FILE_BYTES} bytes"}
    return host_request({"operation": "jetson_write_file", "path": path, "content": content})


if __name__ == "__main__":
    server.run(transport="stdio")
