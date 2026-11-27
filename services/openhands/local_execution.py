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
        "The user selected My PC (WSL) for this conversation (the owner default). The terminal runs as the "
        "owner's normal WSL user in the exact host working directory, with that user's "
        "shell startup files, installed tools, and filesystem permissions. In this mode "
        "use the host terminal directly for file reads, edits, and commands, including "
        "files in the mounted project; use Docker-mounted file tools only when Docker "
        "mode is explicitly selected. For explicit SSH work, use the ssh-hosts MCP: "
        "list hosts, refresh the remote map with ssh_map_files, search with ssh_search_files, "
        "and use ssh_exec, ssh_read_file, and ssh_write_file for every command or edit. "
        "Keep that task on the requested SSH host from inspection through verification; "
        "do not switch to local workspace, Docker, or local repository tools midway. "
        "Never copy remote project code to the local workspace unless the user asks. "
        "The Jetson key is jetson; its host bridge prepares USB networking and keeps "
        "credentials on WSL. Preserve remote data unless removal is requested. "
        "Never remove or recreate a project to recover "
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


def apply_ssh_route(source, request):
    """Pin an explicitly remote conversation to one SSH host and tool server."""
    host = (request.tags or {}).get("sshhost")
    if not host:
        return source
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}", host):
        raise AgentLaunchError("The inferred SSH host key is invalid.")
    if not isinstance(source, ResolvedLaunch) or not isinstance(source.settings, OpenHandsAgentSettings):
        raise AgentLaunchError("Select an OpenHands profile to use automatic SSH routing.")

    settings = source.settings
    ssh_server = settings.mcp_config.get("ssh-hosts")
    if ssh_server is None:
        raise AgentLaunchError("The ssh-hosts MCP is unavailable in the selected profile.")

    tools = [
        tool for tool in resolve_tool_specs(settings.tools)
        if tool.name in {"task_tracker", "TaskTrackerTool"}
    ]
    context = settings.agent_context or AgentContext()
    suffix = (context.system_message_suffix or "") + (
        "\n<ssh-task-route>\n"
        f"This conversation is pinned to SSH host key {host}. Use only ssh-hosts "
        "MCP tools for discovery, file mapping, search, reads, writes, commands, and "
        "verification. Start with ssh_hosts and ssh_map_files. Do not inspect or modify "
        "the local workspace and do not use terminal, local file editing, or local "
        "repository tools. All paths mentioned by the user are paths on the SSH host. "
        "The saved host key selects the connection; use ssh_exec with hostname when "
        "the actual system hostname is requested. Stay on this host through completion.\n"
        "</ssh-task-route>"
    )
    return replace(source, settings=settings.model_copy(update={
        "tools": tools,
        "mcp_config": {"ssh-hosts": ssh_server},
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
    entries = {}
    for line in catalog.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        fields = line.split("|")
        if len(fields) not in (5, 6):
            continue
        entries[fields[0]] = (fields[4], fields[5].split(",") if len(fields) == 6 and fields[5] else [fields[4]])
    if alias not in entries:
        raise AgentLaunchError("The selected model is not in the LocalLLM catalog.")
    default_quant, supported_quants = entries[alias]
    quant = tags.get("localquant") or default_quant
    if quant not in supported_quants:
        raise AgentLaunchError("The selected quantization is not available for this model.")

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

    variant = f"openhands-{alias}-q{quant}-ctx{context}"
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
    source = apply_model_selection(source, request)
    source = apply_terminal_mode(source, request)
    return apply_ssh_route(source, request)
