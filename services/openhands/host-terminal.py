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
                    jetson_env = os.environ.copy()
                    jetson_env["JETSON_TARGET"] = "jetson"
                    result = subprocess.run(
                        ["/home/michael/AI/jetson/connect_jetson.sh", command],
                        capture_output=True, text=True, errors="replace", timeout=150,
                        env=jetson_env,
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
                        "stderr": "Jetson preparation or SSH command timed out after 150 seconds.",
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
                shell_env = os.environ.copy()
                client_env = request.get("env") or {}
                if isinstance(client_env, dict):
                    for key in ("PS1", "PS2", "TERM"):
                        value = client_env.get(key)
                        if isinstance(value, str) and len(value) <= 512:
                            shell_env[key] = value
                os.execvpe("/bin/bash", ["/bin/bash", "--login", "-i"], shell_env)
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
                            # OpenHands' subprocess terminal sends Enter as CR.
                            # Forward it as a canonical line break to the host
                            # PTY so commands submitted through this bridge run.
                            os.write(master, data.replace(b"\r", b"\n"))
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
    import tty
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    old = termios.tcgetattr(sys.stdin.fileno()) if sys.stdin.isatty() else None
    try:
        if old:
            tty.setraw(sys.stdin.fileno(), when=termios.TCSANOW)
        # Set raw mode before announcing the connection. TerminalExecutor may
        # write its initial shell handshake as soon as Popen returns; setting
        # raw mode afterwards uses TCSAFLUSH and can discard that first input.
        sock.connect(path)
        sock.sendall((json.dumps({
            "cwd": os.getcwd(),
            # TerminalExecutor launches this client with its prompt marker in PS1.
            # Relay the harmless terminal settings so the host shell passes its
            # readiness handshake and subsequent command-boundary detection.
            "env": {key: os.environ.get(key, "") for key in ("PS1", "PS2", "TERM")},
        }) + "\n").encode())
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
