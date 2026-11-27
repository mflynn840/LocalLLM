"""Configure the resolved OpenHands agent's terminal for a WSL conversation."""

import os
import re
from dataclasses import replace
from pathlib import Path
from pydantic import SecretStr

from openhands.sdk.context.agent_context import AgentContext
from openhands.sdk.launch import AgentLaunchError, ResolvedLaunch
from openhands.sdk.settings.model import OpenHandsAgentSettings
from openhands.sdk.tool.defaults import resolve_tool_specs


def apply_terminal_mode(source, request):
    if (request.tags or {}).get("localterminal") != "wsl":
        return source
    if (
        os.environ.get("OPENHANDS_HOST_TERMINAL_ENABLED") != "true"
        or os.environ.get("OPENHANDS_ALLOW_COMMANDS") != "true"
    ):
        raise AgentLaunchError("The WSL terminal is disabled in this Canvas instance.")
    if request.worktree or request.conversation_runtime not in (None, "local"):
        raise AgentLaunchError("The WSL terminal requires the shared local workspace.")
    if not isinstance(source, ResolvedLaunch) or not isinstance(source.settings, OpenHandsAgentSettings):
        raise AgentLaunchError("Select an OpenHands agent profile to use the WSL terminal.")

    settings = source.settings
    tools = resolve_tool_specs(settings.tools)
    if not any(tool.name in {"terminal", "TerminalTool"} for tool in tools):
        raise AgentLaunchError("This profile has no terminal tool. Select a profile with terminal access.")
    tools = [
        tool.model_copy(update={"params": {
            **tool.params,
            "terminal_type": "subprocess",
            "shell_path": "/usr/local/bin/openhands-host-terminal",
        }}) if tool.name in {"terminal", "TerminalTool"} else tool
        for tool in tools
    ]
    context = settings.agent_context or AgentContext()
    suffix = (context.system_message_suffix or "") + (
        "\n<local-openhands-tools>\n"
        "The user selected My PC (WSL) for this conversation. The terminal runs as the "
        "owner's normal WSL user in the exact host working directory, with that user's "
        "shell startup files, installed tools, and filesystem permissions. Use the "
        "For Jetson requests, read .openhands/skills/jetson-connect/SKILL.md in the "
        "project workspace and use jetson-host_jetson_exec only when the user explicitly "
        "asks to work on the Jetson. It connects as michael with sudo access. Preserve "
        "user data and SSD contents unless the user explicitly requests a destructive "
        "storage operation. Use /home/michael/AI/jetson/connect_jetson.sh in My PC "
        "(WSL) mode only for USB-network setup or when the MCP tool is unavailable. "
        "terminal for any host path, including files outside the mounted project. "
        "Within the mounted project, use the revision-guarded local-file-edit tools "
        "for file changes when available; use the terminal to create directories with "
        "mkdir -p before creating files. Never remove or recreate a project to recover "
        "from one file error. Read the complete error, correct the specific issue, and "
        "continue. For multi-step work, keep a checklist and verify each deliverable "
        "with the requested commands before reporting completion. Check installed "
        "library APIs or local documentation instead of guessing names or constants.\n"
        "</local-openhands-tools>"
    )
    return replace(source, settings=settings.model_copy(update={
        "tools": tools,
        "agent_context": context.model_copy(update={"system_message_suffix": suffix}),
    }))


def apply_model_selection(source, request):
    tags = request.tags or {}
    alias = tags.get("localmodel")
    if not alias:
        return source
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._-]*", alias):
        raise AgentLaunchError("The selected local model alias is invalid.")

    catalog = Path(os.environ.get("OPENHANDS_MODEL_CATALOG_PATH", "/opt/local-models/models.tsv"))
    aliases = {
        line.split("|", 1)[0]
        for line in catalog.read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    }
    if alias not in aliases:
        raise AgentLaunchError("The selected model is not in the LocalLLM catalog.")

    context_value = tags.get("localcontext", "8192")
    try:
        context = int(context_value)
    except (TypeError, ValueError) as exc:
        raise AgentLaunchError("The selected context size is invalid.") from exc
    maximum = int(os.environ.get("OPENHANDS_MODEL_CONTEXT_MAX", "8192"))
    supported_contexts = {4096, 8192, 16384, 32768, 65536, 100000, 131072, 262144}
    supported_contexts.add(maximum)
    if context not in supported_contexts or context > maximum:
        raise AgentLaunchError("The selected context size is not available in this LocalLLM setup.")

    effort = tags.get("localeffort")
    if effort not in (None, "", "none", "minimal", "low", "medium", "high", "xhigh", "max"):
        raise AgentLaunchError("The selected reasoning effort is invalid.")
    if not isinstance(source, ResolvedLaunch) or not isinstance(source.settings, OpenHandsAgentSettings):
        raise AgentLaunchError("Select an OpenHands profile to use a LocalLLM model.")

    variant = f"openhands-{alias}-ctx{context}"
    llm = source.settings.llm.model_copy(update={
        "model": f"litellm_proxy/{variant}",
        "base_url": os.environ.get("LLM_BASE_URL"),
        "api_key": SecretStr(os.environ.get("LLM_API_KEY", "")),
        "max_input_tokens": context,
    })
    if effort:
        llm = llm.model_copy(update={"reasoning_effort": effort})
    return replace(source, settings=source.settings.model_copy(update={"llm": llm}))


def apply_launch_overrides(source, request):
    return apply_terminal_mode(apply_model_selection(source, request), request)
