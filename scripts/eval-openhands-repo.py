"""Paired OpenHands evaluation against the running local LiteLLM backend."""
import json
import os
import re
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from openhands.sdk import Agent, AgentContext, Conversation, LLM, Tool
from openhands.sdk.conversation.exceptions import ConversationRunError
from openhands.sdk.event import ActionEvent, MessageEvent, ObservationEvent
from openhands.sdk.mcp.config import MCPServer
from openhands.tools.terminal import TerminalTool

CASES = [
    ("runtime-settings", "Which source file sets the OpenHands default LLM profile?",
     r"scripts/configure-openhands-defaults\.sh"),
    ("symbol", "In which path is the Python function resolve_allowed_path defined?",
     r"services/file-edit-mcp/server\.py"),
    ("callers", "Which file imports save_memory from app.store?",
     r"services/memory-mcp/app/mcp_server\.py"),
    ("dependency", "What version range does the memory service declare for httpx?",
     r">=0\.27\s*,\s*<1"),
    ("memory", "What is the default Qdrant collection name for OpenHands durable memory?",
     r"openhands_memories"),
    ("check", "Run a Git whitespace check for tracked changes against HEAD. Report its exit code.",
     r"(?:(?:exit (?:code|status)|exited with (?:code|status))\D{0,8}0|exit_code\W{0,8}0)"),
    ("syntax", "Run a Python syntax check on tracked Python source without importing it. Say whether it passed.",
     r"\b(?:passed|exit code 0|no syntax errors)\b"),
    ("missing", "In which tracked file is the setting " "ZZZ_ORBIT_" "REPO_EVAL_76319 defined? Say if you cannot find it.",
     r"\b(?:not found|not defined|could not find|couldn't find|no (?:match|tracked|definition|file)|does not appear)\b"),
    ("architecture", "Which service provides the embedding API for persistent memory?",
     r"embedding-server"),
    ("workspace", "Which file defines the owner OpenHands container image and mounts?",
     r"(?<!client/)\bcompose\.yaml"),
]


def match_answer(pattern, answer):
    # Treat Markdown typography as presentation. File paths must still match
    # the expected directory and basename.
    plain = answer.translate(str.maketrans({"‐": "-", "‑": "-", "’": "'"}))
    return re.search(pattern, plain, re.I)


def installed_settings():
    key = Path("/home/openhands/.openhands/agent-canvas/api-key.txt").read_text().strip()
    request = urllib.request.Request(
        "http://127.0.0.1:18000/api/settings", headers={"X-Session-API-Key": key},
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.load(response)["agent_settings"]


def main():
    settings = installed_settings()
    configured = settings["mcp_config"]
    all_servers = {
        name: MCPServer.model_validate(configured[name])
        for name in ("local-file-edit", "local-memory", "local-repo-context")
    }
    suffix = settings["agent_context"]["system_message_suffix"]
    baseline_suffix = re.sub(r"<local-repo-evidence>.*?</local-repo-evidence>", "", suffix, flags=re.S)
    workspace = os.environ["OPENHANDS_WORKSPACE_PATH"]
    llm_settings = settings.get("llm") or {}
    temperature = llm_settings.get("temperature")
    if "EVAL_TEMPERATURE" in os.environ:
        temperature = float(os.environ["EVAL_TEMPERATURE"])
    reasoning = os.environ.get("EVAL_REASONING_EFFORT", llm_settings.get("reasoning_effort") or "high")
    default_tools = ["FinishTool"] if os.environ.get("EVAL_FINISH_TOOL", "1") == "1" else []
    outcomes = []
    for name, question, pattern in CASES:
        if len(sys.argv) > 1 and name not in sys.argv[1:]:
            continue
        for variant in ("baseline", "upgraded"):
            events = []
            mcp = dict(all_servers)
            if variant == "baseline":
                mcp.pop("local-repo-context")
            agent = Agent(
                llm=LLM(model=os.environ["LLM_MODEL"], base_url=os.environ["LLM_BASE_URL"],
                        api_key=os.environ["LLM_API_KEY"], num_retries=1, retry_min_wait=1,
                        max_output_tokens=1600, timeout=60,
                        native_tool_calling=os.environ.get("EVAL_NATIVE_TOOLS", "1") == "1",
                        temperature=temperature, reasoning_effort=reasoning),
                tools=[Tool(name=TerminalTool.name)], mcp_config=mcp,
                include_default_tools=default_tools,
                agent_context=AgentContext(system_message_suffix=suffix if variant == "upgraded" else baseline_suffix),
            )
            start = time.monotonic()
            with tempfile.TemporaryDirectory(prefix="repo-agent-eval-") as directory:
                conversation = Conversation(agent, workspace=workspace, persistence_dir=directory,
                                            max_iteration_per_run=6, visualizer=None, callbacks=[events.append])
                error = None
                try:
                    conversation.send_message(
                        question + " Stay inside this checkout. Use read-only tools and give a concise answer with evidence."
                    )
                    conversation.run()
                except ConversationRunError as exc:
                    error = str(exc).split("\n", 1)[0]
                finally:
                    stats = conversation.conversation_stats.model_dump(mode="json")
                    conversation.close()
            messages = [e.llm_message for e in events if isinstance(e, MessageEvent)
                        and e.llm_message.role == "assistant"]
            answer = " ".join(getattr(block, "text", "") for block in messages[-1].content) if messages else ""
            finishes = [e.action for e in events if isinstance(e, ActionEvent) and e.tool_name == "finish"]
            if finishes and getattr(finishes[-1], "message", None):
                answer = finishes[-1].message
            actions = [e for e in events if isinstance(e, ActionEvent) and e.tool_name != "finish"]
            observations = [e for e in events if isinstance(e, ObservationEvent) and e.tool_name != "finish"]
            if os.environ.get("EVAL_TRACE") == "1":
                for event in [*actions, *observations]:
                    data = event.action if isinstance(event, ActionEvent) else event.observation
                    print(f"TRACE {variant} {event.tool_name} {str(data)[:700]}", flush=True)
            errors = [getattr(e.observation, "is_error", False) for e in observations]
            passed = bool(not error and actions and observations and match_answer(pattern, answer))
            evidence = "\n".join(str(e.observation) for e in observations)
            if name == "check":
                passed = passed and ("diff --check" in evidence or "diff" in evidence and "--check" in evidence)
            if name == "syntax":
                passed = passed and any(word in evidence for word in ("python_syntax", "compile", "ast.parse", "syntax"))
            result = {"case": name, "variant": variant, "pass": passed,
                      "tools": [e.tool_name for e in actions], "tool_errors": sum(bool(e) for e in errors),
                      "seconds": round(time.monotonic() - start, 2), "stats": stats, "answer": answer,
                      "error": error}
            outcomes.append(result)
            print("EVAL " + json.dumps(result), flush=True)
    print("SUMMARY " + json.dumps({
        variant: {"passed": sum(row["pass"] for row in outcomes if row["variant"] == variant),
                  "total": sum(row["variant"] == variant for row in outcomes)}
        for variant in ("baseline", "upgraded")
    }), flush=True)


if __name__ == "__main__":
    main()
