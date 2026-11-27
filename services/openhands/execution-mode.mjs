import { request } from "node:http";
import { createConnection } from "node:net";
import { readFileSync } from "node:fs";
import { handleModelManager } from "./model-manager.mjs";

const socketPath = "/run/openhands-host/terminal.sock";
const cookie = "openhands_terminal";
const maxBody = 8 * 1024 * 1024;
const contextSteps = [4096, 8192, 16384, 32768, 65536, 100000, 131072, 262144];
const reasoningEfforts = new Set(["none", "minimal", "low", "medium", "high", "xhigh", "max"]);

function responds() {
  return new Promise((resolve) => {
    if (process.env.OPENHANDS_HOST_TERMINAL_ENABLED !== "true" ||
        process.env.OPENHANDS_ALLOW_COMMANDS !== "true") {
      resolve(false);
      return;
    }
    const connection = createConnection(socketPath);
    const finish = (available) => {
      connection.destroy();
      resolve(available);
    };
    connection.once("connect", () => finish(true));
    connection.once("error", () => finish(false));
    connection.setTimeout(500, () => finish(false));
  });
}

function json(res, status, data) {
  res.writeHead(status, { "Content-Type": "application/json", "Cache-Control": "no-store" });
  res.end(JSON.stringify(data));
}

function isHostCookie(req) {
  return (req.headers.cookie ?? "").split(";").some((value) => value.trim() === `${cookie}=wsl`);
}

function cookieValue(req, name) {
  const value = (req.headers.cookie ?? "").split(";").map((part) => part.trim())
    .find((part) => part.startsWith(`${name}=`))?.slice(name.length + 1);
  if (!value) return "";
  try { return decodeURIComponent(value); } catch { return ""; }
}

function launchSettings(payload) {
  if (payload.agent_settings && typeof payload.agent_settings === "object") return payload.agent_settings;
  if (payload.agent && typeof payload.agent === "object") return payload.agent;
  return null;
}

function usesAgentProfile(payload) {
  return typeof payload.agent_profile_id === "string" && payload.agent_profile_id.length > 0;
}

function appendGuidance(settings, guidance) {
  settings.agent_context ||= {};
  const suffix = settings.agent_context.system_message_suffix || "";
  if (!suffix.includes(guidance)) {
    settings.agent_context.system_message_suffix = [suffix, guidance].filter(Boolean).join("\n\n");
  }
}

function applyWslTerminal(payload) {
  const settings = launchSettings(payload);
  // The Canvas UI normally sends the selected agent profile by ID. OpenHands
  // resolves that profile after this ingress forwards the request, and the
  // local_execution hook applies the WSL terminal to the resolved settings.
  if (!settings && usesAgentProfile(payload)) return;
  if (!settings) throw new Error("My PC (WSL) mode requires an OpenHands settings profile.");
  let tools = settings.tools;
  if (!Array.isArray(tools) || tools.length === 0) {
    tools = [
      { name: "terminal", params: {} },
      { name: "file_editor", params: {} },
      { name: "task_tracker", params: {} },
      { name: "switch_llm", params: {} },
    ];
  }
  const terminal = tools.find((tool) => ["terminal", "TerminalTool"].includes(tool?.name));
  if (!terminal) throw new Error("The selected OpenHands profile has no terminal tool for My PC (WSL) mode.");
  terminal.params = {
    ...(terminal.params || {}),
    terminal_type: "subprocess",
    shell_path: "/usr/local/bin/openhands-host-terminal",
  };
  settings.tools = tools;
  // LiteLLM's OpenHands provider appends `/v1/...` to base_url itself. The
  // saved profile is shared with OpenAI clients and therefore includes `/v1`;
  // remove that suffix for this SDK provider to avoid `/v1/v1/model/info`.
  if (settings.llm?.base_url) settings.llm.base_url = settings.llm.base_url.replace(/\/v1\/?$/, "");
  appendGuidance(settings,
    "My PC (WSL) mode is active for this conversation. The terminal runs as the WSL user in the host distribution. " +
    "Use host absolute paths directly with the terminal for files outside the project; Docker file tools cannot read those paths. " +
    "For project paths, use the mounted project path and the revision-guarded file tools when available.");
}

function applyModelSelection(payload, alias, context, effort) {
  if (!alias) return;
  const settings = launchSettings(payload);
  // Profile launches are resolved inside OpenHands. Its local_execution hook
  // applies the selected model after resolution, matching the terminal path.
  if (!settings && usesAgentProfile(payload)) return;
  if (!settings?.llm) throw new Error("The selected OpenHands profile has no configurable local model settings.");
  if (!/^[a-zA-Z0-9][a-zA-Z0-9._-]*$/.test(alias)) throw new Error("Invalid local model selection.");
  const catalogPath = process.env.OPENHANDS_MODEL_CATALOG_PATH || "/opt/local-models/models.tsv";
  let aliases;
  try {
    aliases = new Set(readFileSync(catalogPath, "utf8").split(/\r?\n/)
      .filter((line) => line && !line.startsWith("#")).map((line) => line.split("|", 1)[0]));
  } catch (error) {
    throw new Error(`Could not read the local model catalog: ${error.message}`);
  }
  if (!aliases.has(alias)) throw new Error("The selected model is not in the LocalLLM catalog.");
  const contextLimit = Number.parseInt(process.env.OPENHANDS_MODEL_CONTEXT_MAX || "8192", 10);
  const contexts = contextSteps.filter((value) => value <= contextLimit);
  if (contextLimit > 0 && !contexts.includes(contextLimit)) contexts.push(contextLimit);
  const size = Number(context);
  if (!Number.isInteger(size) || !contexts.includes(size)) throw new Error("The selected context size is not available in this LocalLLM setup.");
  settings.llm.model = `litellm_proxy/openhands-${alias}-ctx${size}`;
  if (process.env.LLM_BASE_URL) settings.llm.base_url = process.env.LLM_BASE_URL.replace(/\/v1\/?$/, "");
  settings.llm.max_input_tokens = size;
  if (effort && reasoningEfforts.has(effort)) settings.llm.reasoning_effort = effort;
}

async function launchConversation(req, res, backend) {
  const hostMode = isHostCookie(req);
  if (hostMode && !await responds()) {
    json(res, 503, { detail: "WSL terminal is unavailable. Select Docker or restart the local stack." });
    return;
  }

  let payload;
  try {
    const chunks = [];
    let bytes = 0;
    for await (const chunk of req) {
      bytes += chunk.length;
      if (bytes > maxBody) {
        json(res, 413, { detail: "Conversation request is too large." });
        return;
      }
      chunks.push(chunk);
    }
    payload = JSON.parse(Buffer.concat(chunks));
    if (!payload || Array.isArray(payload) || typeof payload !== "object") throw new Error();
  } catch {
    json(res, 400, { detail: "Invalid conversation request." });
    return;
  }
  payload.tags = { ...payload.tags };
  if (hostMode) {
    payload.tags.localterminal = "wsl";
    // A host shell cannot run in a per-conversation Docker worktree. Keep the
    // user's selected checkout and route this conversation through the local
    // agent process where the private WSL bridge is mounted.
    payload.worktree = false;
    payload.worktree_options = null;
    payload.conversation_runtime = "local";
  }
  const model = cookieValue(req, "openhands_model");
  const context = cookieValue(req, "openhands_context");
  const effort = cookieValue(req, "openhands_effort");
  if (/^[a-zA-Z0-9._-]+$/.test(model)) payload.tags.localmodel = model;
  if (/^\d{1,6}$/.test(context)) payload.tags.localcontext = context;
  if (reasoningEfforts.has(effort)) payload.tags.localeffort = effort;
  try {
    if (hostMode) applyWslTerminal(payload);
    if (model) applyModelSelection(payload, model, context, effort);
  } catch (error) {
    json(res, 400, { detail: error.message });
    return;
  }

  const body = Buffer.from(JSON.stringify(payload));
  const headers = { ...req.headers, "content-length": body.length };
  delete headers["transfer-encoding"];
  const upstream = request(new URL("/api/conversations", backend), { method: "POST", headers }, (response) => {
    res.writeHead(response.statusCode, response.headers);
    response.pipe(res);
  });
  upstream.once("error", () => {
    if (!res.headersSent) json(res, 502, { detail: "Could not reach the Canvas agent server." });
    else res.destroy();
  });
  res.once("close", () => upstream.destroy());
  upstream.end(body);
}

export function handleExecutionMode(req, res, backend) {
  const path = (req.url ?? "/").split("?", 1)[0].replace(/^\/canvas(?=\/)/, "");
  if (handleModelManager(req, res, path)) return true;
  if (req.method === "GET" && path === "/local-execution/status") {
    responds().then((available) => json(res, 200, { available }));
    return true;
  }
  if (req.method === "POST" && path === "/api/conversations" && backend &&
      (isHostCookie(req) || cookieValue(req, "openhands_model"))) {
    launchConversation(req, res, backend).catch(() => {
      if (!res.headersSent) json(res, 500, { detail: "Could not start the WSL terminal conversation." });
      else res.destroy();
    });
    return true;
  }
  return false;
}
