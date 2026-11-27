(() => {
  const api = "/canvas/local-models";
  const picker = document.createElement("details");
  picker.className = "local-model-picker";
  picker.innerHTML = `
    <summary aria-label="Select model for new conversations">Model: loading…</summary>
    <div class="local-model-menu">
      <div class="local-model-heading"><strong>Model for new conversations</strong><button type="button" data-refresh aria-label="Refresh models">↻</button></div>
      <div class="local-model-list" role="list"></div>
      <label data-effort-label hidden>Reasoning effort <select data-effort></select></label>
      <p data-model-status role="status" aria-live="polite"></p>
      <p class="local-model-help">A conversation keeps its selected model. The shared backend queues requests while it loads another model.</p>
    </div>`;
  const contextPicker = document.createElement("details");
  contextPicker.className = "local-context-picker";
  contextPicker.innerHTML = `
    <summary aria-label="Select context window for new conversations">Context: loading…</summary>
    <div class="local-context-menu"><label>Context window <select data-context disabled></select></label></div>`;
  const summary = picker.querySelector("summary");
  const contextSummary = contextPicker.querySelector("summary");
  const list = picker.querySelector(".local-model-list");
  const contextSelect = contextPicker.querySelector("[data-context]");
  const effortLabel = picker.querySelector("[data-effort-label]");
  const effortSelect = picker.querySelector("[data-effort]");
  const status = picker.querySelector("[data-model-status]");
  let mountedComposer = null;
  const requestHeaders = () => {
    let storedKey = "";
    try { storedKey = JSON.parse(localStorage.getItem("openhands-agent-server-config") || "{}").sessionApiKey || ""; } catch { /* clean setup */ }
    return { "X-Session-API-Key": window.__AGENT_CANVAS_SESSION_API_KEY__ || storedKey };
  };
  let contexts = [4096, 8192, 16384, 32768, 65536, 100000, 131072, 262144];
  let models = [];
  let contextLimit = 8192;
  let selected = "";
  let selectedQuant = "";
  let selectionVersion = 0;
  let lastRefresh = 0;
  let refreshPromise = null;

  const readCookie = (name) => {
    const item = document.cookie.split(";").map((part) => part.trim()).find((part) => part.startsWith(`${name}=`));
    if (!item) return "";
    try { return decodeURIComponent(item.slice(name.length + 1)); } catch { return ""; }
  };
  const setCookie = (name, value) => {
    const secure = location.protocol === "https:" ? "; Secure" : "";
    document.cookie = `${name}=${encodeURIComponent(value)}; Path=/; SameSite=Strict; Max-Age=2592000${secure}`;
  };
  const starterContexts = contexts.filter((value) => value <= contextLimit);
  contextSelect.replaceChildren(...starterContexts.map((value) => new Option(`${value.toLocaleString()} tokens`, String(value))));
  contextSelect.value = String(starterContexts.includes(Number(readCookie("openhands_context")))
    ? Number(readCookie("openhands_context")) : starterContexts.at(-1));
  contextSelect.disabled = starterContexts.length === 0;
  contextSummary.textContent = `Context: ${Number(contextSelect.value || 0).toLocaleString()}`;
  const variant = (alias, quant, context) => `openhands-${alias}-q${quant}-ctx${context}`;
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
    setCookie("openhands_quant", selectedQuant);
    setCookie("openhands_context", contextSelect.value || "8192");
    setCookie("openhands_effort", effortSelect.value === "default" ? "" : effortSelect.value);
    const model = selectedModel();
    summary.textContent = `Model: ${model?.label || selected}${selectedQuant ? ` (${selectedQuant})` : ""}`;
    contextSummary.textContent = `Context: ${Number(contextSelect.value || 0).toLocaleString()}`;
  }

  function selectModel(model, quant = model.quant) {
    const version = ++selectionVersion;
    selected = model.alias;
    selectedQuant = quant;
    renderModels();
    const availableContexts = contexts.filter((value) => value <= contextLimit);
    const savedContext = Number(readCookie("openhands_context"));
    const defaultContext = availableContexts.includes(8192) ? 8192 : availableContexts.at(-1);
    contextSelect.replaceChildren(...availableContexts.map((value) => new Option(`${value.toLocaleString()} tokens`, String(value))));
    contextSelect.value = availableContexts.includes(savedContext) ? String(savedContext) : String(defaultContext);
    contextSelect.disabled = availableContexts.length === 0;
    contextSummary.textContent = `Context: ${Number(contextSelect.value || 0).toLocaleString()}`;
    effortLabel.hidden = true;
    effortSelect.replaceChildren(new Option("Model default", "default"));
    effortSelect.value = "default";
    status.textContent = "Checking model capabilities…";
    const modelContext = model.default_context || defaultContext;
    const id = variant(model.alias, quant, modelContext);
    apiRequest(`/properties?model=${encodeURIComponent(id)}`).then((result) => {
      if (version !== selectionVersion) return;
      const maxContext = Math.min(contextLimit, Number(result.context_train) || contextLimit);
      const options = contexts.filter((value) => value <= maxContext);
      if (modelContext <= maxContext && !options.includes(modelContext)) options.push(modelContext);
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

  async function watchDownload(model, choice, button) {
    button.disabled = true;
    const row = button.closest(".local-model-quant-row");
    const progress = row.querySelector("progress");
    const state = row.querySelector("[data-download-status]");
    progress.hidden = false;
    progress.value = 0;
    state.textContent = "Starting download…";
    try {
      const context = Math.min(8192, contextLimit);
      const started = await apiRequest("/download", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ alias: model.alias, quant: choice.quant, context }),
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
          if (event.model && event.model !== choice.model_id) continue;
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
    const familyFor = (model) => {
      const name = `${model.alias} ${model.label}`.toLowerCase();
      for (const [family, pattern] of [["Nemotron", /nemotron/], ["Qwen", /qwen/], ["Llama", /llama/], ["DeepSeek", /deepseek/], ["Gemma", /gemma/], ["GPT-OSS", /gpt[- ]oss/], ["GLM", /\bglm\b/]]) {
        if (pattern.test(name)) return family;
      }
      return "Other models";
    };
    const families = new Map();
    for (const model of models) {
      const family = familyFor(model);
      if (!families.has(family)) families.set(family, []);
      families.get(family).push(model);
    }
    for (const [family, familyModels] of families) {
      const familyRow = document.createElement("details");
      familyRow.className = "local-model-family";
      const familySummary = document.createElement("summary");
      familySummary.textContent = `${family} · ${familyModels.length} models`;
      if (familyModels.some((model) => model.quantizations?.some((choice) => choice.downloaded))) familySummary.classList.add("is-ready");
      familyRow.append(familySummary);
      for (const model of familyModels) {
        const modelRow = document.createElement("details");
        modelRow.className = "local-model-row";
        const modelSummary = document.createElement("summary");
        const choices = model.quantizations || [{ quant: model.quant, downloaded: model.downloaded, status: model.status, model_id: model.default_model_id }];
        modelSummary.textContent = `${model.label}${model.alias === selected ? ` · selected ${selectedQuant}` : ""}`;
        if (choices.some((choice) => choice.downloaded)) modelSummary.classList.add("is-ready");
        modelRow.append(modelSummary);
        for (const choice of choices) {
          const quantRow = document.createElement("div");
          quantRow.className = "local-model-quant-row";
          const choose = document.createElement("button");
          choose.type = "button";
          choose.className = "local-model-choice";
          choose.textContent = `${choice.quant}${choice.downloaded ? " · ready" : ""}`;
          choose.disabled = !choice.downloaded;
          choose.setAttribute("aria-pressed", String(model.alias === selected && choice.quant === selectedQuant));
          choose.addEventListener("click", () => selectModel(model, choice.quant));
          const download = document.createElement("button");
          download.type = "button";
          download.className = "local-model-download";
          const downloading = choice.status === "downloading";
          download.textContent = choice.downloaded ? "Ready" : downloading ? "Downloading" : "Download";
          download.disabled = choice.downloaded || downloading;
          download.addEventListener("click", () => watchDownload(model, choice, download));
          const progress = document.createElement("progress");
          progress.max = 100;
          progress.hidden = !downloading;
          const progressText = document.createElement("span");
          progressText.dataset.downloadStatus = "";
          progressText.className = "local-model-download-status";
          if (downloading) {
            const files = Object.values(choice.progress || {}).filter((item) => item && Number.isFinite(item.done) && Number.isFinite(item.total));
            const total = files.reduce((sum, file) => sum + file.total, 0);
            const doneBytes = files.reduce((sum, file) => sum + file.done, 0);
            progress.value = total ? Math.floor(100 * doneBytes / total) : 0;
            progressText.textContent = total ? `Downloading ${progress.value}%` : "Download in progress";
          }
          quantRow.append(choose, download, progress, progressText);
          modelRow.append(quantRow);
        }
        familyRow.append(modelRow);
      }
      list.append(familyRow);
    }
  }

  async function refresh(force = false) {
    if (!force && models.length && Date.now() - lastRefresh < 15000) return;
    if (refreshPromise) return refreshPromise;
    status.textContent = "Refreshing model list…";
    refreshPromise = (async () => { try {
      const result = await apiRequest("/catalog");
      models = result.models || [];
      lastRefresh = Date.now();
      contextLimit = Number(result.context_limit) || 8192;
      contexts = result.contexts?.length ? result.contexts : contexts.filter((value) => value <= contextLimit);
      renderModels();
      if (!selected) {
        const saved = readCookie("openhands_model");
        const savedQuant = readCookie("openhands_quant");
        const savedModel = models.find((model) => model.alias === saved);
        const initial = [
          savedModel && { model: savedModel, choice: savedModel.quantizations?.find((item) => item.quant === savedQuant && item.downloaded) },
          ...models.map((model) => ({ model, choice: model.quantizations?.find((item) => item.quant === model.quant && item.downloaded) })),
          ...models.map((model) => ({ model, choice: model.quantizations?.find((item) => item.downloaded) })),
        ].find((item) => item?.choice);
        if (initial) selectModel(initial.model, initial.choice.quant);
        else status.textContent = "Download a model to use it in new conversations.";
      } else {
        const current = models.find((model) => model.alias === selected);
        const currentQuant = current?.quantizations?.find((item) => item.quant === selectedQuant);
        if (currentQuant?.downloaded) saveSelection();
      }
      if (!status.textContent.startsWith("Checking model")) status.textContent = "";
    } catch (error) {
      status.textContent = error.message;
    } finally { refreshPromise = null; } })();
    return refreshPromise;
  }

  contextSelect.addEventListener("change", saveSelection);
  effortSelect.addEventListener("change", saveSelection);
  picker.querySelector("[data-refresh]").addEventListener("click", () => refresh(true));
  picker.addEventListener("toggle", () => { if (picker.open) refresh(); });
  picker.addEventListener("keydown", (event) => {
    if (event.key === "Escape") { picker.open = false; summary.focus(); }
  });
  contextPicker.addEventListener("keydown", (event) => {
    if (event.key === "Escape") { contextPicker.open = false; contextSummary.focus(); }
  });
  contextSelect.addEventListener("change", () => { saveSelection(); contextPicker.open = false; });
  document.addEventListener("pointerdown", (event) => {
    if (!picker.contains(event.target)) picker.open = false;
    if (!contextPicker.contains(event.target)) contextPicker.open = false;
  });

  // The canvas renders a new composer when navigating between home and a
  // conversation. Mount beside its built-in agent/profile controls and watch
  // for React replacing that area.
  function mountInComposer() {
    const profile = document.querySelector('[data-testid="chat-input-llm-profile"]');
    const actions = profile?.closest('[data-testid="chat-input-actions"]')
      || profile?.parentElement?.parentElement;
    if (!actions) return;
    if (mountedComposer !== actions || !actions.contains(picker) || !actions.contains(contextPicker)) {
      picker.remove();
      contextPicker.remove();
      actions.append(picker, contextPicker);
      mountedComposer = actions;
    }
  }
  const observer = new MutationObserver(mountInComposer);
  observer.observe(document.body, { childList: true, subtree: true });
  mountInComposer();
  refresh();
})();
