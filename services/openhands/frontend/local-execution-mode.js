(() => {
  const picker = document.createElement("details");
  picker.className = "local-execution-picker";
  picker.innerHTML = `
    <summary aria-label="Execution mode for new conversations">Execution: Docker</summary>
    <div class="local-execution-menu">
      <strong>Run new chats in</strong>
      <button type="button" data-mode="docker">Docker</button>
      <button type="button" data-mode="wsl" disabled>My PC (WSL)</button>
      <p>My PC uses your WSL user, shell, tools, and files. Docker stays in the mounted project.</p>
      <span role="status" aria-live="polite"></span>
    </div>`;
  const summary = picker.querySelector("summary");
  const status = picker.querySelector("[role=status]");
  const buttons = [...picker.querySelectorAll("button")];
  const host = buttons.find((button) => button.dataset.mode === "wsl");
  const saved = () => document.cookie.split(";").some((value) => value.trim() === "openhands_terminal=wsl");
  const display = () => {
    summary.textContent = saved() ? "Execution: My PC (WSL)" : "Execution: Docker";
    for (const button of buttons) {
      button.setAttribute("aria-pressed", String(button.dataset.mode === (saved() ? "wsl" : "docker")));
    }
  };
  const refresh = async () => {
    host.disabled = true;
    try {
      const result = await fetch("/canvas/local-execution/status", { cache: "no-store" });
      if (!result.ok) throw new Error();
      host.disabled = !(await result.json()).available;
    } catch {
      host.disabled = true;
    }
    status.textContent = host.disabled ? "WSL terminal unavailable." : "";
  };
  for (const button of buttons) {
    button.addEventListener("click", () => {
      const secure = location.protocol === "https:" ? "; Secure" : "";
      document.cookie = `openhands_terminal=${button.dataset.mode}; Path=/; SameSite=Strict; Max-Age=2592000${secure}`;
      display();
      picker.open = false;
      summary.focus();
    });
  }
  picker.addEventListener("toggle", () => { if (picker.open) refresh(); });
  picker.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      picker.open = false;
      summary.focus();
    }
  });
  document.addEventListener("pointerdown", (event) => {
    if (!picker.contains(event.target)) picker.open = false;
  });
  display();
  document.body.append(picker);
})();
