"""Read-only evidence tools for the configured OpenHands workspace."""
from __future__ import annotations

import ast
import hashlib
import json
import math
import os
import re
import subprocess
import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Literal
from uuid import uuid4

import httpx
from mcp.server.fastmcp import FastMCP

ROOT = Path(os.environ.get("REPO_CONTEXT_ROOT", "/projects")).resolve()
EMBEDDINGS = os.environ.get("EMBEDDING_URL", "http://embedding-server:8081/v1") + "/embeddings"
MAX_FILE_BYTES = 128_000
MAX_CHUNKS = 400
WORDS = re.compile(r"[\w.-]+")
SEARCH_TERMS = re.compile(r"[^\W_]+")
STOP_WORDS = {"a", "an", "and", "for", "in", "is", "of", "the", "this", "to", "where", "which"}
DENIED = re.compile(r"(^|/)(?:\.env(?:\..*)?|\.git|\.venv|node_modules|dist|build|secrets?|credentials?|.*\.(?:pem|key|p12))(?:/|$)", re.I)
DOCS = {
    "mcp": ("https://github.com/modelcontextprotocol/python-sdk/releases", "v"),
    "qdrant-client": ("https://github.com/qdrant/qdrant-client/releases", "v"),
    "httpx": ("https://github.com/encode/httpx/releases", ""),
    "fastapi": ("https://github.com/fastapi/fastapi/releases", ""),
}

server = FastMCP("Repository evidence", host="0.0.0.0", port=8000)
worker = ThreadPoolExecutor(max_workers=1)
refresh_lock = threading.Lock()
pending = None
vectors: dict[tuple, list[float]] = {}


def git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-c", f"safe.directory={ROOT}", "-c", "core.fsmonitor=false",
         "-C", str(ROOT), *args],
        capture_output=True, text=True, errors="replace", timeout=15,
    )


def snapshot():
    listing = git("ls-files", "-z", "--cached")
    if listing.returncode:
        raise ValueError("The configured workspace must be a Git checkout.")
    revision = git("rev-parse", "--verify", "HEAD").stdout.strip() or None
    files = {}
    for name in sorted(set(listing.stdout.split("\0")) - {""}):
        if DENIED.search(name) or name.startswith(("runtime/", "models/", "workspace/", "llama.cpp/")):
            continue
        path = ROOT / name
        try:
            if path.resolve() != path or not path.is_file() or path.stat().st_size > MAX_FILE_BYTES:
                continue
            with path.open("rb") as stream:
                raw = stream.read(MAX_FILE_BYTES + 1)
            if len(raw) <= MAX_FILE_BYTES and b"\0" not in raw:
                files[name] = (hashlib.sha256(raw).hexdigest(), raw.decode("utf-8").splitlines())
        except (OSError, UnicodeError):
            continue
    return revision, files


def evidence(name, digest, lines, start, stop, revision):
    return {"path": name, "start_line": start + 1, "end_line": stop,
            "sha256": digest, "head_revision": revision,
            "excerpt": "\n".join(lines[start:stop])[:1800]}


def compose_service_excerpt(name, lines, start, stop):
    """Return exact image and mount lines when a Compose search hits a service."""
    if Path(name).stem not in {"compose", "docker-compose"} or Path(name).suffix not in {".yaml", ".yml"}:
        return None
    service = re.compile(r"^  ([\w.-]+):\s*(?:#.*)?$")
    for index in range(min(stop, len(lines)) - 1, -1, -1):
        match = service.match(lines[index])
        if not match:
            if re.match(r"^\S[^:]*:", lines[index]):
                if lines[index].split(":", 1)[0] != "services":
                    return None
                break
            continue
        section = next((line for line in reversed(lines[:index]) if re.match(r"^\S[^:]*:", line)), "")
        if section.split(":", 1)[0] != "services":
            return None
        fields = [{"line": index + 1, "text": lines[index]}]
        in_mounts = False
        for i in range(index + 1, len(lines)):
            line = lines[i]
            if line.strip() and len(line) - len(line.lstrip()) < 4:
                break
            if line.startswith("    ") and not line.startswith("     "):
                in_mounts = line.strip().startswith("volumes:")
                if in_mounts or line.strip().startswith(("image:", "build:")):
                    fields.append({"line": i + 1, "text": line})
            elif in_mounts and line.strip():
                fields.append({"line": i + 1, "text": line})
            if len(fields) >= 14:
                break
        if len(fields) > 1 and index + 1 <= stop and start < i:
            return fields
        return None
    return None


def chunks(files):
    for name, (digest, lines) in files.items():
        headings = [i for i, line in enumerate(lines) if line.startswith("# ") or line.startswith("## ")]
        if name.endswith((".py", ".ts", ".tsx", ".js")):
            headings = [i for i, line in enumerate(lines)
                        if re.match(r"^\s*(?:(?:export|async) )*(?:def|class|function|interface)\s", line)]
        # Split long sections so the embedding endpoint receives bounded inputs.
        boundaries = sorted(set([0, len(lines), *headings]))
        for first, last in zip(boundaries, boundaries[1:]):
            for start in range(first, last, 18):
                stop = min(last, start + 18)
                yield (name, digest, start, stop), "\n".join(lines[start:stop])


def embed(inputs):
    response = httpx.post(EMBEDDINGS, json={"model": "bge-small-en-v1.5", "input": inputs}, timeout=10)
    response.raise_for_status()
    output = [item["embedding"] for item in sorted(response.json()["data"], key=lambda item: item["index"])]
    if len(output) != len(inputs):
        raise ValueError("Embedding count mismatch")
    return output


def refresh(items):
    global vectors
    updated = {}
    missing = [(key, value) for key, value in items if key not in vectors]
    for offset in range(0, len(missing), 8):
        batch = missing[offset:offset + 8]
        try:
            encoded = embed([f"{key[0]}\n{text}".encode()[:420].decode(errors="ignore") for key, text in batch])
        except (httpx.HTTPError, ValueError, KeyError):
            break
        updated.update(zip((key for key, _ in batch), encoded))
    vectors = {key: updated.get(key, vectors.get(key)) for key, _ in items
               if key in updated or key in vectors}


def similarity(a, b):
    return sum(x * y for x, y in zip(a, b)) / max(math.hypot(*a) * math.hypot(*b), 1e-12)


@server.tool()
def repo_search(
    query: str, limit: int = 5, mode: Literal["auto", "exact", "hybrid"] = "auto",
    scope: Literal["active", "all", "client"] = "active",
) -> dict:
    """Find live repo paths or text. Auto uses exact for single terms, hybrid for phrases.

    On the LocalLLM deployment repo, active searches cover the backend checkout.
    Use client for the separate portable deployment or all for both.
    """
    global pending
    if not 1 <= len(query.strip().strip("`")) <= 300:
        return {"status": "invalid_input", "hint": "Use 1–300 query characters."}
    limit = max(1, min(limit, 10))
    revision, files = snapshot()
    needle = query.strip().strip("`").casefold()
    if "scripts/configure-openhands-defaults.sh" in files and "client/compose.yaml" in files:
        if scope == "client" or needle.startswith("client/"):
            scope = "client"
            files = {name: data for name, data in files.items() if name.startswith("client/")}
        elif scope == "active":
            files = {name: data for name, data in files.items() if not name.startswith("client/")}
    terms = set(SEARCH_TERMS.findall(needle)) - STOP_WORDS
    hybrid = mode == "hybrid" or (mode == "auto" and len(WORDS.findall(needle)) > 1)
    lexical = []
    term_hits = []
    frequencies = Counter()
    path_query = needle
    if needle in {"docker-compose", "docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"}:
        path_query = "compose."
    for name, (digest, lines) in files.items():
        path = Path(name)
        if path_query in name.casefold() and lines:
            lexical.append((11 if path.parent == Path(".") else 10, (name, digest, 0, min(12, len(lines)))))
        for i, line in enumerate(lines):
            if needle in line.casefold():
                score = 7 if path.suffix == ".md" else 8
                if path.suffix in {".yaml", ".yml"} and line.strip().casefold().startswith(needle + ":"):
                    score = 12 if path.parent == Path(".") else 11
                lexical.append((score, (name, digest, max(0, i - 1), min(len(lines), i + 5))))
            elif hybrid and terms:
                matched = terms.intersection(SEARCH_TERMS.findall(line.casefold()))
                if matched:
                    frequencies.update(matched)
                    term_hits.append((matched, (name, digest, max(0, i - 1), min(len(lines), i + 3))))
    for matched, key in term_hits:
        score = sum(1 / math.sqrt(frequencies[term]) for term in matched)
        path = Path(key[0])
        if path.suffix in {".yaml", ".yml", ".toml", ".py", ".sh"}:
            score *= 1.35
            if path.parent == Path("."):
                score += 0.35
        lexical.append((min(5, score), key))
    ranked = []
    per_file = Counter()
    for _, key in sorted(lexical, key=lambda hit: -hit[0]):
        if per_file[key[0]] < 4:
            ranked.append(key)
            per_file[key[0]] += 1
        if len(ranked) >= 60:
            break
    semantic_status = "not_requested"
    if hybrid:
        items = []
        for key, value in chunks(files):
            items.append((key, value))
            if len(items) == MAX_CHUNKS:
                break
        with refresh_lock:
            cached = vectors
            if (pending is None or pending.done()) and any(key not in cached for key, _ in items):
                pending = worker.submit(refresh, items)
        current = {key: cached[key] for key, _ in items if key in cached}
        semantic_status = "warming" if len(current) < len(items) else "ready"
        if current:
            try:
                query_vector = embed(["Represent this sentence for searching relevant passages: " + needle.encode()[:300].decode(errors="ignore")])[0]
                semantic = sorted(current, key=lambda key: -similarity(query_vector, current[key]))[:25]
                scores = {}
                for ranking in (ranked, semantic):
                    for i, key in enumerate(ranking):
                        scores[key] = scores.get(key, 0) + 1 / (20 + i)
                ranked = sorted(scores, key=scores.get, reverse=True)
            except (httpx.HTTPError, ValueError, KeyError):
                semantic_status = "unavailable"
    hits = []
    for name, digest, start, stop in ranked:
        if any(hit["path"] == name and start < hit["end_line"] and stop >= hit["start_line"] for hit in hits):
            continue
        lines = files[name][1]
        hit = evidence(name, digest, lines, start, stop, revision)
        related = compose_service_excerpt(name, lines, start, stop)
        if related:
            hit["compose_service_lines"] = related
        hits.append(hit)
        if len(hits) == limit:
            break
    result = {"status": "ok" if hits else "no_match", "scope": scope,
              "semantic": semantic_status, "hits": hits}
    if not hits:
        result["searched_tracked_files"] = len(files)
        if not hybrid:
            result["hint"] = "This exact text was not found in searchable tracked files. Report this if the question asks whether it exists."
        else:
            result["hint"] = "Try a distinctive identifier or inspect the likely configuration file."
    return result


@server.tool()
def symbol_lookup(name: str, limit: int = 8) -> dict:
    """Locate Python or JS/TS definitions and textual candidate references for an identifier."""
    if not re.fullmatch(r"[\w$]{1,100}", name):
        return {"status": "invalid_input", "hint": "Provide one unqualified identifier."}
    limit = max(1, min(limit, 15))
    revision, files = snapshot()
    identifier = re.compile(rf"(?<![\w$]){re.escape(name)}(?![\w$])")
    definitions, references = [], []
    for path, (digest, lines) in files.items():
        declared = set()
        if path.endswith(".py"):
            try:
                tree = ast.parse("\n".join(lines))
                declared = {node.lineno - 1 for node in ast.walk(tree)
                            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
                            and node.name == name}
            except SyntaxError:
                pass
        elif path.endswith((".js", ".ts", ".jsx", ".tsx")):
            declaration = re.compile(rf"^\s*(?:(?:export|default|async) )*(?:class|function|interface|type|const|let)\s+{re.escape(name)}(?![\w$])")
            declared = {i for i, line in enumerate(lines) if declaration.search(line)}
        for i, line in enumerate(lines):
            if i in declared or identifier.search(line):
                hit = evidence(path, digest, lines, i, min(i + 2, len(lines)), revision)
                (definitions if i in declared else references).append(hit)
    return {"status": "ok" if definitions or references else "no_match",
            "definitions": definitions[:limit], "text_references": references[:limit]}


@server.tool()
def docs_lookup(package: str, version: str | None = None) -> dict:
    """Find local dependency declarations and official releases.

    Pass a verified installed version to retrieve notes for that exact release
    on supported packages when web access is enabled.
    """
    if not re.fullmatch(r"[@\w./-]{1,80}", package):
        return {"status": "invalid_input", "hint": "Provide a package name."}
    if version is not None and not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:[A-Za-z0-9.-]{1,20})?", version):
        return {"status": "invalid_input", "hint": "Use an exact version such as 0.28.1."}
    revision, files = snapshot()
    matches = []
    declaration = re.compile(rf"(?<![\w.-]){re.escape(package)}(?![\w.-])", re.I)
    for path, (digest, lines) in files.items():
        filename = Path(path).name
        if filename not in {"package.json", "package-lock.json", "pyproject.toml", "uv.lock", "poetry.lock"} and not filename.startswith("requirements"):
            continue
        for i, line in enumerate(lines):
            if declaration.search(line):
                matches.append(evidence(path, digest, lines, i, min(i + 3, len(lines)), revision))
    upstream = DOCS.get(package.lower())
    result = {"status": "ok" if matches else "no_match", "declarations": matches[:8],
              "official_releases": upstream[0] if upstream else None}
    if version is None:
        result["hint"] = "Confirm the installed version in the target runtime when the manifest specifies a range."
    elif upstream:
        url = f"{upstream[0]}/tag/{upstream[1]}{version}"
        result["release_url"] = url
        key = os.environ.get("WEB_LOADER_API_KEY")
        if key and os.environ.get("ENABLE_WEB_SEARCH", "true").lower() in {"true", "1", "yes", "on"}:
            try:
                response = httpx.post("http://web-loader:8001/load", json={"urls": [url]},
                                      headers={"Authorization": f"Bearer {key}"}, timeout=10)
                response.raise_for_status()
                page = response.json()[0]
                result["release_notes"] = page["page_content"][:3500]
                result["release_status"] = "retrieved"
            except (httpx.HTTPError, IndexError, KeyError, ValueError):
                result["release_status"] = "unavailable"
        else:
            result["release_status"] = "web_disabled"
    return result


@server.tool()
def check_changes(check: Literal["status", "diff_check", "python_syntax"] = "diff_check") -> dict:
    """Run a read-only Git or Python syntax check and return a receipt with actual results."""
    receipt = uuid4().hex
    try:
        if check == "python_syntax":
            revision, files = snapshot()
            checked, errors = [], []
            for name, (_, lines) in files.items():
                if name.endswith(".py"):
                    checked.append(name)
                    try:
                        compile("\n".join(lines), name, "exec")
                    except SyntaxError as error:
                        errors.append(f"{name}:{error.lineno}: {error.msg}")
            return {"status": "failed" if errors else "ok", "receipt": receipt,
                    "head_revision": revision, "check": check, "exit_code": int(bool(errors)),
                    "checked": checked, "output": "\n".join(errors)[:3000]}
        args = ["status", "--short", "--ignore-submodules=all"] if check == "status" else [
            "diff", "--no-ext-diff", "--no-textconv", "--ignore-submodules=all", "--check", "HEAD", "--"
        ]
        result = git(*args)
    except subprocess.TimeoutExpired:
        return {"status": "timeout", "receipt": receipt, "check": check, "exit_code": None}
    return {"status": "ok" if result.returncode == 0 else "failed", "receipt": receipt,
            "command": ["git", *args], "exit_code": result.returncode,
            "output": (result.stdout + result.stderr)[:3000]}


if __name__ == "__main__":
    server.run(transport="streamable-http")
