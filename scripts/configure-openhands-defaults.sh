#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

docker compose --env-file "$ROOT/.env" --profile openhands exec -T openhands python - <<'PY'
import json
import os
import time
import urllib.error
import urllib.request

settings_url = "http://127.0.0.1:18000/api/settings"
profiles_url = "http://127.0.0.1:18000/api/profiles"
session_key_path = "/home/openhands/.openhands/agent-canvas/api-key.txt"
session_key = os.environ.get("LOCAL_BACKEND_API_KEY", "").strip()
if not session_key and os.path.exists(session_key_path):
    with open(session_key_path, encoding="utf-8") as session_key_file:
        session_key = session_key_file.read().strip()
if not session_key:
    raise RuntimeError("OpenHands session API key is unavailable.")

headers = {"X-Session-API-Key": session_key}
target_model = os.environ["LLM_MODEL"]
target_base_url = os.environ["LLM_BASE_URL"]

def request_json(url, *, method="GET", payload=None):
    data = None if payload is None else json.dumps(payload).encode()
    request_headers = dict(headers)
    if data is not None:
        request_headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        url, data=data, headers=request_headers, method=method
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.load(response)


for attempt in range(30):
    try:
        profiles = request_json(profiles_url)
        break
    except (urllib.error.URLError, TimeoutError):
        if attempt == 29:
            raise
        time.sleep(1)

default_profile = next(
    (profile for profile in profiles.get("profiles", []) if profile.get("name") == "default"),
    None,
)
should_seed_default = default_profile is None

if default_profile is not None:
    detail = request_json(f"{profiles_url}/default")
    config = detail.get("config", {})
    llm = config.get("llm", config)
    model = llm.get("model")
    base_url = llm.get("base_url")
    key_is_set = detail.get("api_key_set", False)
    previous_local_base_url = target_base_url.removesuffix("/v1")
    should_seed_default = (
        model in {"gpt-5.6", "openai/gpt-5.6-sol"} and not base_url and not key_is_set
    ) or (
        model == target_model and base_url == target_base_url and not key_is_set
    ) or (
        model == target_model and base_url == previous_local_base_url
    )

if should_seed_default:
    request_json(
        f"{profiles_url}/default",
        method="POST",
        payload={
            "llm": {
                "model": target_model,
                "base_url": target_base_url,
                "api_key": os.environ["LLM_API_KEY"],
                "auth_type": "api_key",
            },
            "include_secrets": True,
        },
    )
    print("Configured the OpenHands default LLM profile for local LiteLLM.")

activate_default = profiles.get("active_profile") is None or (
    profiles.get("active_profile") == "default" and should_seed_default
)
if activate_default:
    request_json(f"{profiles_url}/default/activate", method="POST")

updated_profiles = request_json(profiles_url)
if activate_default and updated_profiles.get("active_profile") != "default":
    raise RuntimeError("OpenHands did not activate its default local LLM profile.")

detail = request_json(f"{profiles_url}/default")
config = detail.get("config", {})
llm = config.get("llm", config)
if should_seed_default:
    if llm.get("model") != target_model or llm.get("base_url") != target_base_url:
        raise RuntimeError("OpenHands default LLM profile does not match local LiteLLM.")
    if not detail.get("api_key_set"):
        raise RuntimeError("OpenHands default LLM profile has no API key configured.")
if activate_default and should_seed_default:
    settings = request_json(settings_url)
    llm = settings.get("agent_settings", {}).get("llm", {})
    if llm.get("model") != target_model or llm.get("base_url") != target_base_url:
        raise RuntimeError("OpenHands active LLM profile does not match local LiteLLM.")
    if not settings.get("llm_api_key_is_set"):
        raise RuntimeError("OpenHands active LLM profile has no API key configured.")

if updated_profiles.get("active_profile") == "default":
    print("OpenHands default LLM profile is active.")
else:
    print("OpenHands local default LLM profile is configured; preserving the active profile.")

# Apply access-control setting: enable/disable shell command tooling.
allow_commands = os.environ.get("OPENHANDS_ALLOW_COMMANDS", "false").lower() in {"1", "true", "yes", "on"}
require_approval = os.environ.get("OPENHANDS_REQUIRE_APPROVAL", "false").lower() in {"1", "true", "yes", "on"}
try:
    settings = request_json(settings_url)
    agent_settings = settings.get("agent_settings", {})
    current_commands = agent_settings.get("allow_commands", True)
    current_approval = agent_settings.get("require_approval", False)
    changed = False
    if current_commands != allow_commands:
        agent_settings["allow_commands"] = allow_commands
        changed = True
    if current_approval != require_approval:
        agent_settings["require_approval"] = require_approval
        changed = True
    if changed:
        request_json(
            settings_url,
            method="PATCH",
            payload={"agent_settings_diff": {"allow_commands": allow_commands, "require_approval": require_approval}},
        )
        print(
            f"OpenHands shell command access: {'enabled' if allow_commands else 'disabled'}; "
            f"require approval: {'yes' if require_approval else 'no'}."
        )
    else:
        print(
            f"OpenHands shell command access already {'enabled' if allow_commands else 'disabled'}; "
            f"require approval already {'yes' if require_approval else 'no'}."
        )
except Exception as exc:  # best-effort; do not block stack startup on settings schema
    print(f"Could not apply OpenHands command-access setting: {exc}")

# Prefer the line-based editor for reliable file changes, while preserving any
# other user instructions already stored in the default OpenHands settings.
try:
    guidance = (
        f"The backend WSL home directory is {os.environ['OPENHANDS_HOST_HOME']}, mounted at that exact path; "
        f"the project root is {os.environ['OPENHANDS_WORKSPACE_PATH']}, and shell commands start there. "
        "Use host absolute paths directly in shell commands and file tools. A user path like ~/AI/foo means "
        f"{os.environ['OPENHANDS_HOST_HOME']}/AI/foo; the container's own ~ is /home/openhands, so do not "
        "use it for the user's WSL home. Do not search only /home/openhands for host files, and do not prefix "
        "host paths with /host when their exact path is mounted. Use /host only for paths outside the mounted "
        "home. Start by checking pwd and the user's specified path, then report paths using the same absolute "
        "paths the user sees. "
        "For file edits, use the local-file-edit MCP tools. Read the file first to get "
        "line numbers and sha256. For an exact block, use replace_text with the old "
        "text and sha256; it changes the file only when there is exactly one match. "
        "If whitespace or indentation differs, retry with ignore_whitespace=true. "
        "A failed match returns closest lines to guide correction. "
        "If the block is ambiguous or awkward to quote, use replace_file_lines with "
        "the read line numbers and sha256. Use create_file only when the path does "
        "not exist. For multi-file changes, use search_files to find candidate files "
        "and hashes, then bulk_edit; it validates every file before committing. Set "
        "replace_all=true only for an intentional repeated replacement. If a hash is "
        "stale, reread before retrying. Inspect returned diffs. Use undo only after a "
        "successful edit that must be reverted. For browser sequences, use the "
        "existing Browser MCP session, group independent actions when supported, "
        "and do not refresh state between actions unless navigation changed the "
        "page or a stale-element error requires it."
    )
    settings = request_json(settings_url)
    agent_settings = settings.get("agent_settings", {})
    agent_context = agent_settings.get("agent_context") or {}
    suffix = agent_context.get("system_message_suffix") or ""
    if guidance not in suffix:
        agent_context["system_message_suffix"] = (suffix.rstrip() + "\n\n" + guidance).strip()
        agent_settings["agent_context"] = agent_context
        request_json(
            settings_url,
            method="PATCH",
            payload={"agent_settings_diff": {"agent_context": agent_context}},
        )
        print("OpenHands default instructions now prefer the local file-editing MCP.")
    else:
        print("OpenHands default file-editing instructions are already configured.")
except Exception as exc:  # preserve stack availability if the profile schema changes
    print(f"Could not apply OpenHands file-editing guidance: {exc}")
PY

docker compose --env-file "$ROOT/.env" --profile openhands exec -T openhands \
    python - < "$ROOT/scripts/configure-openhands-mcp.py"
