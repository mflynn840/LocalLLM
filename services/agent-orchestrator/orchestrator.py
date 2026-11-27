"""Checkpoint-driven planner, OpenHands worker, and low-cost validator."""
import json
import os
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path


API = os.environ.get("OPENHANDS_API_URL", "http://openhands:8000/api")
API_KEY = os.environ.get("OPENHANDS_API_KEY", "")
LLM = os.environ.get("LITELLM_API_URL", "http://openhands-llm-gateway:4000/v1")
LLM_KEY = os.environ.get("LITELLM_API_KEY", "")
MODEL = os.environ.get("ORCHESTRATOR_MODEL", "local-model")
STATE = Path(os.environ.get("STATE_DIR", "/state"))
WORKSPACE = os.environ.get("WORKSPACE_PATH", "/projects")


def post(url, payload, headers=None):
    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, method="POST", headers={"Content-Type": "application/json", **(headers or {})})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def get(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def llm_json(system, prompt):
    result = post(LLM + "/chat/completions", {
        "model": MODEL, "temperature": 0, "max_tokens": 1200,
        "response_format": {"type": "json_object"},
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
    }, {"Authorization": f"Bearer {LLM_KEY}"})
    return json.loads(result["choices"][0]["message"]["content"])


def api_headers():
    return {"X-Session-API-Key": API_KEY}


def profile_id():
    settings = get(API + "/profiles", api_headers())
    pid = settings.get("active_agent_profile_id")
    if not pid:
        raise RuntimeError("Select an OpenHands agent profile before starting an orchestrated task.")
    return pid


def start_worker(text, mode):
    payload = {
        "agent_profile_id": profile_id(), "workspace": {"working_dir": WORKSPACE},
        "conversation_runtime": "local", "worktree": False, "max_iterations": 12,
        "autotitle": False, "initial_message": {"role": "user", "content": [{"type": "text", "text": text}]},
        "tags": {"purpose": "orchestrated-task"},
    }
    headers = api_headers()
    if mode == "wsl": headers["Cookie"] = "openhands_terminal=wsl"
    return post(API + "/conversations", payload, headers)


def wait_worker(cid, mode, timeout=1800):
    start = time.monotonic()
    headers = api_headers()
    while time.monotonic() - start < timeout:
        data = get(API + f"/conversations/{cid}", headers)
        status = data.get("execution_status")
        if status in {"finished", "error", "stuck", "paused", "waiting_for_confirmation"}:
            events = get(API + f"/conversations/{cid}/events/search?limit=100", headers)
            response = get(API + f"/conversations/{cid}/agent_final_response", headers).get("response", "")
            return status, response, events
        time.sleep(2)
    return "timeout", "", {}


def run(goal, mode="docker", max_reviews=3):
    if mode not in {"docker", "wsl"}: raise ValueError("mode must be docker or wsl")
    STATE.mkdir(parents=True, exist_ok=True)
    task_id = str(uuid.uuid4())
    state_path = STATE / f"{task_id}.json"
    plan = llm_json(
        "Create a concise coding plan as JSON with key 'steps', an array of 2-6 objects each containing 'objective' and 'acceptance'. Keep steps independently verifiable.", goal,
    )
    steps = plan.get("steps", [])
    if not steps: raise RuntimeError("Planner returned no steps.")
    record = {"id": task_id, "goal": goal, "mode": mode, "plan": steps, "status": "working", "reviews": []}
    state_path.write_text(json.dumps(record, indent=2))
    context = ""
    for index, step in enumerate(steps):
        worker_prompt = (
            f"Goal: {goal}\nPlan: {json.dumps(steps)}\nCurrent step {index+1}/{len(steps)}: {json.dumps(step)}\n"
            f"Complete this step in the current workspace. Preserve existing work. Run relevant checks and report changed files, commands with exit codes, and evidence for each acceptance criterion.\n{context}"
        )
        worker = start_worker(worker_prompt, mode)
        status, answer, events = wait_worker(worker["id"], mode)
        review_input = {"goal": goal, "plan": steps, "step": step, "worker_status": status,
                        "worker_report": answer[:5000], "events": json.dumps(events)[:9000]}
        verdict = llm_json(
            "You are a read-only code reviewer. Check the worker report and event evidence against the current step acceptance criteria. Return JSON: decision (pass/revise/blocked), reason, next_instruction. Never invent test results. Limit next_instruction to one focused correction.",
            json.dumps(review_input),
        )
        record["reviews"].append({"step": index + 1, **verdict, "conversation_id": worker["id"]})
        record["status"] = "needs-revision" if verdict.get("decision") == "revise" else "working"
        state_path.write_text(json.dumps(record, indent=2))
        if verdict.get("decision") == "revise" and len(record["reviews"]) < max_reviews:
            context = "Supervisor review: " + verdict.get("next_instruction", verdict.get("reason", ""))
            # Revisit the same step without an unbounded review loop.
            worker = start_worker(worker_prompt + "\n" + context, mode)
            status, answer, events = wait_worker(worker["id"], mode)
            verdict = llm_json(
                "Read-only reviewer. Return JSON decision pass/revise/blocked, reason, next_instruction. Be concise.",
                json.dumps({**review_input, "worker_status": status, "worker_report": answer[:5000], "events": json.dumps(events)[:9000]}),
            )
            record["reviews"].append({"step": index + 1, **verdict, "conversation_id": worker["id"]})
            if verdict.get("decision") != "pass":
                record["status"] = "blocked"; break
        elif verdict.get("decision") != "pass":
            record["status"] = "blocked"; break
        context = "Previously completed steps: " + json.dumps(steps[:index + 1])
    else:
        record["status"] = "complete"
    state_path.write_text(json.dumps(record, indent=2))
    return record
