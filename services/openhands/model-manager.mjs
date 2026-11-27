import { access, readFile } from "node:fs/promises";
import { request } from "node:http";
import { URL } from "node:url";

const catalogPath = process.env.OPENHANDS_MODEL_CATALOG_PATH || "/opt/local-models/models.tsv";
const serverUrl = process.env.OPENHANDS_MODEL_SERVER_URL || "http://llama-server:8080";
const sessionKey = process.env.LOCAL_BACKEND_API_KEY || "";
const contextLimit = Number.parseInt(process.env.OPENHANDS_MODEL_CONTEXT_MAX || "8192", 10);
const defaultAlias = process.env.OPENHANDS_DEFAULT_MODEL_ALIAS || "";
const contextSteps = [4096, 8192, 16384, 32768, 65536, 100000, 131072, 262144]
  .filter((value) => value <= contextLimit);
if (contextLimit > 0 && !contextSteps.includes(contextLimit)) contextSteps.push(contextLimit);

function sendJson(res, status, payload) {
  res.writeHead(status, { "Content-Type": "application/json", "Cache-Control": "no-store" });
  res.end(JSON.stringify(payload));
}

function authorized(req) {
  if (!sessionKey) return false;
  return req.headers["x-session-api-key"] === sessionKey;
}

function jsonRequest(path, method = "GET", payload = undefined) {
  return new Promise((resolve, reject) => {
    const body = payload === undefined ? null : Buffer.from(JSON.stringify(payload));
    const upstream = request(new URL(path, serverUrl), {
      method,
      headers: body ? { "Content-Type": "application/json", "Content-Length": body.length } : {},
    }, (response) => {
      const chunks = [];
      response.on("data", (chunk) => chunks.push(chunk));
      response.on("end", () => {
        const text = Buffer.concat(chunks).toString("utf8");
        let data;
        try { data = JSON.parse(text); } catch { data = { detail: text.slice(0, 1000) }; }
        resolve({ status: response.statusCode || 502, data });
      });
    });
    upstream.once("error", reject);
    if (body) upstream.end(body);
    else upstream.end();
  });
}

async function readCatalog() {
  const text = await readFile(catalogPath, "utf8");
  return text.split(/\r?\n/).filter((line) => line && !line.startsWith("#")).map((line) => {
    const [alias, label, repo, folder, quant] = line.split("|");
    return { alias, label, repo, folder, quant };
  });
}

function variantId(alias, context) {
  return `openhands-${alias}-ctx${context}`;
}

async function listModels(res) {
  try {
    const [catalog, server] = await Promise.all([
      readCatalog(), jsonRequest("/models?reload=1"),
    ]);
    if (server.status >= 400) throw new Error(server.data?.error?.message || `Model router returned ${server.status}`);
    const rows = server.data?.data || [];
    const models = await Promise.all(catalog.map(async (entry) => {
      const id = variantId(entry.alias, contextSteps.includes(8192) ? 8192 : contextSteps.at(-1));
      const serverModel = rows.find((row) => row.id === id);
      const statusValue = serverModel?.status?.value || "not_downloaded";
      let downloaded = ["loaded", "sleeping"].includes(statusValue);
      if (serverModel?.path && !["incomplete", "downloading", "loading"].includes(statusValue)) {
        try { await access(serverModel.path); downloaded = true; } catch { /* missing HF cache */ }
      }
      const status = statusValue || (downloaded ? "unloaded" : "not_downloaded");
      return { ...entry, downloaded, status, progress: serverModel?.status?.progress || null, default_model_id: id };
    }));
    sendJson(res, 200, { models, context_limit: contextLimit, contexts: contextSteps, default_alias: defaultAlias });
  } catch (error) {
    sendJson(res, 503, { detail: `Model router unavailable: ${error.message}` });
  }
}

function proxyEventStream(req, res) {
  const upstream = request(new URL("/models/sse", serverUrl), {
    method: "GET",
    headers: { Accept: "text/event-stream", "Cache-Control": "no-cache" },
  }, (response) => {
    res.writeHead(response.statusCode || 502, {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache, no-transform",
      Connection: "keep-alive",
      "X-Accel-Buffering": "no",
    });
    response.pipe(res);
  });
  upstream.once("error", () => {
    if (!res.headersSent) sendJson(res, 502, { detail: "Could not connect to model download progress." });
    else res.destroy();
  });
  res.once("close", () => upstream.destroy());
}

export function handleModelManager(req, res, path) {
  if (!path.startsWith("/local-models/")) return false;
  if (!authorized(req)) {
    sendJson(res, 401, { detail: "OpenHands session key required." });
    return true;
  }
  if (req.method === "GET" && path === "/local-models/catalog") {
    void listModels(res);
    return true;
  }
  if (req.method === "GET" && path === "/local-models/events") {
    proxyEventStream(req, res);
    return true;
  }
  if (req.method === "POST" && path === "/local-models/download") {
    let body = "";
    req.on("data", (chunk) => { body += chunk; });
    req.on("end", async () => {
      try {
        const payload = JSON.parse(body || "{}");
        const catalog = await readCatalog();
        const entry = catalog.find((item) => item.alias === payload.alias);
        const context = Number(payload.context);
        if (!entry || !Number.isInteger(context) || !contextSteps.includes(context)) {
          sendJson(res, 400, { detail: "Unknown model or unsupported context size." });
          return;
        }
        const result = await jsonRequest("/models", "POST", {
          model: variantId(entry.alias, context),
        });
        sendJson(res, result.status, result.data);
      } catch (error) {
        sendJson(res, 502, { detail: `Could not start model download: ${error.message}` });
      }
    });
    return true;
  }
  if (req.method === "GET" && path === "/local-models/properties") {
    const url = new URL(req.url || "/", "http://localhost");
    const model = url.searchParams.get("model") || "";
    if (!/^openhands-[a-zA-Z0-9._-]+-ctx\d+$/.test(model)) {
      sendJson(res, 400, { detail: "Invalid model selection." });
      return true;
    }
    Promise.all([
      jsonRequest(`/props?model=${encodeURIComponent(model)}`),
      jsonRequest(`/v1/models?model=${encodeURIComponent(model)}`),
    ]).then(([props, modelList]) => {
      const metadata = modelList.data?.data?.[0]?.meta || {};
      const templateCaps = props.data?.chat_template_caps || {};
      sendJson(res, 200, {
        context_train: metadata.n_ctx_train || props.data?.model_train_context || null,
        reasoning_effort_levels: templateCaps.reasoning_effort_levels || [],
      });
    }).catch((error) => sendJson(res, 503, { detail: `Could not inspect model capabilities: ${error.message}` }));
    return true;
  }
  sendJson(res, 404, { detail: "Not found." });
  return true;
}
