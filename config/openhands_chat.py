"""Compatibility hook for the owner OpenHands agent's local gpt-oss chat."""
import json
import os

from litellm.integrations.custom_logger import CustomLogger

MARKER = "<local-openhands-tools>"
SEARCH_TOOLS = {"local-repo-context_repo_search", "local-file-edit_search_files"}
TOOL_ARGUMENT_ERRORS = (
    "unparseable json",
    "failed to parse tool call arguments",
    "expecting property name enclosed in double quotes",
)


def repeated_search(messages):
    """Recognize repeated searches for the same text within this user turn."""
    queries = []
    for message in reversed(messages):
        if message.get("role") == "user":
            break
        for call in reversed(message.get("tool_calls") or []):
            function = call.get("function") or {}
            name = function.get("name")
            if name not in SEARCH_TOOLS:
                continue
            try:
                args = json.loads(function.get("arguments", ""))
                query = args.get("query", "")
            except (ValueError, TypeError, AttributeError):
                continue
            if isinstance(query, str) and query:
                queries.append((name, " ".join(query.casefold().split()), args.get("pattern")))
    if queries and queries.count(queries[0]) > 1:
        return queries[0][0]
    return None


def invalid_tool_arguments(messages):
    """Detect a rejected malformed call so the model can retry the guarded tool."""
    for message in reversed(messages):
        content = message.get("content", "")
        if isinstance(content, str):
            text = content.casefold()
        else:
            text = json.dumps(content, ensure_ascii=False).casefold()
        if any(marker in text for marker in TOOL_ARGUMENT_ERRORS):
            return True
    return False


class OpenHandsChatCompatibility(CustomLogger):
    async def async_pre_call_hook(self, user_api_key_dict, cache, data, call_type):
        if (
            not (
                (os.environ.get("LOCAL_MODEL_ALIAS") == "gpt-oss-20b" and data.get("model") == "local-model")
                or str(data.get("model", "")).startswith(("openhands-gpt-oss-20b-ctx", "openhands-gpt-oss-20b-q"))
            )
            or not any(message.get("role") in {"system", "developer"}
                       and MARKER in str(message.get("content", ""))
                       for message in data.get("messages") or [])
        ):
            return data

        # GPT-OSS can emit an invalid Harmony channel when it tries to use the
        # finish tool as a final response. OpenHands handles normal assistant
        # messages as completed turns, so it does not need this tool upstream.
        tools = data.get("tools") or []
        suppressed = {"finish"}
        repeated = repeated_search(data.get("messages") or [])
        if invalid_tool_arguments(data.get("messages") or []):
            data["messages"] = [*(data.get("messages") or []), {
                "role": "developer",
                "content": "The previous tool call failed because its arguments were not valid JSON. "
                           "Retry the same guarded local-file-edit tool with clean JSON arguments, "
                           "for example {\"path\": \"...\", \"content\": \"...\"}. "
                           "Do not switch to a shell command to create or edit project files. "
                           "Keep file changes on the revision-guarded local-file-edit tools.",
            }]
        if repeated:
            suppressed.add(repeated)
            messages = data["messages"]
            data["messages"] = [*messages, {
                "role": "developer",
                "content": "A search for the same text was repeated. Review the existing results. "
                           "If they answer the question, give the answer now. If you still need evidence, "
                           "read the relevant file directly or use a different verification tool.",
            }]
        if tools:
            data["tools"] = [tool for tool in tools if tool.get("function", {}).get("name") not in suppressed]
        data["temperature"] = 0.15
        data["reasoning_effort"] = "low"
        return data


openhands_chat = OpenHandsChatCompatibility()
