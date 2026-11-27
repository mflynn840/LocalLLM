"""Run from the OpenHands container to verify the installed MCP tools."""
import asyncio
import hashlib
import json
import os
import re
import time
from contextlib import AsyncExitStack
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


async def connect(stack, host):
    reader, writer = await stack.enter_async_context(
        streamable_http_client(f"http://{host}:8000/mcp")
    )
    session = await stack.enter_async_context(ClientSession(reader, writer))
    await session.initialize()
    return session


async def call(session, tool, **arguments):
    result = await session.call_tool(tool, arguments)
    assert not result.is_error, result.content
    value = result.structured_content
    if value is None:
        value = result.content[0].text
    if isinstance(value, dict) and set(value) == {"result"}:
        value = value["result"]
    if isinstance(value, str) and value[:1] in ("{", "["):
        return json.loads(value)
    return value


async def main():
    async with AsyncExitStack() as stack:
        repo = await connect(stack, "repo-context-mcp")
        memory = await connect(stack, "memory-mcp")
        edit = await connect(stack, "file-edit-mcp")
        root = Path(os.environ["OPENHANDS_WORKSPACE_PATH"])
        for query in ("QDRANT_COLLECTION", "local-model", "OPENHANDS_WORKSPACE_PATH", "bulk_edit"):
            result = await call(repo, "repo_search", query=query)
            assert result["status"] == "ok"
            for hit in result["hits"]:
                assert hashlib.sha256((root / hit["path"]).read_bytes()).hexdigest() == hit["sha256"]
        for scope, expected_prefix in (("active", False), ("client", True)):
            scoped = await call(repo, "repo_search", query="openhands", scope=scope)
            assert scoped["hits"] and all(hit["path"].startswith("client/") == expected_prefix for hit in scoped["hits"])
        assert (await call(repo, "repo_search", query=uuid4().hex))["status"] == "no_match"
        assert (await call(repo, "repo_search", query="", limit=12))["status"] == "invalid_input"
        capped = await call(repo, "repo_search", query="httpx", limit=200)
        assert 1 <= len(capped["hits"]) <= 10
        lookup = await call(repo, "symbol_lookup", name="save_memory")
        assert lookup["definitions"][0]["path"] == "services/memory-mcp/app/store.py"
        assert lookup["text_references"]
        docs = await call(repo, "docs_lookup", package="mcp")
        assert docs["official_releases"].startswith("https://github.com/modelcontextprotocol/")
        assert docs["declarations"]
        for check in ("diff_check", "python_syntax", "status"):
            result = await call(repo, "check_changes", check=check)
            assert result["receipt"] and result["exit_code"] == 0, result
        print("PASS exact repo search, provenance, unknown identifier, symbols, package docs, 3 check receipts", flush=True)

        deadline = time.monotonic() + 75
        while True:
            result = await call(repo, "repo_search", query="durable project memory", mode="hybrid")
            if result["semantic"] == "ready" or time.monotonic() >= deadline:
                break
            await asyncio.sleep(2)
        assert result["semantic"] == "ready" and result["hits"], result
        print("PASS hybrid search with real local embeddings", flush=True)

        session = "eval-" + uuid4().hex
        path = "README.md"
        digest = hashlib.sha256((root / path).read_bytes()).hexdigest()
        ids = set()
        try:
            arguments = {"content": session, "scope": session, "kind": "repo_fact",
                         "source_path": path, "source_sha256": digest}
            fact = await call(memory, "save_project_memory", **arguments)
            ids.add(fact["id"])
            assert (await call(memory, "save_project_memory", **arguments))["id"] == fact["id"]
            assert (await call(memory, "search_project_memory", query=session, scope=session))[0]["id"] == fact["id"]
            assert not await call(memory, "search_project_memory", query=session, scope=session + "-other")
            decision = await call(memory, "save_project_memory", content="old decision",
                                  scope=session, memory_key="build-command")
            ids.add(decision["id"])
            revised = await call(memory, "save_project_memory", content="new decision",
                                 scope=session, memory_key="build-command")
            assert revised["id"] == decision["id"]
            decisions = await call(memory, "search_project_memory", query="decision", scope=session)
            assert [row["content"] for row in decisions if row["kind"] == "decision"] == ["new decision"]
            checkpoint = await call(memory, "save_project_memory", content="first",
                                    scope=session, kind="checkpoint")
            ids.add(checkpoint["id"])
            updated = await call(memory, "save_project_memory", content="second",
                                 scope=session, kind="checkpoint")
            assert updated["id"] == checkpoint["id"]
            scoped = await call(memory, "search_project_memory", query="second", scope=session)
            assert [row["content"] for row in scoped if row["kind"] == "checkpoint"] == ["second"]
            general = await call(memory, "search_project_memory", query="second")
            assert checkpoint["id"] not in {row["id"] for row in general}
            stale_source = await memory.call_tool("save_project_memory", {**arguments, "source_sha256": "bad"})
            assert stale_source.is_error
        finally:
            for memory_id in ids:
                await call(memory, "forget_project_memory", memory_id=memory_id)
        assert not await call(memory, "search_project_memory", query=session, scope=session)
        print("PASS deduplication, validated sources, task checkpoints, filters, and cleanup", flush=True)

        stale = await call(edit, "replace_text", path=str(root / path), old_text="# Local AI stack",
                           new_text="unexpected change", expected_sha256="0" * 64)
        assert not stale["ok"] and stale["code"] == "stale_hash"
        assert hashlib.sha256((root / path).read_bytes()).hexdigest() == digest
        print("PASS stale edit rejected without a file change", flush=True)
        with TemporaryDirectory(prefix="openhands-mcp-eval-", dir=root) as directory:
            target = Path(directory) / "example.py"
            target.write_text("VALUE = 1\n")
            read = await call(edit, "read_file_lines", path=str(target))
            revision = re.search(r"(?m)^revision: ([a-f0-9]{12})$", read)[1]
            changed = await call(edit, "replace_text", path=str(target), old_text="VALUE = 1",
                                 new_text="VALUE = 2", revision=revision)
            stale_short = await call(edit, "replace_text", path=str(target), old_text="VALUE = 2",
                                     new_text="VALUE = 3", revision=revision)
            assert not stale_short["ok"] and stale_short["code"] == "stale_hash"
            assert changed["ok"] and "-VALUE = 1" in changed["diff"]
            assert target.read_text() == "VALUE = 2\n"
            assert changed["sha256"] == hashlib.sha256(target.read_bytes()).hexdigest()
        print("PASS isolated hash-guarded edit, diff, and updated file verification", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
