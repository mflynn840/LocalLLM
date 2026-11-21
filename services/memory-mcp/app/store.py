import os
import re
import threading
import uuid
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
        json={"model": EMBEDDING_MODEL, "input": text},
        timeout=60,
    )
    response.raise_for_status()
    vector = response.json()["data"][0]["embedding"]
    if len(vector) != DIMENSIONS:
        raise RuntimeError(
            f"Embedding service returned {len(vector)} values; expected {DIMENSIONS}."
        )
    return vector


def save_memory(content: str, scope: str = "project", tags: list[str] | None = None) -> str:
    content = content.strip()
    if not content:
        raise ValueError("Memory content cannot be empty.")
    if len(content) > 5000:
        raise ValueError("Memory content must be 5,000 characters or fewer.")
    scope = _validate_scope(scope)
    if tags is None:
        tags = []
    if len(tags) > 12 or any(len(tag) > 64 for tag in tags):
        raise ValueError("Provide at most 12 tags, each no longer than 64 characters.")

    _ensure_collection()
    memory_id = str(uuid.uuid4())
    client.upsert(
        collection_name=COLLECTION,
        points=[
            models.PointStruct(
                id=memory_id,
                vector=_embed(content),
                payload={
                    "content": content,
                    "scope": scope,
                    "tags": tags,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                },
            )
        ],
        wait=True,
    )
    return memory_id


def search_memories(
    query: str, scope: str | None = None, limit: int = 5
) -> list[dict[str, Any]]:
    query = query.strip()
    if not query:
        raise ValueError("Search query cannot be empty.")
    if len(query) > 2000:
        raise ValueError("Search query must be 2,000 characters or fewer.")
    limit = max(1, min(limit, 10))
    query_filter = None
    if scope is not None:
        query_filter = models.Filter(
            must=[
                models.FieldCondition(
                    key="scope",
                    match=models.MatchValue(value=_validate_scope(scope)),
                )
            ]
        )
    _ensure_collection()
    response = client.query_points(
        collection_name=COLLECTION,
        query=_embed(query),
        query_filter=query_filter,
        limit=limit,
        with_payload=True,
    )
    return [
        {
            "id": str(point.id),
            "score": point.score,
            "content": (point.payload or {}).get("content", ""),
            "scope": (point.payload or {}).get("scope", ""),
            "tags": (point.payload or {}).get("tags", []),
            "created_at": (point.payload or {}).get("created_at", ""),
        }
        for point in response.points
    ]


def forget_memory(memory_id: str) -> None:
    try:
        uuid.UUID(memory_id)
    except ValueError as exc:
        raise ValueError("Memory ID must be a UUID returned by save_memory or search_memories.") from exc
    _ensure_collection()
    client.delete(
        collection_name=COLLECTION,
        points_selector=models.PointIdsList(points=[memory_id]),
        wait=True,
    )
