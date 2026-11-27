(() => {
  const api = "/canvas/local-models";
  const picker = document.createElement("details");
  picker.className = "local-model-picker";
  picker.innerHTML = `
    <summary>Model: loading…</summary>
    <div class="local-model-menu">
      <div class="local-model-heading"><strong>Model for new conversations</strong><button type="button" data-refresh aria-label="Refresh models">↻</button></div>
      <div class="local-model-list" role="list"></div>
      <label>Context window <select data-context disabled></select></label>
      <label data-effort-label hidden>Reasoning effort <select data-effort></select></label>
      <p data-model-status role="status" aria-live="polite"></p>
      <p class="local-model-help">A conversation keeps its selected model. The shared backend queues requests while it loads another model.</p>
    </div>`;
  const summary = picker.querySelector("summary");
  const list = picker.querySelector(".local-model-list");
  const contextSelect = picker.querySelector("[data-context]");
  const effortLabel = picker.querySelector("[data-effort-label]");
  const effortSelect = picker.querySelector("[data-effort]");
  const status = picker.querySelector("[data-model-status]");
  const requestHeaders = () => {
    let storedKey = "";
    try { storedKey = JSON.parse(localStorage.getItem("openhands-agent-server-config") || "{}").sessionApiKey || ""; } catch { /* clean setup */ }
    return { "X-Session-API-Key": window.__AGENT_CANVAS_SESSION_API_KEY__ || storedKey };
  };
  let contexts = [4096, 8192, 16384, 32768, 65536, 100000, 131072, 262144];
  let models = [];
  let contextLimit = 8192;
  let selected = "";
  let selectionVersion = 0;

  const readCookie = (name) => {
    const item = document.cookie.split(";").map((part) => part.trim()).find((part) => part.startsWith(`${name}=`));
    if (!item) return "";
    try { return decodeURIComponent(item.slice(name.length + 1)); } catch { return ""; }
  };
  const setCookie = (name, value) => {
    const secure = location.protocol === "https:" ? "; Secure" : "";
    document.cookie = `${name}=${encodeURIComponent(value)}; Path=/; SameSite=Strict; Max-Age=2592000${secure}`;
  };
  const variant = (alias, context) => `openhands-${alias}-ctx${context}`;
  const selectedModel = () => models.find((model) => model.alias === selected);

  async function apiRequest(path, options = {}) {
    const response = await fetch(`${api}${path}`, {
      cache: "no-store", ...options,
      headers: { ...requestHeaders(), ...(options.headers || {}) },
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || `Request failed (${response.status})`);
    return data;
  }

  function saveSelection() {
    if (!selected) return;
    setCookie("openhands_model", selected);
    setCookie("openhands_context", contextSelect.value || "8192");
    setCookie("openhands_effort", effortSelect.value === "default" ? "" : effortSelect.value);
    const model = selectedModel();
    summary.textContent = `Model: ${model?.label || selected}${model?.quant ? ` (${model.quant})` : ""}`;
  }

  function selectModel(model) {
    const version = ++selectionVersion;
    selected = model.alias;
    renderModels();
    contextSelect.disabled = true;
    contextSelect.replaceChildren(new Option("Loading model limits…", ""));
    effortLabel.hidden = true;
    effortSelect.replaceChildren(new Option("Model default", "default"));
    effortSelect.value = "default";
    status.textContent = "Checking model capabilities…";
    const defaultContext = contexts.includes(8192) ? 8192 : contexts[contexts.length - 1];
    const modelContext = model.default_context || defaultContext;
    const id = variant(model.alias, modelContext);
    apiRequest(`/properties?model=${encodeURIComponent(id)}`).then((result) => {
      if (version !== selectionVersion) return;
      const maxContext = Math.min(contextLimit, Number(result.context_train) || contextLimit);
      const options = contexts.filter((value) => value <= maxContext);
      if (modelContext <= maxContext && !options.includes(modelContext)) options.push(modelContext);
      const savedContext = Number(readCookie("openhands_context"));
      contextSelect.replaceChildren(...options.sort((a, b) => a - b).map((value) => new Option(`${value.toLocaleString()} tokens`, String(value))));
      contextSelect.value = options.includes(savedContext) ? String(savedContext) : String(options.includes(defaultContext) ? defaultContext : options[0]);
      contextSelect.disabled = false;
      const levels = (result.reasoning_effort_levels || []).filter((value) => ["none", "minimal", "low", "medium", "high", "xhigh", "max"].includes(value));
      effortSelect.replaceChildren(new Option("Model default", "default"), ...levels.map((value) => new Option(value, value)));
      const savedEffort = readCookie("openhands_effort");
      effortSelect.value = levels.includes(savedEffort) ? savedEffort : "default";
      effortLabel.hidden = levels.length === 0;
      status.textContent = "";
      saveSelection();
    }).catch((error) => {
      if (version !== selectionVersion) return;
      contextSelect.replaceChildren(...contexts.filter((value) => value <= Math.min(contextLimit, 8192)).map((value) => new Option(`${value.toLocaleString()} tokens`, String(value))));
      contextSelect.value = String(Math.min(8192, contextLimit));
      contextSelect.disabled = false;
      effortLabel.hidden = true;
      effortSelect.value = "default";
      status.textContent = `Could not inspect capabilities: ${error.message}`;
      saveSelection();
    });
  }

  async function watchDownload(model, button) {
    button.disabled = true;
    const row = button.closest(".local-model-row");
    const progress = row.querySelector("progress");
    const state = row.querySelector("[data-download-status]");
    progress.hidden = false;
    progress.value = 0;
    state.textContent = "Starting download…";
    try {
      const context = Math.min(8192, contextLimit);
      const started = await apiRequest("/download", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ alias: model.alias, context }),
      });
      if (started.error) throw new Error(started.error.message || "Download failed to start.");
      const response = await fetch(`${api}/events`, { headers: requestHeaders(), cache: "no-store" });
      if (!response.ok || !response.body) throw new Error("Could not connect to download progress.");
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let pending = "";
      let eventType = "";
      let finished = false;
      while (!finished) {
        const { value, done } = await reader.read();
        if (done) break;
        pending += decoder.decode(value, { stream: true });
        const lines = pending.split(/\r?\n/);
        pending = lines.pop() || "";
        for (const line of lines) {
          if (line.startsWith("event:")) { eventType = line.slice(6).trim(); continue; }
          const raw = line.startsWith("data:") ? line.slice(5).trim() : line.trim();
          if (!raw.startsWith("{")) continue;
          let event;
          try { event = JSON.parse(raw); } catch { continue; }
          event.event ||= eventType;
          eventType = "";
          if (event.model && event.model !== model.default_model_id) continue;
          if (event.event === "download_progress" || event.data && typeof event.data === "object") {
            const progressData = event.data?.progress || event.data || {};
            const files = Object.values(progressData).filter((item) => item && Number.isFinite(item.done) && Number.isFinite(item.total));
            const total = files.reduce((sum, file) => sum + file.total, 0);
            const doneBytes = files.reduce((sum, file) => sum + file.done, 0);
            if (total > 0) {
              progress.value = Math.floor(100 * doneBytes / total);
              state.textContent = `Downloading ${progress.value}%`;
            }
          }
          if (event.event === "download_finished" || event.event === "download_failed") {
            finished = true;
            if (event.event === "download_failed") throw new Error(event.data?.error || "Model download failed.");
            state.textContent = "Download complete";
          }
        }
      }
      await reader.cancel().catch(() => {});
      await refresh();
    } catch (error) {
      state.textContent = error.message;
    } finally {
      button.disabled = false;
    }
  }

  function renderModels() {
    list.replaceChildren();
    for (const model of models) {
      const row = document.createElement("div");
      row.className = `local-model-row${model.downloaded ? "" : " is-missing"}`;
      const choose = document.createElement("button");
      choose.type = "button";
      choose.className = "local-model-choice";
      choose.textContent = `${model.label}${model.quant ? ` · ${model.quant}` : ""}${model.downloaded ? "" : " · not downloaded"}`;
      choose.disabled = !model.downloaded;
      choose.setAttribute("aria-pressed", String(model.alias === selected));
      if (model.downloaded) choose.addEventListener("click", () => selectModel(model));
      const download = document.createElement("button");
      download.type = "button";
      download.className = "local-model-download";
      const downloading = model.status === "downloading";
      download.textContent = model.downloaded ? "Ready" : downloading ? "Downloading" : "Download";
      download.disabled = model.downloaded || downloading;
      if (!model.downloaded) download.addEventListener("click", () => watchDownload(model, download));
      const progress = document.createElement("progress");
      progress.max = 100;
      progress.hidden = true;
      const progressText = document.createElement("span");
      progressText.dataset.downloadStatus = "";
      progressText.className = "local-model-download-status";
      if (downloading) {
        const files = Object.values(model.progress || {}).filter((item) => item && Number.isFinite(item.done) && Number.isFinite(item.total));
        const total = files.reduce((sum, file) => sum + file.total, 0);
        const doneBytes = files.reduce((sum, file) => sum + file.done, 0);
        progress.hidden = false;
        progress.value = total ? Math.floor(100 * doneBytes / total) : 0;
        progressText.textContent = total ? `Downloading ${progress.value}%` : "Download in progress";
      }
      row.append(choose, download, progress, progressText);
      list.append(row);
    }
  }

  async function refresh() {
    status.textContent = "Refreshing model list…";
    try {
      const result = await apiRequest("/catalog");
      models = result.models || [];
      contextLimit = Number(result.context_limit) || 8192;
      contexts = result.contexts?.length ? result.contexts : contexts.filter((value) => value <= contextLimit);
      renderModels();
      if (!selected) {
        const saved = readCookie("openhands_model");
        const defaultAlias = result.default_alias;
        const initial = models.find((model) => model.alias === saved && model.downloaded)
          || models.find((model) => model.alias === defaultAlias && model.downloaded)
          || models.find((model) => model.downloaded);
        if (initial) selectModel(initial);
        else status.textContent = "Download a model to use it in new conversations.";
      } else {
        const current = models.find((model) => model.alias === selected);
        if (current?.downloaded) saveSelection();
      }
      if (!status.textContent.startsWith("Checking model")) status.textContent = "";
    } catch (error) {
      status.textContent = error.message;
    }
  }

  contextSelect.addEventListener("change", saveSelection);
  effortSelect.addEventListener("change", saveSelection);
  picker.querySelector("[data-refresh]").addEventListener("click", refresh);
  picker.addEventListener("toggle", () => { if (picker.open) refresh(); });
  picker.addEventListener("keydown", (event) => {
    if (event.key === "Escape") { picker.open = false; summary.focus(); }
  });
  document.addEventListener("pointerdown", (event) => { if (!picker.contains(event.target)) picker.open = false; });
  document.body.append(picker);
  refresh();
})();
