#!/usr/bin/env python3
"""Private Unix-socket PTY bridge. Commands run as the WSL user."""
import json
import os
import pty
import select
import socket
import subprocess
import sys
import termios
import signal
import time


def serve(path: str, workspace: str) -> None:
    os.umask(0o007)
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(path)
    os.chmod(path, 0o660)
    server.listen(8)
    while True:
        conn, _ = server.accept()
        try:
            header = b""
            while not header.endswith(b"\n") and len(header) < 8192:
                chunk = conn.recv(1)
                if not chunk:
                    break
                header += chunk
            request = json.loads(header)
            if request.get("operation") == "jetson_exec":
                command = request.get("command")
                if not isinstance(command, str) or not command.strip() or len(command) > 4096:
                    conn.sendall(b'{"exit_code":2,"stdout":"","stderr":"command must contain 1-4096 characters"}\n')
                    continue
                try:
                    result = subprocess.run(
                        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "jetson", command],
                        capture_output=True, text=True, errors="replace", timeout=120,
                    )
                    response = {
                        "exit_code": result.returncode,
                        "stdout": result.stdout[:65536],
                        "stderr": result.stderr[:65536],
                    }
                except subprocess.TimeoutExpired as error:
                    response = {
                        "exit_code": 124,
                        "stdout": (error.stdout or b"").decode(errors="replace")[:65536]
                            if isinstance(error.stdout, bytes) else (error.stdout or "")[:65536],
                        "stderr": "Jetson SSH command timed out after 120 seconds.",
                    }
                conn.sendall((json.dumps(response) + "\n").encode())
                continue
            requested_cwd = request.get("cwd") or workspace
            if requested_cwd == "/projects":
                requested_cwd = workspace
            elif requested_cwd.startswith("/projects/"):
                requested_cwd = os.path.join(workspace, requested_cwd[len("/projects/"):])
            cwd = os.path.realpath(requested_cwd)
            if not os.path.isdir(cwd) or not os.access(cwd, os.R_OK | os.X_OK):
                conn.sendall(b"ERROR: working directory must exist and be accessible to the WSL user\n")
                continue
            pid, master = pty.fork()
            if pid == 0:
                os.chdir(cwd)
                os.execvpe("/bin/bash", ["/bin/bash", "--login", "-i"], os.environ.copy())
            child_status = None
            disconnected = False
            try:
                while child_status is None and not disconnected:
                    readable, _, _ = select.select([conn, master], [], [], 0.25)
                    for source in readable:
                        data = source.recv(4096) if source is conn else os.read(master, 4096)
                        if not data:
                            disconnected = source is conn
                            break
                        if source is conn:
                            os.write(master, data)
                        else:
                            conn.sendall(data)
                    waited, child_status = os.waitpid(pid, os.WNOHANG)
                    if not waited:
                        child_status = None
            finally:
                os.close(master)
                if child_status is None:
                    try:
                        os.killpg(pid, signal.SIGTERM)
                        for _ in range(20):
                            waited, child_status = os.waitpid(pid, os.WNOHANG)
                            if waited:
                                break
                            time.sleep(0.05)
                        if child_status is None:
                            os.killpg(pid, signal.SIGKILL)
                        os.waitpid(pid, 0)
                    except (ProcessLookupError, ChildProcessError):
                        pass
        except (ValueError, OSError):
            pass
        finally:
            conn.close()


def client(path: str) -> None:
    import shutil
    import tty
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.connect(path)
    sock.sendall((json.dumps({"cwd": os.getcwd()}) + "\n").encode())
    old = termios.tcgetattr(sys.stdin.fileno()) if sys.stdin.isatty() else None
    try:
        if old:
            tty.setraw(sys.stdin.fileno())
        while True:
            readable, _, _ = select.select([sock, sys.stdin], [], [])
            if sock in readable:
                data = sock.recv(4096)
                if not data:
                    return
                os.write(sys.stdout.fileno(), data)
            if sys.stdin in readable:
                data = os.read(sys.stdin.fileno(), 4096)
                if not data:
                    return
                sock.sendall(data)
    finally:
        if old:
            termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, old)
        sock.close()


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "serve" and len(sys.argv) == 4:
        serve(sys.argv[2], sys.argv[3])
    elif len(sys.argv) >= 2 and sys.argv[1] == "client":
        client("/run/openhands-host/terminal.sock")
    else:
        raise SystemExit("usage: host-terminal.py serve SOCKET WORKSPACE | client")
