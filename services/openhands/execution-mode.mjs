import { request } from "node:http";
import { createConnection } from "node:net";
import { handleModelManager } from "./model-manager.mjs";

const socketPath = "/run/openhands-host/terminal.sock";
const cookie = "openhands_terminal";
const maxBody = 8 * 1024 * 1024;

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
  if (["low", "medium", "high", "xhigh", "none"].includes(effort)) payload.tags.localeffort = effort;

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
