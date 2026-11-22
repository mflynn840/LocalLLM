"""Line-oriented, hash-guarded file editing tools for OpenHands."""

from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
import stat
import tempfile
from difflib import SequenceMatcher
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel


MAX_FILE_BYTES = 2_000_000
MAX_READ_LINES = 300
ALLOWED_ROOTS = (Path("/projects"), Path("/host"))

server = FastMCP("Safe file editing", host="0.0.0.0", port=8000)


class FileTextEdit(BaseModel):
    path: str
    old_text: str
    new_text: str
    expected_sha256: str
    ignore_whitespace: bool = False
    replace_all: bool = False


def find_text_spans(text: str, old_text: str, ignore_whitespace: bool) -> list[tuple[int, int]]:
    if not ignore_whitespace:
        spans = []
        offset = 0
        while (index := text.find(old_text, offset)) >= 0:
            spans.append((index, index + len(old_text)))
            offset = index + len(old_text)
        return spans

    source_lines = text.splitlines(keepends=True)
    query_lines = old_text.splitlines(keepends=True)
    if not query_lines:
        return []

    def normalize(line: str) -> str:
        return re.sub(r"[ \t]+", " ", line.rstrip("\r\n").strip())

    query = [normalize(line) for line in query_lines]
    source = [normalize(line) for line in source_lines]
    offsets = []
    total = 0
    for line in source_lines:
        offsets.append(total)
        total += len(line)
    spans = []
    for start in range(len(source) - len(query) + 1):
        if source[start : start + len(query)] != query:
            continue
        last = start + len(query) - 1
        end = offsets[last] + len(source_lines[last].rstrip("\r\n"))
        if old_text.endswith(("\n", "\r")):
            end += len(source_lines[last]) - len(source_lines[last].rstrip("\r\n"))
        spans.append((offsets[start], end))
    return spans


def nearest_lines(text: str, old_text: str, limit: int = 3) -> list[dict[str, object]]:
    query = re.sub(r"\s+", " ", old_text).strip()
    lines = text.splitlines()
    window_size = max(1, len(old_text.splitlines()))
    candidates = []
    for index in range(max(1, len(lines) - window_size + 1)):
        excerpt = "\n".join(lines[index : index + window_size])
        score = SequenceMatcher(None, query, re.sub(r"\s+", " ", excerpt).strip()).ratio()
        if score >= 0.25:
            candidates.append((score, index + 1, excerpt[:400]))
    candidates.sort(reverse=True)
    return [{"start_line": number, "text": excerpt} for _, number, excerpt in candidates[:limit]]


def replace_spans(
    text: str,
    spans: list[tuple[int, int]],
    replacement: str,
    preserve_final_newline: bool = False,
) -> str:
    for start, end in reversed(spans):
        existing = text[start:end]
        value = replacement
        if preserve_final_newline and not value.endswith(("\n", "\r")):
            ending = re.search(r"(\r\n|\n|\r)$", existing)
            if ending:
                value += ending.group(1)
        text = text[:start] + value + text[end:]
    return text


def write_atomic(target: Path, data: bytes, mode: int) -> None:
    fd, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    try:
        with os.fdopen(fd, "wb") as temporary:
            temporary.write(data)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.chmod(temporary_name, mode)
        os.replace(temporary_name, target)
    except Exception:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def resolve_allowed_path(raw_path: str) -> Path:
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        path = Path("/projects") / path
    resolved = path.resolve(strict=False)
    roots = tuple(root.resolve(strict=False) for root in ALLOWED_ROOTS)
    if not any(resolved == root or root in resolved.parents for root in roots):
        raise ValueError("Path must be inside /projects or /host.")
    return resolved


def load_text(path: Path) -> tuple[bytes, str, str]:
    raw = path.read_bytes()
    if len(raw) > MAX_FILE_BYTES:
        raise ValueError(f"File is larger than the {MAX_FILE_BYTES}-byte edit limit.")
    text = raw.decode("utf-8")
    newline = "\r\n" if b"\r\n" in raw else "\n"
    return raw, text, newline


@server.tool()
def read_file_lines(path: str, start_line: int = 1, max_lines: int = 120) -> str:
    """Read a bounded, line-numbered text range before editing.

    Returns a SHA-256 revision token. Use that token with replace_file_lines so
    edits fail safely if another change happened after the read.
    """
    target = resolve_allowed_path(path)
    if not target.is_file():
        raise ValueError(f"Not an existing regular file: {target}")
    if start_line < 1:
        raise ValueError("start_line must be 1 or greater.")
    if not 1 <= max_lines <= MAX_READ_LINES:
        raise ValueError(f"max_lines must be between 1 and {MAX_READ_LINES}.")
    raw, text, _ = load_text(target)
    lines = text.splitlines()
    first = start_line - 1
    selected = lines[first : first + max_lines]
    numbered = "\n".join(f"{first + i + 1}: {line}" for i, line in enumerate(selected))
    end_line = first + len(selected)
    return (
        f"path: {target}\nsha256: {hashlib.sha256(raw).hexdigest()}\n"
        f"total_lines: {len(lines)}\nshowing: {start_line}-{end_line}\n{numbered}"
    )


@server.tool()
def search_files(root: str, query: str, pattern: str = "**/*", max_results: int = 100) -> str:
    """Find literal text in files and return paths, hashes, counts, and line excerpts.

    Use the returned hashes with bulk_edit for a reviewed, multi-file change.
    Large/generated directories and files over the normal edit limit are skipped.
    """
    if not query:
        raise ValueError("query must not be empty.")
    if not 1 <= max_results <= 200:
        raise ValueError("max_results must be between 1 and 200.")
    base = resolve_allowed_path(root)
    if not base.is_dir():
        raise ValueError(f"Not a directory: {base}")
    excluded = {".git", ".venv", "venv", "node_modules", "__pycache__", "models", "runtime", "dist", "build"}
    found = []
    scanned = 0
    for current, dirs, files in os.walk(base, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d not in excluded and not (Path(current) / d).is_symlink())
        for name in sorted(files):
            candidate = Path(current) / name
            rel = candidate.relative_to(base)
            if not rel.match(pattern):
                continue
            scanned += 1
            if scanned > 10000:
                return json.dumps({"results": found, "truncated": True, "error": "Scan limit of 10,000 files reached; narrow the pattern."}, ensure_ascii=False)
            try:
                raw = candidate.read_bytes()
                if len(raw) > MAX_FILE_BYTES or b"\0" in raw:
                    continue
                text = raw.decode("utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            count = text.count(query)
            if not count:
                continue
            line_numbers = []
            for index, line in enumerate(text.splitlines(), start=1):
                if query in line and len(line_numbers) < 5:
                    line_numbers.append({"line": index, "text": line[:240]})
            found.append({
                "path": str(candidate),
                "sha256": hashlib.sha256(raw).hexdigest(),
                "matches": count,
                "lines": line_numbers,
            })
            if len(found) >= max_results:
                return json.dumps({"results": found, "truncated": True, "scanned_files": scanned}, ensure_ascii=False)
    return json.dumps({"results": found, "truncated": False, "scanned_files": scanned}, ensure_ascii=False)


@server.tool()
def replace_file_lines(
    path: str,
    start_line: int,
    end_line: int,
    replacement: str,
    expected_sha256: str,
) -> str:
    """Replace a 1-based inclusive line range using the hash from read_file_lines.

    Set end_line to start_line - 1 to insert at that location. The file must
    already exist. Returns the exact diff and the new hash; stale hashes do not
    modify the file.
    """
    target = resolve_allowed_path(path)
    if not target.is_file():
        raise ValueError(f"Not an existing regular file: {target}")
    raw, text, newline = load_text(target)
    old_hash = hashlib.sha256(raw).hexdigest()
    if old_hash != expected_sha256:
        return json.dumps(
            {
                "ok": False,
                "error": "File changed since it was read; reread it and retry with the new sha256.",
                "current_sha256": old_hash,
            }
        )

    lines = text.splitlines()
    if not 1 <= start_line <= len(lines) + 1:
        raise ValueError(f"start_line must be between 1 and {len(lines) + 1}.")
    if not start_line - 1 <= end_line <= len(lines):
        raise ValueError(f"end_line must be between {start_line - 1} and {len(lines)}.")

    replacement_lines = replacement.splitlines()
    updated_lines = lines[: start_line - 1] + replacement_lines + lines[end_line:]
    updated = newline.join(updated_lines)
    if text.endswith(("\n", "\r")) and updated_lines:
        updated += newline
    updated_bytes = updated.encode("utf-8")
    if len(updated_bytes) > MAX_FILE_BYTES:
        raise ValueError(f"Updated file would exceed the {MAX_FILE_BYTES}-byte edit limit.")

    before = text.splitlines(keepends=True)
    after = updated.splitlines(keepends=True)
    diff = list(
        difflib.unified_diff(
            before,
            after,
            fromfile=str(target),
            tofile=str(target),
            n=3,
        )
    )
    mode = stat.S_IMODE(target.stat().st_mode)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    try:
        with os.fdopen(fd, "wb") as temporary:
            temporary.write(updated_bytes)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.chmod(temporary_name, mode)
        os.replace(temporary_name, target)
    except Exception:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise

    return json.dumps(
        {
            "ok": True,
            "path": str(target),
            "sha256": hashlib.sha256(updated_bytes).hexdigest(),
            "diff": "".join(diff)[:24000],
        },
        ensure_ascii=False,
    )


@server.tool()
def replace_text(
    path: str,
    old_text: str,
    new_text: str,
    expected_sha256: str,
    ignore_whitespace: bool = False,
    replace_all: bool = False,
) -> str:
    """Replace exact or whitespace-tolerant text from read_file_lines output.

    By default exactly one match is required. Set ignore_whitespace to tolerate
    indentation, tabs, trailing spaces, and line-ending differences. Set
    replace_all for deliberate repeated replacements. Failures include nearby
    candidate lines when possible. A stale file hash never changes the file.
    """
    if not old_text:
        raise ValueError("old_text must not be empty.")
    target = resolve_allowed_path(path)
    if not target.is_file():
        raise ValueError(f"Not an existing regular file: {target}")
    raw, text, _ = load_text(target)
    old_hash = hashlib.sha256(raw).hexdigest()
    if old_hash != expected_sha256:
        return json.dumps({"ok": False, "error": "File changed since it was read; reread and retry.", "current_sha256": old_hash})
    spans = find_text_spans(text, old_text, ignore_whitespace)
    if not spans:
        return json.dumps({
            "ok": False,
            "error": "No match found. Check indentation, tabs, line endings, and nearby text; or reread the file.",
            "closest_lines": nearest_lines(text, old_text),
        }, ensure_ascii=False)
    if len(spans) > 1 and not replace_all:
        return json.dumps({
            "ok": False,
            "error": f"Found {len(spans)} matches; add more surrounding text or set replace_all=true.",
            "match_count": len(spans),
        })

    selected = spans if replace_all else spans[:1]
    updated = replace_spans(
        text,
        selected,
        new_text,
        preserve_final_newline=ignore_whitespace and old_text.endswith(("\n", "\r")),
    )
    updated_bytes = updated.encode("utf-8")
    if len(updated_bytes) > MAX_FILE_BYTES:
        raise ValueError(f"Updated file would exceed the {MAX_FILE_BYTES}-byte edit limit.")
    diff = list(difflib.unified_diff(
        text.splitlines(keepends=True), updated.splitlines(keepends=True),
        fromfile=str(target), tofile=str(target), n=3,
    ))
    mode = stat.S_IMODE(target.stat().st_mode)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    try:
        with os.fdopen(fd, "wb") as temporary:
            temporary.write(updated_bytes)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.chmod(temporary_name, mode)
        os.replace(temporary_name, target)
    except Exception:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise
    return json.dumps({
        "ok": True,
        "path": str(target),
        "sha256": hashlib.sha256(updated_bytes).hexdigest(),
        "diff": "".join(diff)[:24000],
        "matches_replaced": len(selected),
    }, ensure_ascii=False)


@server.tool()
def bulk_edit(edits: list[FileTextEdit]) -> str:
    """Apply hash-guarded text replacements across up to 50 files.

    Every edit is validated and staged before any file is changed. Each path
    must be unique in the request. Set replace_all per item for deliberate
    repeated matches in that file; ignore_whitespace is available per item.
    """
    if not edits:
        raise ValueError("Provide at least one edit.")
    if len(edits) > 50:
        raise ValueError("A bulk_edit request can include at most 50 files.")

    prepared = []
    seen = set()
    total_size = 0
    for edit in edits:
        target = resolve_allowed_path(edit.path)
        if target in seen:
            raise ValueError(f"Each path may appear only once in bulk_edit: {target}")
        seen.add(target)
        if not target.is_file():
            raise ValueError(f"Not an existing regular file: {target}")
        raw, text, _ = load_text(target)
        total_size += len(raw)
        if total_size > 20_000_000:
            raise ValueError("Combined bulk edit input exceeds 20 MB.")
        actual_hash = hashlib.sha256(raw).hexdigest()
        if actual_hash != edit.expected_sha256:
            return json.dumps({
                "ok": False,
                "path": str(target),
                "error": "File changed since it was read; reread and retry the bulk edit.",
                "current_sha256": actual_hash,
            })
        spans = find_text_spans(text, edit.old_text, edit.ignore_whitespace)
        if not spans:
            return json.dumps({
                "ok": False,
                "path": str(target),
                "error": "No match found; inspect the closest lines or reread the file.",
                "closest_lines": nearest_lines(text, edit.old_text),
            }, ensure_ascii=False)
        if len(spans) > 1 and not edit.replace_all:
            return json.dumps({
                "ok": False,
                "path": str(target),
                "error": f"Found {len(spans)} matches; set replace_all=true or make old_text more specific.",
                "match_count": len(spans),
            })
        selected = spans if edit.replace_all else spans[:1]
        updated = replace_spans(
            text,
            selected,
            edit.new_text,
            preserve_final_newline=edit.ignore_whitespace and edit.old_text.endswith(("\n", "\r")),
        )
        updated_bytes = updated.encode("utf-8")
        if len(updated_bytes) > MAX_FILE_BYTES:
            raise ValueError(f"Updated file would exceed the {MAX_FILE_BYTES}-byte edit limit: {target}")
        diff = "".join(difflib.unified_diff(
            text.splitlines(keepends=True), updated.splitlines(keepends=True),
            fromfile=str(target), tofile=str(target), n=3,
        ))[:24000]
        prepared.append({
            "path": target,
            "original": raw,
            "mode": stat.S_IMODE(target.stat().st_mode),
            "updated": updated_bytes,
            "sha256": hashlib.sha256(updated_bytes).hexdigest(),
            "diff": diff,
            "matches_replaced": len(selected),
        })

    staged = []
    committed = []
    try:
        for item in prepared:
            fd, name = tempfile.mkstemp(prefix=f".{item['path'].name}.", dir=item['path'].parent)
            staged.append((item, name))
            with os.fdopen(fd, "wb") as output:
                output.write(item["updated"])
                output.flush()
                os.fsync(output.fileno())
            os.chmod(name, item["mode"])
        for item, _ in staged:
            current = item["path"].read_bytes()
            if hashlib.sha256(current).hexdigest() != hashlib.sha256(item["original"]).hexdigest():
                raise RuntimeError(f"File changed during bulk edit; no files were committed: {item['path']}")
        for item, name in staged:
            os.replace(name, item["path"])
            committed.append(item)
    except Exception:
        for item in reversed(committed):
            write_atomic(item["path"], item["original"], item["mode"])
        raise
    finally:
        for _, name in staged:
            try:
                os.unlink(name)
            except FileNotFoundError:
                pass

    return json.dumps({
        "ok": True,
        "files_changed": len(prepared),
        "results": [{
            "path": str(item["path"]),
            "sha256": item["sha256"],
            "matches_replaced": item["matches_replaced"],
            "diff": item["diff"],
        } for item in prepared],
    }, ensure_ascii=False)


@server.tool()
def create_file(path: str, content: str) -> str:
    """Create a new UTF-8 text file; refuses to overwrite an existing file.

    For existing files, use read_file_lines followed by replace_file_lines.
    Parent directories must already exist.
    """
    target = resolve_allowed_path(path)
    if not target.parent.is_dir():
        raise ValueError(f"Parent directory does not exist: {target.parent}")
    encoded = content.encode("utf-8")
    if len(encoded) > MAX_FILE_BYTES:
        raise ValueError(f"File is larger than the {MAX_FILE_BYTES}-byte edit limit.")
    try:
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    except FileExistsError:
        raise ValueError(
            f"File already exists: {target}. Read it and use replace_file_lines to edit it."
        ) from None
    with os.fdopen(fd, "wb") as created:
        created.write(encoded)
        created.flush()
        os.fsync(created.fileno())
    return json.dumps({"ok": True, "created": str(target), "bytes": len(encoded)})


if __name__ == "__main__":
    server.run(transport="streamable-http")
