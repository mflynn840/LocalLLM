"""Verify the running Canvas agent and its default profile on the owner checkout."""
import hashlib
import json
import os
import re
import runpy
import shlex
import sys
import tempfile
import time
import urllib.error
import urllib.request
from contextlib import nullcontext
from pathlib import Path


ROOT = Path(os.environ["OPENHANDS_WORKSPACE_PATH"]).resolve()
KEY = Path("/home/openhands/.openhands/agent-canvas/api-key.txt").read_text().strip()
CASE_SUITE = runpy.run_path(str(ROOT / "scripts/eval-openhands-repo.py"))
CASES = CASE_SUITE["CASES"]
match_answer = CASE_SUITE["match_answer"]


def request(path, method="GET", data=None, extra_headers=None, canvas_proxy=False):
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(
        f"http://127.0.0.1:{'8000/api' if canvas_proxy else '18000/api'}{path}", data=body, method=method,
        headers={"X-Session-API-Key": KEY, "Content-Type": "application/json", **(extra_headers or {})},
    )
    try:
        with urllib.request.urlopen(req, timeout=12) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"Canvas {method} {path}: HTTP {exc.code}") from exc


def chat(question, tags=None, wsl_mode=False, timeout_seconds=95):
    settings = request("/settings", extra_headers={"X-Expose-Secrets": "encrypted"})
    payload = {
        "agent_settings": settings["agent_settings"],
        "secrets_encrypted": True,
        "workspace": {"working_dir": str(ROOT)},
        "max_iterations": 6,
        "autotitle": False,
        "tags": tags or {"purpose": "local-repo-verification"},
        "initial_message": {"role": "user", "content": [{"type": "text", "text": question}]},
    }
    conversation = request(
        "/conversations", "POST", payload,
        extra_headers={"Cookie": "openhands_terminal=wsl"} if wsl_mode else None,
        canvas_proxy=wsl_mode,
    )
    path = "/conversations/" + conversation["id"]
    try:
        assert Path(conversation["workspace"]["working_dir"]) == ROOT
        start = time.monotonic()
        while time.monotonic() - start < timeout_seconds:
            status = request(path)["execution_status"]
            if status in {"finished", "error", "stuck", "paused", "waiting_for_confirmation"}:
                break
            time.sleep(0.4)
        else:
            request(path + "/interrupt", "POST")
        response = request(path + "/agent_final_response")["response"]
        raw = request(path + "/events/search?limit=100")
        events = raw.get("items", raw.get("events", [])) if isinstance(raw, dict) else raw
        if not response:
            messages = [event for event in events if event.get("source") == "agent"
                        and event.get("kind") == "MessageEvent"]
            if messages:
                response = " ".join(item.get("text", "") for item in messages[-1]["llm_message"]["content"])
        actions = [event for event in events if event.get("kind") == "ActionEvent"]
        observations = [event for event in events if event.get("kind") == "ObservationEvent"]
        return {"status": status, "answer": response or "", "actions": actions,
                "observations": observations, "seconds": round(time.monotonic() - start, 2)}
    finally:
        request(path, "DELETE")


def summarize(name, result, passed):
    summary = {
        "case": name, "pass": passed, "status": result["status"], "seconds": result["seconds"],
        "tools": [event.get("tool_name") for event in result["actions"]],
        "answer": result["answer"],
    }
    if os.environ.get("EVAL_TRACE") == "1":
        summary["trace"] = [
            {"tool": item.get("tool_name"), "data": json.dumps(item.get("action"))[:400]}
            for item in result["actions"]
        ]
        if not passed:
            summary["tool_results"] = [
                {"tool": item.get("tool_name"), "data": json.dumps(item.get("observation"))[:650]}
                for item in result["observations"]
            ]
    print("CANVAS " + json.dumps(summary), flush=True)
    return passed


def main():
    selected = set(sys.argv[1:])
    repo_results = []
    for name, question, pattern in CASES:
        if selected and name not in selected:
            continue
        result = chat(question + " Stay inside this checkout. Use read-only tools and give a concise answer with evidence.")
        answer = result["answer"]
        evidence = json.dumps(result["observations"])
        passed = bool(result["status"] == "finished" and evidence != "[]" and match_answer(pattern, answer))
        if name == "check":
            passed = passed and ("diff" in evidence and "--check" in evidence)
        if name == "syntax":
            passed = passed and any(word in evidence for word in ("python_syntax", "compile", "ast.parse", "syntax"))
        if name == "workspace":
            # If the answer quotes mount entries, every quoted mount must actually
            # exist in the owner service block. A correct path alone cannot excuse
            # invented configuration.
            owner_compose = (ROOT / "compose.yaml").read_text().split("\n  openhands:\n", 1)[1]
            owner_compose = re.split(r"(?m)^  [\w.-]+:", owner_compose, maxsplit=1)[0]
            mounts = re.findall(r"(?m)^\s*- `?((?:[./$]|openhands-state:)[^\n`]+:[^\n`]+)`?\s*$", answer)
            passed = passed and all(mount.strip() in owner_compose for mount in mounts)
        repo_results.append(summarize(name, result, passed))

    capabilities = []
    if not selected or "edit-shell" in selected:
        host_fixture = os.environ.get("EVAL_SAMPLE_PATH")
        scratch = nullcontext(None) if host_fixture else tempfile.TemporaryDirectory(prefix="openhands-canvas-eval-", dir=ROOT)
        with scratch as directory:
            sample = Path(host_fixture).resolve() if host_fixture else Path(directory) / "sample.txt"
            if host_fixture and (not sample.is_relative_to(ROOT / "runtime") or not sample.is_file() or sample.stat().st_size):
                raise ValueError("EVAL_SAMPLE_PATH must be an empty temporary file under runtime/.")
            token = hashlib.sha256(sample.name.encode() + os.urandom(16)).hexdigest()[:16]
            original = f"SAMPLE={token}\nRESULT=pending\n"
            sample.write_text(original)
            owner = sample.stat()
            result = chat(
                f"Edit the exact untracked file {sample}. Change only RESULT=pending to RESULT=done. "
                "Use read_file_lines, then use replace_text with the value labeled revision. "
                "Run this verification command in the terminal: "
                f"`pwd && rg -n -e '^SAMPLE=' -e '^RESULT=' -- {shlex.quote(str(sample))}`\n"
                "In your final response give the working directory and the value after SAMPLE=."
            )
            actions = result["actions"]
            terminal_results = [event for event in result["observations"] if event.get("tool_name") == "terminal"]
            evidence = json.dumps(terminal_results)
            read_file = any(
                event.get("tool_name", "").endswith("read_file_lines")
                or event.get("action", {}).get("command") == "view" for event in actions
            )
            edited_file = any(
                event.get("tool_name", "").endswith(("replace_text", "replace_file_lines"))
                or event.get("action", {}).get("command") == "str_replace" for event in actions
            )
            passed = (result["status"] == "finished" and sample.read_text() == original.replace("pending", "done")
                      and sample.stat().st_uid == owner.st_uid
                      and sample.stat().st_gid == owner.st_gid
                      and token in result["answer"] and str(ROOT) in result["answer"]
                      and read_file and edited_file and str(ROOT) in evidence
                      and "RESULT=done" in evidence and f"SAMPLE={token}" in evidence)
            capabilities.append(summarize("edit-shell", result, passed))

    if not selected or "web" in selected:
        result = chat(
            "Use the web search tool to find the official GitHub repository for ripgrep, then fetch "
            "the repository page with the web fetch tool. Report its URL and one fact from the fetched page."
        )
        tools = [event.get("tool_name", "") for event in result["actions"]]
        observations = [event for event in result["observations"] if event.get("tool_name", "").endswith("fetch_url")]
        passed = (result["status"] == "finished" and "github.com/burntsushi/ripgrep" in result["answer"].lower()
                  and any(name.endswith("search_web") for name in tools)
                  and any(not event.get("observation", {}).get("is_error", False) for event in observations))
        capabilities.append(summarize("web", result, passed))

    if not selected or "wsl-files" in selected:
        sample = os.environ.get("EVAL_WSL_SAMPLE_PATH", "")
        token = os.environ.get("EVAL_WSL_TOKEN", "")
        expected_user = os.environ.get("EVAL_WSL_USER", "")
        if not sample or not token or not expected_user:
            raise RuntimeError("wsl-files requires a host fixture path, token, and WSL username.")
        result = chat(
            f"In My PC (WSL) mode, read the temporary file at {sample}, which is outside this project. "
            "Do not change it. Report the exact file path, TOKEN value, username from whoami, and working "
            "directory from pwd. Use the terminal for the WSL file.",
            tags={"purpose": "wsl-file-access-evaluation"}, wsl_mode=True, timeout_seconds=240,
        )
        terminal_actions = [event for event in result["actions"] if event.get("tool_name") == "terminal"]
        terminal_observations = [
            event for event in result["observations"] if event.get("tool_name") == "terminal"
        ]
        terminal_evidence = json.dumps(terminal_observations)
        passed = (
            result["status"] == "finished"
            and bool(terminal_actions)
            and bool(terminal_observations)
            and all(event.get("observation", {}).get("exit_code") == 0
                    and not event.get("observation", {}).get("is_error", False)
                    for event in terminal_observations)
            and sample in terminal_evidence
            and token in terminal_evidence
            and expected_user in terminal_evidence
            and str(ROOT) in terminal_evidence
            and token in result["answer"]
            and expected_user in result["answer"]
            and sample in result["answer"]
        )
        capabilities.append(summarize("wsl-files", result, passed))

    print("SUMMARY " + json.dumps({"repo": [sum(repo_results), len(repo_results)],
                                   "capabilities": [sum(capabilities), len(capabilities)]}), flush=True)
    return int(not all(repo_results + capabilities))


if __name__ == "__main__":
    raise SystemExit(main())
