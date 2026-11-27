import hashlib
import os
import re
import threading
import uuid
from pathlib import Path
from datetime import datetime, timezone
from typing import Any

import httpx
from qdrant_client import QdrantClient
from qdrant_client.http import models

COLLECTION = os.environ.get("QDRANT_COLLECTION", "openhands_memories")
DIMENSIONS = int(os.environ.get("EMBEDDING_DIMENSIONS", "384"))
EMBEDDING_URL = os.environ.get(
    "EMBEDDING_URL", "http://embedding-server:8081/v1"
).rstrip("/")
EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "bge-small-en-v1.5")
client = QdrantClient(url=os.environ.get("QDRANT_URL", "http://qdrant:6333"))
scope_pattern = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
PROJECT = hashlib.sha256(os.environ.get("MEMORY_PROJECT", "./workspace").encode()).hexdigest()
WORKSPACE = Path(os.environ.get("MEMORY_WORKSPACE", "/projects")).resolve()
KINDS = {"decision", "preference", "repo_fact", "checkpoint"}
collection_lock = threading.Lock()


def _validate_scope(scope: str) -> str:
    if not scope_pattern.fullmatch(scope):
        raise ValueError("Scope must contain 1-64 letters, digits, dots, underscores, or hyphens.")
    return scope


def _ensure_collection() -> None:
    with collection_lock:
        if not client.collection_exists(COLLECTION):
            client.create_collection(
                collection_name=COLLECTION,
                vectors_config=models.VectorParams(
                    size=DIMENSIONS,
                    distance=models.Distance.COSINE,
                ),
            )


def _embed(text: str) -> list[float]:
    response = httpx.post(
        f"{EMBEDDING_URL}/embeddings",
        json={"model": EMBEDDING_MODEL, "input": text.encode()[:300].decode(errors="ignore")},
        timeout=60,
    )
    response.raise_for_status()
    vector = response.json()["data"][0]["embedding"]
    if len(vector) != DIMENSIONS:
        raise RuntimeError(
            f"Embedding service returned {len(vector)} values; expected {DIMENSIONS}."
        )
    return vector


def source_hash(source_path: str) -> str | None:
    relative = Path(source_path)
    if relative.is_absolute() or not relative.parts or any(
        part == ".." or part.startswith(".env") or part in {".git", "secrets", "credentials"}
        or Path(part).suffix.lower() in {".pem", ".key", ".p12"}
        for part in relative.parts
    ):
        return None
    path = WORKSPACE / relative
    try:
        if path.resolve() != path or not path.is_file():
            return None
        with path.open("rb") as stream:
            data = stream.read(2_000_001)
        return hashlib.sha256(data).hexdigest() if len(data) <= 2_000_000 else None
    except OSError:
        return None


def project_filter(*conditions) -> models.Filter:
    return models.Filter(must=[
        models.FieldCondition(key="project_id", match=models.MatchValue(value=PROJECT)),
        *conditions,
    ])


def save_memory(
    content: str, scope: str = "project", tags: list[str] | None = None, *,
    kind: str = "decision", source_path: str | None = None,
    source_sha256: str | None = None, evidence: str | None = None,
    memory_key: str | None = None,
) -> str:
    content = content.strip()
    if not content or len(content) > 5000:
        raise ValueError("Provide 1–5,000 characters of memory content.")
    scope = _validate_scope(scope)
    tags = tags or []
    if len(tags) > 12 or any(len(tag) > 64 for tag in tags):
        raise ValueError("Provide at most 12 tags, each no longer than 64 characters.")
    if kind not in KINDS:
        raise ValueError("Unknown memory kind.")
    if memory_key is not None:
        _validate_scope(memory_key)
    if kind == "checkpoint" and scope == "project":
        raise ValueError("Give checkpoints a named task scope.")
    if evidence and len(evidence) > 500:
        raise ValueError("Evidence must be at most 500 characters.")
    if kind == "repo_fact" or source_path is not None or source_sha256 is not None:
        if not source_path or not source_sha256 or source_hash(source_path) != source_sha256:
            raise ValueError("Provide a current workspace-relative source path and SHA-256 from a repo tool.")

    _ensure_collection()
    # A checkpoint replaces the previous checkpoint for the task. Repeated durable saves deduplicate.
    identity = (scope, kind, None if kind == "checkpoint" else (content, source_path))
    if memory_key is not None and kind != "checkpoint":
        identity = (scope, kind, "key", memory_key)
    memory_id = str(uuid.uuid5(uuid.NAMESPACE_URL, repr((PROJECT, identity))))
    now = datetime.now(timezone.utc)
    payload = {
        "project_id": PROJECT, "scope": scope, "kind": kind, "content": content,
        "tags": tags, "created_at": now.isoformat(), "evidence": evidence,
        "source_path": source_path, "source_sha256": source_sha256, "memory_key": memory_key,
    }
    if kind == "checkpoint":
        payload["expires_at"] = int(now.timestamp()) + 7 * 86400
    client.upsert(
        collection_name=COLLECTION,
        points=[models.PointStruct(id=memory_id, vector=_embed(content), payload=payload)],
        wait=True,
    )
    return memory_id


def search_memories(query: str, scope: str | None = None, limit: int = 5) -> list[dict[str, Any]]:
    query = query.strip()
    if not query or len(query) > 2000:
        raise ValueError("Provide 1–2,000 query characters.")
    limit = max(1, min(limit, 10))
    query_filter = project_filter()
    if scope is not None:
        query_filter.must.append(models.FieldCondition(
            key="scope", match=models.MatchValue(value=_validate_scope(scope)),
        ))
    query_filter.must_not = [models.FieldCondition(
        key="expires_at", range=models.Range(lte=int(datetime.now(timezone.utc).timestamp())),
    )]
    if scope in (None, "project"):
        query_filter.must_not.append(models.FieldCondition(
            key="kind", match=models.MatchValue(value="checkpoint"),
        ))
    _ensure_collection()
    response = client.query_points(
        collection_name=COLLECTION, query=_embed(query), query_filter=query_filter,
        limit=min(30, limit * 3), with_payload=True,
    )
    results = []
    for point in response.points:
        payload = dict(point.payload or {})
        if payload.get("source_path") and source_hash(payload["source_path"]) != payload["source_sha256"]:
            continue
        payload.pop("project_id", None)
        results.append({"id": str(point.id), "score": point.score, **payload})
        if len(results) == limit:
            break
    return results


def forget_memory(memory_id: str) -> None:
    try:
        memory_id = str(uuid.UUID(memory_id))
    except ValueError as exc:
        raise ValueError("Memory ID must be a UUID returned by a memory tool.") from exc
    _ensure_collection()
    client.delete(
        collection_name=COLLECTION,
        points_selector=models.FilterSelector(filter=project_filter(models.HasIdCondition(has_id=[memory_id]))),
        wait=True,
    )
