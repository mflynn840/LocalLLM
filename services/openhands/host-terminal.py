#!/usr/bin/env python3
"""Private Unix-socket PTY bridge. Commands run as the WSL user."""
import json
import os
import pty
import posixpath
import select
import shlex
import socket
import subprocess
import sys
import termios
import signal
import time
import hashlib


MAX_REQUEST_BYTES = 256 * 1024 * 1024
MAX_RESPONSE_BYTES = 256 * 1024 * 1024
SSH_HOSTS_PATH = os.path.join(os.path.dirname(__file__), "../../runtime/openhands-ssh-hosts.json")


def ssh_hosts() -> dict:
    try:
        with open(SSH_HOSTS_PATH, encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}


def invalidate_ssh_map(key: str) -> None:
    cache_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../runtime/ssh-file-maps"))
    cache_path = os.path.join(cache_dir, hashlib.sha256(key.encode()).hexdigest() + ".json")
    try:
        with open(cache_path, encoding="utf-8") as handle:
            cached = json.load(handle)
        cached["stale"] = True
        with open(cache_path + ".tmp", "w", encoding="utf-8") as handle:
            json.dump(cached, handle, ensure_ascii=False)
        os.chmod(cache_path + ".tmp", 0o600)
        os.replace(cache_path + ".tmp", cache_path)
    except (OSError, ValueError):
        pass


def ssh_send(request: dict, conn: socket.socket) -> None:
    operation = request.get("operation")
    hosts = ssh_hosts()
    if operation == "ssh_hosts":
        response = {"exit_code": 0, "hosts": hosts}
    elif operation == "ssh_save_host":
        key, target, port = request.get("key"), request.get("target"), request.get("port", 22)
        if (not isinstance(key, str) or not key or len(key) > 64 or
                not isinstance(target, str) or not target.strip() or len(target) > 255 or
                not isinstance(port, int) or not 1 <= port <= 65535):
            response = {"exit_code": 2, "stderr": "invalid key, target, or port"}
        else:
            hosts[key] = {"target": target, "port": port,
                          "description": str(request.get("description", ""))[:512],
                          "metadata": request.get("metadata", {})}
            os.makedirs(os.path.dirname(SSH_HOSTS_PATH), exist_ok=True)
            tmp = SSH_HOSTS_PATH + ".tmp"
            with open(tmp, "w", encoding="utf-8") as handle:
                json.dump(hosts, handle, ensure_ascii=False, indent=2)
            os.chmod(tmp, 0o600)
            os.replace(tmp, SSH_HOSTS_PATH)
            response = {"exit_code": 0, "key": key, "host": hosts[key]}
    elif operation == "ssh_remove_host":
        key = request.get("key")
        if key not in hosts:
            response = {"exit_code": 2, "stderr": "unknown host key"}
        else:
            del hosts[key]
            with open(SSH_HOSTS_PATH, "w", encoding="utf-8") as handle:
                json.dump(hosts, handle, ensure_ascii=False, indent=2)
            os.chmod(SSH_HOSTS_PATH, 0o600)
            response = {"exit_code": 0, "key": key}
    else:
        key = request.get("host")
        host = hosts.get(key)
        if not host:
            response = {"exit_code": 2, "stderr": f"unknown SSH host key: {key}"}
        else:
            path = request.get("path")
            if operation in ("ssh_read_file", "ssh_write_file") and (not isinstance(path, str) or not path.startswith("/")):
                response = {"exit_code": 2, "stderr": "remote path must be absolute"}
            else:
                target, port = host["target"], str(host.get("port", 22))
                base = ["ssh", "-T", "-p", port, "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", target]
                helper = "/home/michael/AI/jetson/connect_jetson.sh"
                jetson = key == "jetson" and os.path.exists(helper)
                def run(command, **kwargs):
                    if jetson:
                        result = subprocess.run([helper, command], env={**os.environ, "JETSON_TARGET": target}, **kwargs)
                        # The connection helper prints a routine reachability line on
                        # every call. Do not contaminate command output or file reads.
                        output = result.stdout
                        routine_prefixes = (
                            b"Jetson USB network responds at ",
                            b"Jetson USB networking is not attached; asking ",
                            b"Configuring Jetson USB interface ",
                        )
                        if isinstance(output, bytes):
                            output = b"\n".join(line for line in output.split(b"\n") if not line.startswith(routine_prefixes))
                        elif isinstance(output, str):
                            decoded_prefixes = tuple(prefix.decode() for prefix in routine_prefixes)
                            output = "\n".join(line for line in output.split("\n") if not line.startswith(decoded_prefixes))
                        result.stdout = output
                        return result
                    return subprocess.run(base + ["bash", "-lc", command], **kwargs)
                timeout = request.get("timeout_seconds", 3600)
                try:
                    if operation == "ssh_map_files":
                        root = request.get("root")
                        offset, page_size = request.get("offset", 0), request.get("page_size", 80)
                        if (not isinstance(root, str) or not root.startswith("/") or
                                not isinstance(offset, int) or offset < 0 or
                                not isinstance(page_size, int) or not 1 <= page_size <= 100):
                            response = {"exit_code": 2, "stderr": "root must be absolute; offset nonnegative; page_size 1-100"}
                        else:
                            cache_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../runtime/ssh-file-maps"))
                            os.makedirs(cache_dir, exist_ok=True)
                            cache_path = os.path.join(cache_dir, hashlib.sha256(key.encode()).hexdigest() + ".json")
                            cached = None
                            try:
                                with open(cache_path, encoding="utf-8") as handle:
                                    cached = json.load(handle)
                            except (OSError, ValueError):
                                pass
                            if request.get("refresh", True) or not cached or cached.get("root") != root or cached.get("stale", False):
                                find = ("cd -- " + shlex.quote(root) + " && find . -maxdepth 8 -type d "
                                    + "\\( -name .git -o -name venv -o -name .venv -o -name node_modules "
                                    + "-o -name __pycache__ -o -name .pytest_cache -o -name build \\) -prune "
                                    + "-o -type f -printf '%P\\t%s\\t%T@\\n' | head -n 5001")
                                result = run(find, capture_output=True, text=True, errors="replace", timeout=timeout)
                                entries = []
                                for line in result.stdout.splitlines():
                                    fields = line.split("\t", 2)
                                    if len(fields) == 3:
                                        try:
                                            entries.append({"path": fields[0], "size": int(fields[1]), "mtime": float(fields[2])})
                                        except ValueError:
                                            continue
                                cached = {"root": root, "host": key, "updated_at": time.time(), "files": entries, "stale": False,
                                          "truncated": len(entries) >= 5000}
                                with open(cache_path + ".tmp", "w", encoding="utf-8") as handle:
                                    json.dump(cached, handle, ensure_ascii=False)
                                os.chmod(cache_path + ".tmp", 0o600)
                                os.replace(cache_path + ".tmp", cache_path)
                            entries = cached.get("files", [])
                            response = {"exit_code": 0, "root": root, "host": key, "updated_at": cached.get("updated_at"),
                                        "file_count": len(entries), "truncated": cached.get("truncated", False),
                                        "stale": cached.get("stale", False),
                                        "offset": offset, "next_offset": min(offset + page_size, len(entries)),
                                        "files": entries[offset:offset + page_size]}
                    elif operation == "ssh_search_files":
                        root, query = request.get("root"), request.get("query")
                        kind, pattern = request.get("search_kind", "content"), request.get("glob", "*")
                        if not isinstance(root, str) or not root.startswith("/") or not isinstance(query, str) or len(query) > 512:
                            response = {"exit_code": 2, "stderr": "root must be absolute and query at most 512 characters"}
                        elif not isinstance(pattern, str) or len(pattern) > 128 or kind not in {"content", "filename"}:
                            response = {"exit_code": 2, "stderr": "invalid search kind or glob"}
                        else:
                            excludes = " -not -path './.git/*' -not -path './venv/*' -not -path './.venv/*' -not -path './node_modules/*' -not -path './__pycache__/*'"
                            if kind == "filename":
                                command = ("cd -- " + shlex.quote(root) + " && find . -type f " + excludes
                                    + " -print | grep -F -- " + shlex.quote(query) + " | head -n 80")
                            else:
                                command = ("cd -- " + shlex.quote(root) + " && rg -n --no-heading --color never "
                                    + "--glob " + shlex.quote(pattern) + " --glob '!**/venv/**' --glob '!**/.venv/**' "
                                    + "--glob '!**/node_modules/**' --glob '!**/.git/**' -F -- " + shlex.quote(query) + " . | head -n 80")
                            result = run(command, capture_output=True, text=True, errors="replace", timeout=timeout)
                            if kind == "content" and (result.returncode == 127 or "rg: command not found" in result.stderr):
                                fallback = ("cd -- " + shlex.quote(root) + " && grep -R -n -F --exclude-dir=.git "
                                    + "--exclude-dir=venv --exclude-dir=.venv --exclude-dir=node_modules "
                                    + "--exclude-dir=__pycache__ --include=" + shlex.quote(pattern)
                                    + " -- " + shlex.quote(query) + " . | head -n 80")
                                result = run(fallback, capture_output=True, text=True, errors="replace", timeout=timeout)
                            output = result.stdout[-20000:]
                            response = {"exit_code": result.returncode, "results": output,
                                        "truncated": len(result.stdout) > 20000, "stderr": result.stderr[-4000:]}
                    elif operation == "ssh_exec":
                        command = request.get("command")
                        if not isinstance(command, str) or len(command) > 1_000_000:
                            response = {"exit_code": 2, "stderr": "command must be at most 1 MB"}
                        else:
                            result = run(command, capture_output=True, text=True, errors="replace", timeout=timeout)
                            invalidate_ssh_map(key)
                            combined = result.stdout
                            truncated = len(combined) > 20000
                            if truncated:
                                combined = combined[:6000] + "\n...[output truncated]...\n" + combined[-14000:]
                            response = {"exit_code": result.returncode, "stdout": combined, "stderr": result.stderr[-6000:], "truncated": truncated}
                    elif operation == "ssh_read_file":
                        offset, max_bytes = request.get("offset", 0), request.get("max_bytes", 20000)
                        if not isinstance(offset, int) or offset < 0 or not isinstance(max_bytes, int) or not 1 <= max_bytes <= 48000:
                            response = {"exit_code": 2, "stderr": "offset must be nonnegative and max_bytes 1-48000"}
                        else:
                            reader = "import sys; f=open(sys.argv[1],'rb'); f.seek(int(sys.argv[2])); sys.stdout.buffer.write(f.read(int(sys.argv[3])))"
                            command = "python3 -c " + shlex.quote(reader) + " " + shlex.quote(path) + f" {offset} {max_bytes}"
                            result = run(command, capture_output=True, timeout=timeout)
                            content = result.stdout
                            response = {"exit_code": result.returncode, "content": content.decode("utf-8", "replace"),
                                        "offset": offset, "bytes_read": len(content), "next_offset": offset + len(content),
                                        "eof": len(content) < max_bytes,
                                        "stderr": result.stderr.decode("utf-8", "replace")[-4000:]}
                    elif operation == "ssh_write_file":
                        content = request.get("content")
                        if not isinstance(content, str):
                            response = {"exit_code": 2, "stderr": "content must be text"}
                        else:
                            mode = request.get("mode", "0644")
                            if not isinstance(mode, str) or not __import__("re").fullmatch(r"0?[0-7]{3}", mode):
                                response = {"exit_code": 2, "stderr": "mode must be an octal permission such as 0644"}
                            else:
                                parent = posixpath.dirname(path)
                                redirect = ">>" if request.get("append") is True else ">"
                                cmd = "mkdir -p -- " + shlex.quote(parent) + " && cat " + redirect + " " + shlex.quote(path) + " && chmod " + mode + " " + shlex.quote(path)
                                result = run(cmd, input=content.encode("utf-8"), capture_output=True, timeout=timeout)
                                if result.returncode == 0:
                                    invalidate_ssh_map(key)
                                response = {"exit_code": result.returncode, "stderr": result.stderr.decode("utf-8", "replace")[-10000:], "bytes_written": len(content.encode("utf-8"))}
                    else:
                        response = {"exit_code": 2, "stderr": "unknown SSH operation"}
                except subprocess.TimeoutExpired as error:
                    response = {"exit_code": 124, "stderr": f"SSH operation exceeded {timeout} seconds", "stdout": ""}
    encoded = json.dumps(response, ensure_ascii=False).encode() + b"\n"
    if len(encoded) > MAX_RESPONSE_BYTES:
        encoded = json.dumps({"exit_code": 2, "stderr": "response exceeded 256 MiB bridge limit"}).encode() + b"\n"
    conn.sendall(encoded)


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
            header = bytearray()
            while not header.endswith(b"\n") and len(header) <= MAX_REQUEST_BYTES:
                chunk = conn.recv(min(4096, MAX_REQUEST_BYTES + 1 - len(header)))
                if not chunk:
                    break
                header.extend(chunk)
            if len(header) > MAX_REQUEST_BYTES or not header.endswith(b"\n"):
                conn.sendall(b'{"exit_code":2,"stdout":"","stderr":"request too large or missing newline"}\n')
                continue
            request = json.loads(header)
            if str(request.get("operation", "")).startswith("ssh_"):
                ssh_send(request, conn)
                continue
            requested_cwd = request.get("cwd") or workspace
            host_workspace = request.get("host_workspace")
            if isinstance(host_workspace, str) and os.path.isabs(host_workspace):
                host_workspace = os.path.realpath(host_workspace)
                if os.path.isdir(host_workspace) and os.access(host_workspace, os.R_OK | os.X_OK):
                    workspace_for_request = host_workspace
                else:
                    workspace_for_request = workspace
            else:
                workspace_for_request = workspace
            if requested_cwd == "/projects":
                requested_cwd = workspace_for_request
            elif requested_cwd.startswith("/projects/"):
                requested_cwd = os.path.join(workspace_for_request, requested_cwd[len("/projects/"):])
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
            "host_workspace": os.environ.get("OPENHANDS_HOST_WORKSPACE_PATH", ""),
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
