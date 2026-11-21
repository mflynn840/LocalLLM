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
with open(session_key_path, encoding="utf-8") as session_key_file:
    session_key = session_key_file.read().strip()

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
    should_seed_default = (
        model in {"gpt-5.6", "openai/gpt-5.6-sol"} and not base_url and not key_is_set
    ) or (
        model == target_model and base_url == target_base_url and not key_is_set
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
    print("Created the OpenHands default LLM profile for local LiteLLM.")

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
PY

docker compose --env-file "$ROOT/.env" --profile openhands exec -T openhands \
    python - < "$ROOT/scripts/configure-openhands-mcp.py"
