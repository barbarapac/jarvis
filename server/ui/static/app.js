"use strict";

/* ============================================================
   JARVIS UI — controlador da SPA.
   Views: home / history / tools / settings.
   ============================================================ */

// ---------- Refs principais ----------
const transcript = document.getElementById("transcript");
const brandStatus = document.getElementById("brandStatus");
const bigReactor = document.getElementById("bigReactor");
const lastLine = document.getElementById("lastLine");
const inputEl = document.getElementById("input");
const composer = document.getElementById("composer");
const micBtn = document.getElementById("micBtn");
const capList = document.getElementById("capList");

const cfgSaveBtn = document.getElementById("cfgSave");
const cfgReloadBtn = document.getElementById("cfgReload");
const cfgHint = document.getElementById("cfgHint");

const mcpList = document.getElementById("mcpList");
const mcpAddBtn = document.getElementById("mcpAdd");
const mcpReloadBtn = document.getElementById("mcpReload");

const cmdList = document.getElementById("cmdList");
const cmdAddBtn = document.getElementById("cmdAdd");
const cmdCancelBtn = document.getElementById("cmdCancel");
const cmdToolSel = document.getElementById("cmdTool");
const cmdActionSel = document.getElementById("cmdAction");
const cmdParamsBox = document.getElementById("cmdParams");
const cmdTriggerInp = document.getElementById("cmdTrigger");
const cmdDescriptionInp = document.getElementById("cmdDescription");
const cmdHint = document.getElementById("cmdHint");

const MAX_ENTRIES = 200;
const TARGET_RATE = 16000; // taxa que Vosk espera

const STATUS_LABELS = {
  idle: "online",
  listening: "ouvindo...",
  speaking: "falando...",
  working: "executando...",
};

// ---------- State ----------
let currentConfig = null;
let micRecorder = null;

// ============================================================
//                      Roteamento de abas
// ============================================================

document.querySelectorAll(".nav-item").forEach((btn) => {
  btn.addEventListener("click", () => {
    const view = btn.dataset.view;
    document.querySelectorAll(".nav-item").forEach((n) => n.classList.toggle("active", n === btn));
    document.querySelectorAll(".view").forEach((v) => v.classList.toggle("active", v.dataset.view === view));
    if (view === "settings") loadConfig();
    if (view === "tools") loadMcps();
    if (view === "commands") loadCommands();
  });
});

// ============================================================
//                       Reator / status
// ============================================================

function setStatus(state) {
  brandStatus.textContent = STATUS_LABELS[state] || state;
  bigReactor.classList.remove("listening", "speaking", "working");
  if (state && state !== "idle") bigReactor.classList.add(state);
}

function setLastLine(text) {
  lastLine.textContent = text || "";
}

function setMicRecordingUi(recording) {
  const label = micBtn.querySelector(".mic-label");
  if (recording) {
    micBtn.classList.add("recording");
    if (label) label.textContent = "GRAVANDO...";
  } else {
    micBtn.classList.remove("recording");
    if (label) label.textContent = "FALAR";
  }
}

// ============================================================
//                       Histórico
// ============================================================

function addEntry({ kind, badge, text, url }) {
  const div = document.createElement("div");
  div.className = `entry entry-${kind}`;
  const badgeSpan = document.createElement("span");
  badgeSpan.className = "badge";
  badgeSpan.textContent = badge;
  const textSpan = document.createElement("span");
  textSpan.className = "text";
  textSpan.textContent = text;
  if (url) {
    const a = document.createElement("a");
    a.href = url;
    a.textContent = url;
    a.className = "url";
    a.target = "_blank";
    a.rel = "noopener";
    textSpan.appendChild(a);
  }
  div.appendChild(badgeSpan);
  div.appendChild(textSpan);
  transcript.appendChild(div);
  while (transcript.children.length > MAX_ENTRIES) {
    transcript.removeChild(transcript.firstChild);
  }
  transcript.scrollTop = transcript.scrollHeight;
}

// ============================================================
//                   WebSocket / eventos
// ============================================================

function handleEvent(evt) {
  const { type, data } = evt;
  switch (type) {
    case "status":
      setStatus(data.state);
      break;
    case "speaking_started":
      setStatus("speaking");
      setLastLine(data.text || "");
      addEntry({ kind: "jarvis", badge: "JARVIS", text: data.text || "" });
      break;
    case "speaking_ended":
      setStatus("idle");
      break;
    case "listening_started":
      setStatus("listening");
      setMicRecordingUi(true);
      setLastLine("Ouvindo...");
      break;
    case "listening_ended":
      setStatus("idle");
      // Só restaura o botão se NÃO for o gravador local (UI mic) — esse tem
      // ciclo próprio gerenciado por start/stopMic.
      if (!micRecorder) setMicRecordingUi(false);
      break;
    case "user_voice_transcribed":
      if (data.text) {
        addEntry({ kind: "user", badge: "VOCÊ", text: data.text });
        setLastLine(`Você: ${data.text}`);
      }
      break;
    case "user_text_input":
      addEntry({ kind: "user", badge: "VOCÊ", text: data.text });
      break;
    case "gitlab_announce":
      addEntry({ kind: "gitlab", badge: "GITLAB", text: data.text, url: data.url });
      break;
    case "review_started":
      setStatus("working");
      addEntry({
        kind: "system",
        badge: "REVIEW",
        text: `Iniciando revisão ${data.review_type || ""} em ${data.project || ""}@${data.branch || ""}`,
      });
      break;
    case "review_finished":
      setStatus("idle");
      addEntry({
        kind: "system",
        badge: "REVIEW",
        text: `Revisão concluída (${data.success ? "ok" : "falhou"})`,
      });
      break;
    case "log":
      addEntry({ kind: "system", badge: "LOG", text: data.text || "" });
      break;
  }
}

function connectWS() {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  const ws = new WebSocket(`${proto}//${location.host}/ws`);
  ws.onopen = () => setStatus("idle");
  ws.onmessage = (msg) => {
    try { handleEvent(JSON.parse(msg.data)); }
    catch (e) { console.error("evento inválido", e, msg.data); }
  };
  ws.onclose = () => {
    setStatus("idle");
    brandStatus.textContent = "desconectado";
    setTimeout(connectWS, 1500);
  };
  ws.onerror = (e) => console.error("ws error", e);
}

// ============================================================
//                       Comando texto
// ============================================================

async function sendCommand(text) {
  if (!text.trim()) return;
  try {
    await fetch("/api/command", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    });
  } catch (e) {
    addEntry({ kind: "error", badge: "ERRO", text: `Falha de envio: ${e.message}` });
  }
}

composer.addEventListener("submit", (e) => {
  e.preventDefault();
  const text = inputEl.value;
  inputEl.value = "";
  sendCommand(text);
});

// ============================================================
//                       Botão de mic
// ============================================================

class MicRecorder {
  constructor() {
    this.stream = null;
    this.audioCtx = null;
    this.processor = null;
    this.source = null;
    this.chunks = [];
    this.recording = false;
  }

  async start() {
    if (this.recording) return;
    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
    });
    this.audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    this.source = this.audioCtx.createMediaStreamSource(this.stream);

    // ScriptProcessor é deprecated mas funciona em todos os browsers e
    // é suficiente pra capturar PCM em chunks.
    const bufSize = 4096;
    this.processor = this.audioCtx.createScriptProcessor(bufSize, 1, 1);
    this.chunks = [];
    this.processor.onaudioprocess = (e) => {
      const data = e.inputBuffer.getChannelData(0);
      this.chunks.push(new Float32Array(data));
    };
    this.source.connect(this.processor);
    this.processor.connect(this.audioCtx.destination);
    this.recording = true;
  }

  async stop() {
    if (!this.recording) return null;
    this.recording = false;
    this.processor.disconnect();
    this.source.disconnect();
    const sourceRate = this.audioCtx.sampleRate;
    await this.audioCtx.close();
    this.stream.getTracks().forEach((t) => t.stop());
    this.audioCtx = null;
    this.stream = null;

    // Concatena chunks
    const totalLen = this.chunks.reduce((a, c) => a + c.length, 0);
    const merged = new Float32Array(totalLen);
    let offset = 0;
    for (const c of this.chunks) {
      merged.set(c, offset);
      offset += c.length;
    }
    this.chunks = [];

    // Downsample → 16kHz mono → int16 PCM
    const downsampled = downsample(merged, sourceRate, TARGET_RATE);
    return floatTo16BitPCM(downsampled);
  }
}

function downsample(buffer, fromRate, toRate) {
  if (fromRate === toRate) return buffer;
  const ratio = fromRate / toRate;
  const newLen = Math.round(buffer.length / ratio);
  const result = new Float32Array(newLen);
  let offsetResult = 0;
  let offsetBuffer = 0;
  while (offsetResult < newLen) {
    const next = Math.round((offsetResult + 1) * ratio);
    let accum = 0;
    let count = 0;
    for (let i = offsetBuffer; i < next && i < buffer.length; i++) {
      accum += buffer[i];
      count++;
    }
    result[offsetResult] = count > 0 ? accum / count : 0;
    offsetResult++;
    offsetBuffer = next;
  }
  return result;
}

function floatTo16BitPCM(input) {
  const out = new Int16Array(input.length);
  for (let i = 0; i < input.length; i++) {
    const s = Math.max(-1, Math.min(1, input[i]));
    out[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
  }
  return new Uint8Array(out.buffer);
}

async function startMic() {
  try {
    micRecorder = new MicRecorder();
    await micRecorder.start();
    setMicRecordingUi(true);
    setStatus("listening");
  } catch (e) {
    addEntry({ kind: "error", badge: "MIC", text: `Microfone falhou: ${e.message}` });
    micRecorder = null;
  }
}

async function stopMic() {
  if (!micRecorder) return;
  try {
    const pcm = await micRecorder.stop();
    micRecorder = null;
    setMicRecordingUi(false);
    if (!pcm || pcm.length < 1600) { // < 0.1s
      setStatus("idle");
      return;
    }
    setStatus("working");
    await fetch("/api/voice", {
      method: "POST",
      headers: { "Content-Type": "application/octet-stream" },
      body: pcm,
    });
  } catch (e) {
    addEntry({ kind: "error", badge: "MIC", text: `Falha enviando áudio: ${e.message}` });
  }
}

// Push-to-talk simples: clica → grava; clica de novo → para.
micBtn.addEventListener("click", async () => {
  if (micRecorder) await stopMic();
  else await startMic();
});

// ============================================================
//                    Configurações
// ============================================================

async function loadConfig() {
  try {
    const r = await fetch("/api/config");
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    currentConfig = await r.json();
    fillSettingsForm(currentConfig);
    cfgHint.textContent = "";
  } catch (e) {
    cfgHint.textContent = `Falha lendo config: ${e.message}`;
  }
}

function getByPath(obj, path) {
  let v = obj;
  for (const key of path) v = v == null ? undefined : v[key];
  return v;
}

function setByPath(obj, path, value) {
  let target = obj;
  for (let i = 0; i < path.length - 1; i++) {
    const k = path[i];
    target[k] = target[k] || {};
    target = target[k];
  }
  target[path[path.length - 1]] = value;
}

function fillSettingsForm(cfg) {
  document.querySelectorAll("[data-cfg]").forEach((el) => {
    const v = getByPath(cfg, el.dataset.cfg.split("."));
    if (el.type === "checkbox") el.checked = !!v;
    else el.value = v == null ? "" : v;
  });
  document.querySelectorAll("[data-cfg-list]").forEach((el) => {
    const v = getByPath(cfg, el.dataset.cfgList.split("."));
    el.value = Array.isArray(v) ? v.join("\n") : "";
  });
  document.querySelectorAll("[data-cfg-map]").forEach((el) => {
    const v = getByPath(cfg, el.dataset.cfgMap.split("."));
    if (v && typeof v === "object" && !Array.isArray(v)) {
      el.value = Object.entries(v).map(([k, val]) => `${k} = ${val}`).join("\n");
    } else {
      el.value = "";
    }
  });
}

function readSettingsForm() {
  const result = {};
  document.querySelectorAll("[data-cfg]").forEach((el) => {
    const path = el.dataset.cfg.split(".");
    let value;
    if (el.type === "checkbox") value = el.checked;
    else if (el.type === "number") value = el.value === "" ? null : Number(el.value);
    else value = el.value;
    setByPath(result, path, value);
  });
  document.querySelectorAll("[data-cfg-list]").forEach((el) => {
    const path = el.dataset.cfgList.split(".");
    const items = el.value
      .split("\n")
      .map((s) => s.trim())
      .filter(Boolean);
    setByPath(result, path, items);
  });
  document.querySelectorAll("[data-cfg-map]").forEach((el) => {
    const path = el.dataset.cfgMap.split(".");
    const map = {};
    for (const line of el.value.split("\n")) {
      const idx = line.indexOf("=");
      if (idx <= 0) continue;
      const k = line.slice(0, idx).trim();
      const v = line.slice(idx + 1).trim();
      if (k && v) map[k] = v;
    }
    // Marca chaves removidas como null para o backend deletá-las.
    const previous = currentConfig ? getByPath(currentConfig, path) : null;
    if (previous && typeof previous === "object" && !Array.isArray(previous)) {
      for (const k of Object.keys(previous)) {
        if (!(k in map)) map[k] = null;
      }
    }
    setByPath(result, path, map);
  });
  return result;
}

cfgSaveBtn.addEventListener("click", async () => {
  cfgHint.textContent = "Salvando...";
  try {
    const payload = readSettingsForm();
    const r = await fetch("/api/config", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    currentConfig = await r.json();
    cfgHint.textContent = "Salvo. Reinicie o Jarvis pra aplicar mudanças marcadas com ↻.";
  } catch (e) {
    cfgHint.textContent = `Falha ao salvar: ${e.message}`;
  }
});

cfgReloadBtn.addEventListener("click", () => loadConfig());

// ============================================================
//                          MCPs
// ============================================================

async function loadMcps() {
  try {
    const r = await fetch("/api/mcps");
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const data = await r.json();
    renderMcps(data.servers || []);
  } catch (e) {
    mcpList.innerHTML = `<div class="mcp-empty">Falha lendo MCPs: ${e.message}</div>`;
  }
}

function renderMcps(servers) {
  mcpList.innerHTML = "";
  if (!servers.length) {
    mcpList.innerHTML = `<div class="mcp-empty">Nenhum servidor MCP registrado. Adicione abaixo.</div>`;
    return;
  }
  for (const s of servers) {
    const row = document.createElement("div");
    let cls = "mcp-row";
    if (!s.enabled) cls += " disabled";
    else if (s.connected) cls += " connected";
    else cls += " error";
    row.className = cls;

    const cmdLine = `${s.command} ${(s.args || []).join(" ")}`.trim();
    const toolsText = s.error
      ? `erro: ${s.error}`
      : (s.tools && s.tools.length ? `${s.tools.length} tool(s): ${s.tools.join(", ")}` : "sem tools");

    row.innerHTML = `
      <div class="mcp-info">
        <div class="mcp-name">${escapeHtml(s.name)}</div>
        <div class="mcp-cmd">${escapeHtml(cmdLine)}</div>
        <div class="mcp-tools ${s.error ? "error" : ""}">${escapeHtml(toolsText)}</div>
      </div>
      <div class="mcp-actions">
        <button class="icon-btn" data-action="toggle" data-name="${escapeHtml(s.name)}">${s.enabled ? "Desligar" : "Ligar"}</button>
        <button class="icon-btn danger" data-action="remove" data-name="${escapeHtml(s.name)}">Remover</button>
      </div>
    `;
    mcpList.appendChild(row);
  }
  mcpList.querySelectorAll("button[data-action]").forEach((btn) => {
    btn.addEventListener("click", () => mcpAction(btn.dataset.action, btn.dataset.name));
  });
}

async function mcpAction(action, name) {
  // Lê a lista atual, modifica, faz PUT.
  const r = await fetch("/api/mcps");
  const data = await r.json();
  let servers = (data.servers || []).map((s) => ({
    name: s.name, command: s.command, args: s.args, env: {}, enabled: s.enabled,
  }));
  if (action === "remove") {
    servers = servers.filter((s) => s.name !== name);
  } else if (action === "toggle") {
    servers = servers.map((s) => s.name === name ? { ...s, enabled: !s.enabled } : s);
  }
  await fetch("/api/mcps", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ servers }),
  });
  loadMcps();
}

mcpAddBtn.addEventListener("click", async () => {
  const name = document.getElementById("mcpName").value.trim();
  const command = document.getElementById("mcpCommand").value.trim();
  const argsRaw = document.getElementById("mcpArgs").value.trim();
  const envRaw = document.getElementById("mcpEnv").value.trim();
  if (!name || !command) {
    alert("Nome e comando são obrigatórios.");
    return;
  }
  const args = argsRaw ? argsRaw.split("\n").map((s) => s.trim()).filter(Boolean) : [];
  const env = {};
  if (envRaw) {
    for (const line of envRaw.split("\n")) {
      const idx = line.indexOf("=");
      if (idx > 0) env[line.slice(0, idx).trim()] = line.slice(idx + 1).trim();
    }
  }

  const r = await fetch("/api/mcps");
  const data = await r.json();
  const existing = (data.servers || []).map((s) => ({
    name: s.name, command: s.command, args: s.args, env: {}, enabled: s.enabled,
  }));
  const merged = [...existing.filter((s) => s.name !== name), { name, command, args, env, enabled: true }];

  await fetch("/api/mcps", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ servers: merged }),
  });

  document.getElementById("mcpName").value = "";
  document.getElementById("mcpCommand").value = "";
  document.getElementById("mcpArgs").value = "";
  document.getElementById("mcpEnv").value = "";
  loadMcps();
});

mcpReloadBtn.addEventListener("click", async () => {
  await fetch("/api/mcps/reload", { method: "POST" });
  loadMcps();
});

// ============================================================
//                       Comandos
// ============================================================

let commandsCatalog = [];
let commandsList = [];
let editingCommandIndex = null;

async function loadCommands() {
  try {
    const [catalogRes, commandsRes] = await Promise.all([
      fetch("/api/commands/catalog"),
      fetch("/api/commands"),
    ]);
    if (!catalogRes.ok) throw new Error(`catalog HTTP ${catalogRes.status}`);
    if (!commandsRes.ok) throw new Error(`commands HTTP ${commandsRes.status}`);
    const catalogData = await catalogRes.json();
    const cmdData = await commandsRes.json();
    commandsCatalog = catalogData.tools || [];
    commandsList = cmdData.commands || [];
    populateToolSelect();
    cancelEditCommand();
  } catch (e) {
    cmdList.innerHTML = `<div class="mcp-empty">Falha lendo comandos: ${escapeHtml(e.message)}</div>`;
  }
}

function populateToolSelect() {
  cmdToolSel.innerHTML = "";
  if (!commandsCatalog.length) {
    const opt = document.createElement("option");
    opt.textContent = "(nenhuma ferramenta ativa)";
    opt.disabled = true;
    cmdToolSel.appendChild(opt);
    cmdActionSel.innerHTML = "";
    cmdParamsBox.innerHTML = "";
    return;
  }
  for (const tool of commandsCatalog) {
    const opt = document.createElement("option");
    opt.value = tool.name;
    opt.textContent = tool.label;
    cmdToolSel.appendChild(opt);
  }
  cmdToolSel.value = commandsCatalog[0].name;
  populateActionSelect();
}

function populateActionSelect() {
  const tool = commandsCatalog.find((t) => t.name === cmdToolSel.value);
  cmdActionSel.innerHTML = "";
  if (!tool) return;
  for (const action of tool.actions) {
    const opt = document.createElement("option");
    opt.value = action.name;
    opt.textContent = action.label;
    cmdActionSel.appendChild(opt);
  }
  if (tool.actions.length) {
    cmdActionSel.value = tool.actions[0].name;
    renderParamsForm();
  }
}

function renderParamsForm() {
  cmdParamsBox.innerHTML = "";
  const tool = commandsCatalog.find((t) => t.name === cmdToolSel.value);
  const action = tool && tool.actions.find((a) => a.name === cmdActionSel.value);
  if (!action || !action.params.length) return;
  const wrap = document.createElement("div");
  wrap.className = "form-grid";
  for (const p of action.params) {
    const label = document.createElement("label");
    label.className = "full";
    const text = document.createTextNode(`${p.label}${p.required ? " *" : ""}`);
    label.appendChild(text);
    const input = document.createElement("input");
    input.type = p.type === "number" ? "number" : "text";
    input.dataset.paramName = p.name;
    input.placeholder = p.placeholder || "";
    label.appendChild(input);
    wrap.appendChild(label);
  }
  cmdParamsBox.appendChild(wrap);
}

cmdToolSel.addEventListener("change", populateActionSelect);
cmdActionSel.addEventListener("change", renderParamsForm);

function renderCommands() {
  cmdList.innerHTML = "";
  if (!commandsList.length) {
    cmdList.innerHTML = `<div class="mcp-empty">Nenhum comando cadastrado. Adicione abaixo. Built-ins continuam funcionando sem cadastro.</div>`;
    return;
  }
  for (let i = 0; i < commandsList.length; i++) {
    const c = commandsList[i];
    const paramsText = c.params && Object.keys(c.params).length
      ? `(${Object.entries(c.params).map(([k, v]) => `${k}: ${v}`).join(", ")})`
      : "";
    const row = document.createElement("div");
    row.className = "mcp-row connected";
    if (editingCommandIndex === i) row.classList.add("editing");
    row.innerHTML = `
      <div class="mcp-info">
        <div class="mcp-name">${escapeHtml(c.trigger)}</div>
        <div class="mcp-cmd">${escapeHtml(c.description || "")}</div>
        <div class="mcp-tools">${escapeHtml(c.tool)}.${escapeHtml(c.action)}${escapeHtml(paramsText)}</div>
      </div>
      <div class="mcp-actions">
        <button class="icon-btn" data-action="edit" type="button">Editar</button>
        <button class="icon-btn danger" data-action="remove" type="button">Remover</button>
      </div>
    `;
    row.querySelector('[data-action="edit"]').addEventListener("click", () => startEditCommand(i));
    row.querySelector('[data-action="remove"]').addEventListener("click", () => removeCommand(i));
    cmdList.appendChild(row);
  }
}

function startEditCommand(index) {
  const c = commandsList[index];
  if (!c) return;
  editingCommandIndex = index;
  cmdTriggerInp.value = c.trigger || "";
  cmdDescriptionInp.value = c.description || "";

  // Garante que tool/action existem no catálogo antes de selecionar.
  if (commandsCatalog.find((t) => t.name === c.tool)) {
    cmdToolSel.value = c.tool;
  }
  populateActionSelect();
  if (cmdActionSel.querySelector(`option[value="${c.action}"]`)) {
    cmdActionSel.value = c.action;
  }
  renderParamsForm();
  for (const [name, value] of Object.entries(c.params || {})) {
    const el = cmdParamsBox.querySelector(`[data-param-name="${name}"]`);
    if (el) el.value = value;
  }

  cmdAddBtn.textContent = "Salvar";
  cmdCancelBtn.style.display = "";
  cmdHint.textContent = `Editando: ${c.trigger}`;
  renderCommands();
  cmdAddBtn.scrollIntoView({ behavior: "smooth", block: "center" });
}

function cancelEditCommand() {
  editingCommandIndex = null;
  cmdTriggerInp.value = "";
  cmdDescriptionInp.value = "";
  if (commandsCatalog.length) {
    cmdToolSel.value = commandsCatalog[0].name;
    populateActionSelect();
  }
  cmdAddBtn.textContent = "Adicionar";
  cmdCancelBtn.style.display = "none";
  cmdHint.textContent = "";
  renderCommands();
}

async function saveCommands(commands) {
  const r = await fetch("/api/commands", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ commands }),
  });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  const data = await r.json();
  commandsList = data.commands || [];
  renderCommands();
}

async function removeCommand(index) {
  cmdHint.textContent = "Removendo...";
  try {
    const next = commandsList.filter((_, i) => i !== index);
    await saveCommands(next);
    cmdHint.textContent = "Removido. Reinicie pra aplicar.";
  } catch (e) {
    cmdHint.textContent = `Falha: ${e.message}`;
  }
}

cmdAddBtn.addEventListener("click", async () => {
  const trigger = cmdTriggerInp.value.trim();
  const description = cmdDescriptionInp.value.trim();
  const tool = cmdToolSel.value;
  const action = cmdActionSel.value;
  if (!trigger || !tool || !action) {
    cmdHint.textContent = "Preencha gatilho, ferramenta e ação.";
    return;
  }
  const params = {};
  cmdParamsBox.querySelectorAll("[data-param-name]").forEach((el) => {
    const name = el.dataset.paramName;
    const val = el.value.trim();
    if (val !== "") params[name] = el.type === "number" ? Number(val) : val;
  });
  const newCmd = { trigger, description, tool, action, params };
  cmdHint.textContent = "Salvando...";
  try {
    let next;
    if (editingCommandIndex !== null) {
      next = commandsList.map((c, i) => (i === editingCommandIndex ? newCmd : c));
    } else {
      next = [...commandsList, newCmd];
    }
    await saveCommands(next);
    cancelEditCommand();
    cmdHint.textContent = "Salvo. Reinicie o Jarvis pra aplicar.";
  } catch (e) {
    cmdHint.textContent = `Falha ao salvar: ${e.message}`;
  }
});

cmdCancelBtn.addEventListener("click", cancelEditCommand);

// ============================================================
//                       Capabilities
// ============================================================

async function loadCapabilities() {
  try {
    const r = await fetch("/api/status");
    if (!r.ok) return;
    const data = await r.json();
    const caps = data.capabilities || {};
    const items = [
      ["STT", caps.stt],
      ["Spotify", caps.spotify],
      ["Code review", caps.code_review],
      ["Agente Claude", caps.agent],
      ["Wake word", caps.wake_word],
    ];
    capList.innerHTML = items.map(([label, on]) =>
      `<div class="cap"><span class="dot ${on ? "on" : "off"}"></span>${escapeHtml(label)}</div>`
    ).join("");
  } catch (_) { /* silencioso */ }
}

// ============================================================
//                        Utils
// ============================================================

function escapeHtml(s) {
  if (s == null) return "";
  return String(s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

// ============================================================
//                          Boot
// ============================================================

connectWS();
loadCapabilities();
inputEl.focus();
