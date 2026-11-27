"""Expose the host's configured Jetson SSH alias to OpenHands over its private socket."""
import json
import socket
from typing import Any

from mcp.server.mcpserver import MCPServer

SOCKET_PATH = "/run/openhands-host/terminal.sock"
server = MCPServer("Jetson host SSH")


@server.tool()
def jetson_exec(command: str) -> dict[str, Any]:
    """Run a command on the Jetson when the user explicitly requests Jetson work.

    The SSH alias and key stay on the host and are never exposed to this container.
    The SSH account is michael and has full sudo access. Preserve user data and SSD
    contents unless the user explicitly requests a destructive storage operation.
    """
    if not isinstance(command, str) or not command.strip() or len(command) > 4096:
        return {"exit_code": 2, "stdout": "", "stderr": "command must contain 1-4096 characters"}

    request = json.dumps({"operation": "jetson_exec", "command": command}) + "\n"
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(125)
        connection.connect(SOCKET_PATH)
        connection.sendall(request.encode())
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


if __name__ == "__main__":
    server.run(transport="stdio")
