#!/usr/bin/env python3
"""Private Unix-socket PTY bridge. Commands run as the WSL user."""
import json
import os
import pty
import select
import socket
import struct
import subprocess
import sys
import termios
import fcntl


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
            cwd = os.path.realpath(request.get("cwd") or workspace)
            if not os.path.isdir(cwd) or not os.access(cwd, os.R_OK | os.X_OK):
                conn.sendall(b"ERROR: working directory must exist and be accessible to the WSL user\n")
                continue
            master, slave = pty.openpty()
            proc = subprocess.Popen(["/bin/bash", "-i"], cwd=cwd, stdin=slave, stdout=slave,
                                    stderr=slave, close_fds=True, start_new_session=True)
            os.close(slave)
            try:
                while proc.poll() is None:
                    readable, _, _ = select.select([conn, master], [], [], 0.25)
                    for source in readable:
                        data = source.recv(4096) if source is conn else os.read(master, 4096)
                        if not data:
                            proc.terminate()
                            break
                        if source is conn:
                            os.write(master, data)
                        else:
                            conn.sendall(data)
                if proc.poll() is None:
                    proc.wait(timeout=2)
            finally:
                os.close(master)
                if proc.poll() is None:
                    proc.kill()
                    proc.wait()
        except (ValueError, json.JSONDecodeError, OSError, struct.error):
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
    elif len(sys.argv) == 2 and sys.argv[1] == "client":
        client("/run/openhands-host/terminal.sock")
    else:
        raise SystemExit("usage: host-terminal.py serve SOCKET WORKSPACE | client")
