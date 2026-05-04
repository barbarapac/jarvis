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

// Home cockpit refs
const sysDate = document.getElementById("sysDate");
const sysTime = document.getElementById("sysTime");
const homeStatusDots = {
  agent: { dot: document.getElementById("dotAgent"), val: document.getElementById("valAgent") },
  stt: { dot: document.getElementById("dotStt"), val: document.getElementById("valStt") },
  vault: { dot: document.getElementById("dotVault"), val: document.getElementById("valVault") },
  spotify: { dot: document.getElementById("dotSpotify"), val: document.getElementById("valSpotify") },
  wake_word: { dot: document.getElementById("dotWake"), val: document.getElementById("valWake") },
};
const ovNotes = document.getElementById("ovNotes");
const ovLinks = document.getElementById("ovLinks");
const ovToday = document.getElementById("ovToday");
const ovWeek = document.getElementById("ovWeek");
const ovPath = document.getElementById("ovPath");
const openObsidianBtn = document.getElementById("openObsidianBtn");
const openExplorerBtn = document.getElementById("openExplorerBtn");
const brainGraph = document.getElementById("brainGraph");
const brainMeta = document.getElementById("brainMeta");
const brainEmpty = document.getElementById("brainEmpty");

let vaultRootPath = "";

// Mega-Brain — botões de cabeçalho (ingerir conhecimento, sintetizar memórias)
const synthBtn = document.getElementById("synthBtn");
const synthModal = document.getElementById("synthModal");
const synthBody = document.getElementById("synthBody");
const synthClose = document.getElementById("synthClose");
const ingestBtn = document.getElementById("ingestBtn");

// Mega-Brain — métricas + última nota
const mbNotes = document.getElementById("mbNotes");
const mbLinks = document.getElementById("mbLinks");
const mbToday = document.getElementById("mbToday");
const mbWeek = document.getElementById("mbWeek");
const mbMcps = document.getElementById("mbMcps");
const mbInteracoes = document.getElementById("mbInteracoes");
const mbRecent = document.getElementById("mbRecent");
const mbRecentTitle = document.getElementById("mbRecentTitle");
const mbRecentMeta = document.getElementById("mbRecentMeta");
const ingestModal = document.getElementById("ingestModal");
const ingestClose = document.getElementById("ingestClose");
const ingestForm = document.getElementById("ingestForm");
const ingestTitle = document.getElementById("ingestTitle");
const ingestContent = document.getElementById("ingestContent");
const ingestSubmit = document.getElementById("ingestSubmit");
const ingestHint = document.getElementById("ingestHint");
const toastEl = document.getElementById("toast");

const cfgSaveBtn = document.getElementById("cfgSave");
const cfgReloadBtn = document.getElementById("cfgReload");
const cfgHint = document.getElementById("cfgHint");

const secretsList = document.getElementById("secretsList");

const mcpList = document.getElementById("mcpList");
const mcpGlobalList = document.getElementById("mcpGlobalList");
const mcpLocalMeta = document.getElementById("mcpLocalMeta");
const mcpGlobalMeta = document.getElementById("mcpGlobalMeta");
const mcpAddBtn = document.getElementById("mcpAdd");
const mcpReloadBtn = document.getElementById("mcpReload");
const mcpTransportSel = document.getElementById("mcpTransport");

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
    if (view === "settings") {
      loadConfig();
      loadMcps();
      loadCommands();
    }
    if (view === "megabrain") {
      refreshBrainGraph();
      refreshBrainMetrics();
    }
  });
});

// Sub-abas dentro de Configurações.
document.querySelectorAll(".settings-tab").forEach((btn) => {
  btn.addEventListener("click", () => {
    const tab = btn.dataset.stab;
    document.querySelectorAll(".settings-tab").forEach((n) => {
      const active = n === btn;
      n.classList.toggle("active", active);
      n.setAttribute("aria-selected", active ? "true" : "false");
    });
    document.querySelectorAll(".settings-pane").forEach((p) => {
      p.classList.toggle("active", p.dataset.spane === tab);
    });
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

  // Mensagens do Jarvis vêm de um LLM e podem trazer markdown (parágrafos,
  // listas, **negrito**, `código`). Renderizamos com um parser próprio
  // pra não trazer dependência externa. Demais entries (user, tool log,
  // erros) ficam como texto puro pra evitar surpresa de injeção.
  if (kind === "jarvis") {
    textSpan.innerHTML = renderMarkdownLite(text || "");
  } else {
    textSpan.textContent = text || "";
  }

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

// Markdown leve: cobre o que o Claude tipicamente devolve. Escapa o texto
// ANTES de aplicar regexes de inline pra não permitir injeção de HTML
// arbitrário (ex: <script>) — só geramos as tags que controlamos.
function renderMarkdownLite(src) {
  if (!src) return "";

  // 1) Code blocks ``` ... ``` — extrai e substitui por placeholders pra
  //    que o resto dos regexes não estraguem o conteúdo do bloco.
  const blocks = [];
  src = String(src).replace(/```([a-zA-Z0-9_-]*)\n?([\s\S]*?)```/g, (_m, lang, code) => {
    const idx = blocks.length;
    blocks.push({ lang: lang || "", code });
    return ` CODEBLOCK${idx} `;
  });

  // 2) Escape do texto inteiro.
  src = escapeHtml(src);

  // 3) Inline code `…` — reaproveita placeholders pra não conflitar com **/*.
  const inlines = [];
  src = src.replace(/`([^`\n]+)`/g, (_m, code) => {
    const idx = inlines.length;
    inlines.push(code);
    return ` INLINE${idx} `;
  });

  // 4) Inline: bold, italic, links.
  src = src
    .replace(/\*\*([^*\n]+?)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[^*])\*([^*\n]+?)\*(?!\*)/g, "$1<em>$2</em>")
    .replace(/\[([^\]]+)\]\(((?:https?:|mailto:)[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');

  // 5) Quebra em blocos por linha em branco; processa listas e cabeçalhos.
  const blocksHtml = src.split(/\n{2,}/).map((block) => {
    const lines = block.split("\n");
    // Heading (# / ## / ###).
    const heading = lines[0].match(/^(#{1,3})\s+(.+)$/);
    if (heading && lines.length === 1) {
      const level = heading[1].length;
      return `<h${level + 2} class="md-h">${heading[2]}</h${level + 2}>`;
    }
    // Lista não ordenada.
    if (lines.every((l) => /^\s*[-*+]\s+/.test(l))) {
      const items = lines.map((l) => `<li>${l.replace(/^\s*[-*+]\s+/, "")}</li>`).join("");
      return `<ul class="md-ul">${items}</ul>`;
    }
    // Lista ordenada.
    if (lines.every((l) => /^\s*\d+\.\s+/.test(l))) {
      const items = lines.map((l) => `<li>${l.replace(/^\s*\d+\.\s+/, "")}</li>`).join("");
      return `<ol class="md-ol">${items}</ol>`;
    }
    // Parágrafo: preserva quebras simples.
    return `<p class="md-p">${lines.join("<br>")}</p>`;
  }).join("");

  // 6) Re-injetar inline code e code blocks.
  let html = blocksHtml.replace(/ INLINE(\d+) /g, (_m, i) => {
    return `<code class="md-code-inline">${escapeHtml(inlines[Number(i)])}</code>`;
  });
  html = html.replace(/ CODEBLOCK(\d+) /g, (_m, i) => {
    const b = blocks[Number(i)];
    const langClass = b.lang ? ` data-lang="${escapeHtml(b.lang)}"` : "";
    return `<pre class="md-pre"${langClass}><code>${escapeHtml(b.code)}</code></pre>`;
  });

  return html;
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
    case "agent_turn_started":
      // O reator já entra em "working" e o user_text é registrado pelo
      // user_text_input/user_voice_transcribed; sem duplicar entrada aqui.
      break;
    case "agent_thinking":
      // Estado pulsante do reator já reflete "pensando"; sem ruído no histórico.
      break;
    case "agent_tool_call":
      addEntry({ kind: "system", badge: "TOOL", text: `${data.tool || ""}${data.args_preview ? ` — ${data.args_preview}` : ""}` });
      break;
    case "agent_tool_result":
      // O retorno do tool já é absorvido pela resposta final; manter histórico enxuto.
      break;
    case "agent_turn_ended":
      if (data.assistant_text) addEntry({ kind: "jarvis", badge: "JARVIS", text: data.assistant_text });
      break;
    case "jarvis_initiative":
      if (data.text) addEntry({ kind: "jarvis", badge: (data.label || "INICIATIVA").toUpperCase(), text: data.text });
      break;
    case "log":
      addEntry({ kind: "system", badge: "LOG", text: data.text || "" });
      break;
  }
}

// ============================================================
//                      Vault — refresh global
// ============================================================

// Chamado depois de qualquer mutação no vault (ingest, apply de síntese).
// Mantém home + grafo + métricas do Mega-Brain consistentes.
function refreshVaultEverywhere() {
  refreshHomeVault();
  refreshBrainGraph();
  refreshBrainMetrics();
}

async function refreshBrainMetrics() {
  // Stats do vault — notas, ingestões, última nota aprendida.
  try {
    const r = await fetch("/api/vault/stats");
    if (r.ok) {
      const data = await r.json();
      const s = data.stats || {};
      if (mbNotes) mbNotes.textContent = s.notas_total ?? 0;
      if (mbLinks) mbLinks.textContent = s.wikilinks_total ?? 0;
      if (mbToday) mbToday.textContent = s.ingestoes_hoje ?? 0;
      if (mbWeek) mbWeek.textContent = s.ingestoes_7dias ?? 0;
      if (mbInteracoes) mbInteracoes.textContent = s.interacoes_hoje ?? 0;

      const last = s.last_ingest;
      if (mbRecent) {
        if (last && last.title) {
          mbRecentTitle.textContent = last.title;
          const when = last.ingested_at ? formatRelativeTime(last.ingested_at) : "";
          mbRecentMeta.textContent = [last.rel_path, when].filter(Boolean).join(" · ");
          mbRecent.hidden = false;
        } else {
          mbRecent.hidden = true;
        }
      }
    }
  } catch (_) { /* silencia — métricas são best-effort */ }

  // MCPs conectados — só conta locais (globais detectados ainda não estão ativos).
  try {
    const r = await fetch("/api/mcps");
    if (r.ok) {
      const data = await r.json();
      const locals = (data.servers || []).filter((s) => (s.source || "local") === "local");
      const connected = locals.filter((s) => s.connected).length;
      if (mbMcps) mbMcps.textContent = `${connected}/${locals.length}`;
    }
  } catch (_) { /* idem */ }
}

function formatRelativeTime(iso) {
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return "";
  const diffSec = Math.max(0, Math.floor((Date.now() - t) / 1000));
  if (diffSec < 60) return "agora";
  if (diffSec < 3600) return `há ${Math.floor(diffSec / 60)}min`;
  if (diffSec < 86400) return `há ${Math.floor(diffSec / 3600)}h`;
  return `há ${Math.floor(diffSec / 86400)}d`;
}

// ============================================================
//                      Síntese de memórias
// ============================================================

function showSynthModal() {
  synthModal.style.display = "flex";
  synthBody.innerHTML = `<div class="synth-loading">Pensando, Senhora...</div>`;
}

function hideSynthModal() {
  synthModal.style.display = "none";
}

async function runSynthesis() {
  showSynthModal();
  try {
    const r = await fetch("/api/vault/synthesize", { method: "POST" });
    if (!r.ok) {
      const detail = await r.text();
      synthBody.innerHTML = `<div class="synth-error">Falha: ${escapeHtml(detail)}</div>`;
      return;
    }
    const data = await r.json();
    renderProposals(data.proposals || []);
  } catch (e) {
    synthBody.innerHTML = `<div class="synth-error">Erro: ${escapeHtml(e.message)}</div>`;
  }
}

function renderProposals(proposals) {
  if (!proposals.length) {
    synthBody.innerHTML = `<div class="synth-empty">Nada relevante a promover, Senhora. Tudo certo no vault.</div>`;
    return;
  }
  synthBody.innerHTML = "";
  for (let i = 0; i < proposals.length; i++) {
    synthBody.appendChild(renderProposalCard(proposals[i], i));
  }
}

function renderProposalCard(proposal, index) {
  const card = document.createElement("div");
  card.className = "synth-card";
  card.dataset.index = index;

  const head = document.createElement("div");
  head.className = "synth-head";
  const fileEl = document.createElement("span");
  fileEl.className = "synth-file";
  fileEl.textContent = proposal.file;
  const actionEl = document.createElement("span");
  actionEl.className = `synth-action synth-action-${proposal.action}`;
  actionEl.textContent = proposal.action;
  head.appendChild(fileEl);
  head.appendChild(actionEl);
  card.appendChild(head);

  if (proposal.rationale) {
    const why = document.createElement("div");
    why.className = "synth-rationale";
    why.textContent = proposal.rationale;
    card.appendChild(why);
  }

  const contentEl = document.createElement("textarea");
  contentEl.className = "synth-content";
  contentEl.value = proposal.content;
  contentEl.rows = Math.min(12, Math.max(3, proposal.content.split("\n").length + 1));
  card.appendChild(contentEl);

  const actions = document.createElement("div");
  actions.className = "synth-actions";

  const applyBtn = document.createElement("button");
  applyBtn.className = "btn-primary";
  applyBtn.type = "button";
  applyBtn.textContent = "Aplicar";

  const rejectBtn = document.createElement("button");
  rejectBtn.className = "btn-ghost";
  rejectBtn.type = "button";
  rejectBtn.textContent = "Rejeitar";

  const status = document.createElement("span");
  status.className = "synth-status";

  applyBtn.addEventListener("click", async () => {
    applyBtn.disabled = true;
    rejectBtn.disabled = true;
    status.textContent = "salvando...";
    try {
      const r = await fetch("/api/vault/apply", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          file: proposal.file,
          action: proposal.action,
          content: contentEl.value,
        }),
      });
      if (!r.ok) {
        const txt = await r.text();
        throw new Error(txt);
      }
      card.classList.add("applied");
      status.textContent = "✓ aplicado";
      refreshVaultEverywhere();
    } catch (e) {
      applyBtn.disabled = false;
      rejectBtn.disabled = false;
      status.textContent = `falha: ${e.message}`;
    }
  });

  rejectBtn.addEventListener("click", () => {
    card.classList.add("rejected");
    applyBtn.disabled = true;
    rejectBtn.disabled = true;
    status.textContent = "× rejeitado";
  });

  actions.appendChild(applyBtn);
  actions.appendChild(rejectBtn);
  actions.appendChild(status);
  card.appendChild(actions);

  return card;
}

if (synthBtn) synthBtn.addEventListener("click", runSynthesis);
if (synthClose) synthClose.addEventListener("click", hideSynthModal);
if (synthModal) {
  synthModal.addEventListener("click", (e) => {
    if (e.target === synthModal) hideSynthModal();
  });
}

// ============================================================
//                       Ingestão de conhecimento
// ============================================================

let toastTimer = null;
function showToast(text, kind = "info") {
  if (!toastEl) return;
  toastEl.textContent = text;
  toastEl.classList.toggle("error", kind === "error");
  toastEl.hidden = false;
  // Force reflow pra animação rodar quando reutilizamos o elemento.
  void toastEl.offsetWidth;
  toastEl.classList.add("show");
  if (toastTimer) clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    toastEl.classList.remove("show");
    setTimeout(() => { toastEl.hidden = true; }, 250);
  }, 3500);
}

// Ingest tabs/file refs (resolvidos em runtime — modal pode ainda não existir).
const ingestCategory = document.getElementById("ingestCategory");
const ingestFile = document.getElementById("ingestFile");
const ingestFilePreview = document.getElementById("ingestFilePreview");
const ingestCancelBtn = document.getElementById("ingestCancelBtn");

let ingestActiveTab = "text";
let ingestFileText = "";
let ingestFileName = "";
let ingestFileBlob = null;     // mantém o File para upload binário (PDF)
let ingestFileIsBinary = false; // true → manda multipart pro server extrair

function showIngestModal() {
  if (!ingestModal) return;
  ingestModal.style.display = "flex";
  ingestHint.textContent = "";
  setTimeout(() => ingestContent && ingestContent.focus(), 30);
}

function hideIngestModal() {
  if (!ingestModal) return;
  ingestModal.style.display = "none";
}

function resetIngestForm() {
  if (ingestTitle) ingestTitle.value = "";
  if (ingestContent) ingestContent.value = "";
  if (ingestCategory) ingestCategory.value = "conhecimento";
  if (ingestFile) ingestFile.value = "";
  if (ingestFilePreview) {
    ingestFilePreview.textContent = "";
    ingestFilePreview.hidden = true;
  }
  ingestFileText = "";
  ingestFileName = "";
  ingestFileBlob = null;
  ingestFileIsBinary = false;
  if (ingestHint) ingestHint.textContent = "";
  switchIngestTab("text");
}

function switchIngestTab(tab) {
  ingestActiveTab = tab;
  document.querySelectorAll(".ingest-tab").forEach((btn) => {
    const active = btn.dataset.tab === tab;
    btn.classList.toggle("active", active);
    btn.setAttribute("aria-selected", active ? "true" : "false");
  });
  document.querySelectorAll(".ingest-pane").forEach((pane) => {
    pane.classList.toggle("active", pane.dataset.pane === tab);
  });
}

document.querySelectorAll(".ingest-tab").forEach((btn) => {
  btn.addEventListener("click", () => {
    if (btn.disabled) return;
    switchIngestTab(btn.dataset.tab);
  });
});

if (ingestFile) {
  ingestFile.addEventListener("change", async () => {
    const f = ingestFile.files && ingestFile.files[0];
    if (!f) {
      ingestFileText = "";
      ingestFileName = "";
      ingestFileBlob = null;
      ingestFileIsBinary = false;
      if (ingestFilePreview) {
        ingestFilePreview.textContent = "";
        ingestFilePreview.hidden = true;
      }
      return;
    }
    ingestFileName = f.name;
    ingestFileBlob = f;
    const isPdf = /\.pdf$/i.test(f.name) || f.type === "application/pdf";
    ingestFileIsBinary = isPdf;
    try {
      if (isPdf) {
        // PDF não é lido no browser — extração acontece no server.
        ingestFileText = "";
        if (ingestFilePreview) {
          const sizeMb = (f.size / (1024 * 1024)).toFixed(2);
          ingestFilePreview.textContent = `PDF • ${sizeMb} MB — texto será extraído no servidor.`;
          ingestFilePreview.hidden = false;
        }
      } else {
        ingestFileText = await f.text();
        if (ingestFilePreview) {
          const preview = ingestFileText.slice(0, 600);
          ingestFilePreview.textContent = preview + (ingestFileText.length > 600 ? "\n…" : "");
          ingestFilePreview.hidden = false;
        }
      }
    } catch (err) {
      ingestHint.textContent = `Falha lendo arquivo: ${err.message}`;
    }
  });
}

async function submitIngest(e) {
  e.preventDefault();
  if (!ingestForm) return;

  let suggestedTitle = (ingestTitle && ingestTitle.value || "").trim();
  const category = (ingestCategory && ingestCategory.value) || "conhecimento";
  const isBinaryFile = ingestActiveTab === "file" && ingestFileIsBinary && ingestFileBlob;

  // PDF (e qualquer binário futuro): multipart pro server extrair.
  if (isBinaryFile) {
    if (!suggestedTitle && ingestFileName) {
      suggestedTitle = ingestFileName.replace(/\.[^.]+$/, "");
    }
    const fd = new FormData();
    fd.append("file", ingestFileBlob, ingestFileName);
    fd.append("category", category);
    if (suggestedTitle) fd.append("title", suggestedTitle);

    ingestSubmit.disabled = true;
    ingestHint.textContent = "Extraindo texto e ingerindo...";
    try {
      const r = await fetch("/api/vault/ingest_file", { method: "POST", body: fd });
      if (!r.ok) {
        const detail = await r.text();
        throw new Error(detail || `HTTP ${r.status}`);
      }
      const data = await r.json();
      hideIngestModal();
      resetIngestForm();
      const where = data.rel_path || "vault";
      const cat = data.category ? ` [${data.category}]` : "";
      const warn = data.warning ? ` (aviso: ${data.warning})` : "";
      showToast(`Aprendido em ${where}${cat}${warn}`);
      refreshVaultEverywhere();
    } catch (err) {
      ingestHint.textContent = `Falha: ${err.message}`;
    } finally {
      ingestSubmit.disabled = false;
    }
    return;
  }

  // Caminho texto/markdown (mesmo de antes).
  let content;
  if (ingestActiveTab === "file") {
    content = ingestFileText.trim();
    if (!suggestedTitle && ingestFileName) {
      suggestedTitle = ingestFileName.replace(/\.[^.]+$/, "");
    }
  } else {
    content = (ingestContent.value || "").trim();
  }
  if (!content) {
    ingestHint.textContent = "Cole texto ou selecione um arquivo.";
    return;
  }

  ingestSubmit.disabled = true;
  ingestHint.textContent = "Ingerindo...";
  try {
    const r = await fetch("/api/vault/ingest", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        title: suggestedTitle || null,
        content,
        category,
        source: ingestActiveTab === "file" && ingestFileName ? `arquivo:${ingestFileName}` : null,
      }),
    });
    if (!r.ok) {
      const detail = await r.text();
      throw new Error(detail || `HTTP ${r.status}`);
    }
    const data = await r.json();
    hideIngestModal();
    resetIngestForm();
    const where = data.rel_path || "vault";
    const cat = data.category ? ` [${data.category}]` : "";
    showToast(`Aprendido em ${where}${cat}`);
    refreshVaultEverywhere();
  } catch (err) {
    ingestHint.textContent = `Falha: ${err.message}`;
  } finally {
    ingestSubmit.disabled = false;
  }
}

if (ingestBtn) ingestBtn.addEventListener("click", showIngestModal);
if (ingestClose) ingestClose.addEventListener("click", hideIngestModal);
if (ingestCancelBtn) ingestCancelBtn.addEventListener("click", hideIngestModal);
if (ingestModal) {
  ingestModal.addEventListener("click", (e) => {
    if (e.target === ingestModal) hideIngestModal();
  });
}
if (ingestForm) ingestForm.addEventListener("submit", submitIngest);

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
  loadSecrets();
}

// ============================================================
//                       Credenciais (.env)
// ============================================================

async function loadSecrets() {
  if (!secretsList) return;
  try {
    const r = await fetch("/api/secrets");
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const data = await r.json();
    renderSecrets(data.items || []);
  } catch (e) {
    secretsList.innerHTML = `<div class="secrets-loading">Falha lendo secrets: ${escapeHtml(e.message)}</div>`;
  }
}

function renderSecrets(items) {
  secretsList.innerHTML = "";
  for (const item of items) {
    secretsList.appendChild(buildSecretRow(item));
  }
}

function buildSecretRow(item) {
  const row = document.createElement("div");
  const requiredMissing = item.required && !item.defined;
  row.className = `secret-row ${item.defined ? "defined" : "missing"}${item.required ? " required" : ""}`;

  // ----- info -----
  const info = document.createElement("div");
  info.className = "secret-info";

  const head = document.createElement("div");
  head.className = "secret-head";
  const keyEl = document.createElement("span");
  keyEl.className = "secret-key";
  keyEl.textContent = item.key;
  head.appendChild(keyEl);

  const status = document.createElement("span");
  if (requiredMissing) {
    status.className = "secret-status required-missing";
    status.textContent = "obrigatória — vazia";
  } else if (item.defined) {
    status.className = "secret-status defined";
    status.textContent = "✓ definida";
  } else {
    status.className = "secret-status missing";
    status.textContent = "ausente";
  }
  head.appendChild(status);
  info.appendChild(head);

  const label = document.createElement("div");
  label.className = "secret-label";
  label.textContent = item.label || "";
  info.appendChild(label);

  if (item.description) {
    const desc = document.createElement("div");
    desc.className = "secret-desc";
    desc.textContent = item.description;
    info.appendChild(desc);
  }

  if (item.docs_url) {
    const a = document.createElement("a");
    a.href = item.docs_url;
    a.target = "_blank";
    a.rel = "noopener";
    a.className = "secret-docs";
    a.textContent = "↗ pegar aqui";
    info.appendChild(a);
  }

  row.appendChild(info);

  // ----- edit row -----
  const edit = document.createElement("div");
  edit.className = "secret-edit";

  const input = document.createElement("input");
  input.type = item.sensitive ? "password" : "text";
  input.autocomplete = "off";
  input.spellcheck = false;

  // Estado: "view" (bullets cosméticos, readonly) | "edit" (valor real, editável).
  // - missing → começa em "edit" com placeholder
  // - defined → começa em "view"; clicar Trocar busca valor e vai pra "edit"
  let mode = item.defined ? "view" : "edit";
  if (mode === "view") {
    input.value = item.sensitive ? "••••••••••••" : (item.preview || "");
    input.readOnly = true;
  } else {
    input.value = "";
    input.placeholder = "cole o valor aqui";
  }
  edit.appendChild(input);

  const actions = document.createElement("div");
  actions.className = "secret-actions";
  edit.appendChild(actions);

  row.appendChild(edit);

  const hint = document.createElement("div");
  hint.className = "secret-hint";
  row.appendChild(hint);

  // Botões mudam conforme o modo. renderActions reaplica.
  function renderActions() {
    actions.innerHTML = "";
    if (mode === "view") {
      const trocarBtn = document.createElement("button");
      trocarBtn.type = "button";
      trocarBtn.className = "btn-cockpit";
      trocarBtn.textContent = "Trocar";
      trocarBtn.addEventListener("click", () => enterEditMode());
      actions.appendChild(trocarBtn);

      const limparBtn = document.createElement("button");
      limparBtn.type = "button";
      limparBtn.className = "btn-cockpit";
      limparBtn.textContent = "Limpar";
      limparBtn.title = "Remove a chave do .env";
      limparBtn.addEventListener("click", () => saveSecret(item.key, "", row));
      actions.appendChild(limparBtn);
    } else {
      const salvarBtn = document.createElement("button");
      salvarBtn.type = "button";
      salvarBtn.className = "btn-cockpit cyan-fill";
      salvarBtn.textContent = "Salvar";
      salvarBtn.addEventListener("click", () => trySave());
      actions.appendChild(salvarBtn);

      if (item.defined) {
        const cancelarBtn = document.createElement("button");
        cancelarBtn.type = "button";
        cancelarBtn.className = "btn-cockpit";
        cancelarBtn.textContent = "Cancelar";
        cancelarBtn.addEventListener("click", () => exitEditMode());
        actions.appendChild(cancelarBtn);
      }
    }
  }

  async function enterEditMode() {
    hint.className = "secret-hint";
    hint.textContent = "Carregando…";
    try {
      const r = await fetch(`/api/secrets/${encodeURIComponent(item.key)}/reveal`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const data = await r.json();
      input.value = data.value || "";
    } catch (e) {
      hint.className = "secret-hint error";
      hint.textContent = `Falha revelando: ${e.message}`;
      return;
    }
    mode = "edit";
    input.readOnly = false;
    input.placeholder = "";
    hint.textContent = "";
    renderActions();
    input.focus();
    input.select();
  }

  function exitEditMode() {
    mode = "view";
    input.readOnly = true;
    input.value = item.sensitive ? "••••••••••••" : (item.preview || "");
    hint.className = "secret-hint";
    hint.textContent = "";
    renderActions();
  }

  function trySave() {
    const v = input.value.trim();
    if (!v) {
      hint.className = "secret-hint error";
      hint.textContent = "Cole o valor antes de salvar.";
      return;
    }
    saveSecret(item.key, v, row);
  }

  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && mode === "edit") {
      e.preventDefault();
      trySave();
    } else if (e.key === "Escape" && mode === "edit" && item.defined) {
      e.preventDefault();
      exitEditMode();
    }
  });

  renderActions();
  return row;
}

async function saveSecret(key, value, row) {
  const hint = row.querySelector(".secret-hint");
  hint.className = "secret-hint";
  hint.textContent = "Salvando...";
  try {
    const r = await fetch("/api/secrets", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ updates: { [key]: value || null } }),
    });
    if (!r.ok) {
      const detail = await r.text();
      throw new Error(detail || `HTTP ${r.status}`);
    }
    const data = await r.json();
    renderSecrets(data.items || []);
    showToast(value ? `${key} salva — reinicie pra aplicar` : `${key} removida — reinicie pra aplicar`);
  } catch (e) {
    hint.className = "secret-hint error";
    hint.textContent = `Falha: ${e.message}`;
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

// Cache da última lista de servers — evita race entre toggle e re-fetch.
let mcpServersCache = [];

// Container do painel pra aplicar overlay de "carregando" em ações que
// afetam todos os servers (reload, import com reconexão).
function getMcpPanel() {
  return mcpList?.closest(".panel") || null;
}

function setMcpPanelBusy(busy, label) {
  const panel = getMcpPanel();
  if (!panel) return;
  panel.classList.toggle("is-busy", !!busy);
  if (busy) {
    panel.dataset.busyLabel = label || "Aguardando…";
  } else {
    delete panel.dataset.busyLabel;
  }
}

function setRowBusy(rowEl, busy) {
  if (!rowEl) return;
  rowEl.classList.toggle("is-busy", !!busy);
  rowEl.querySelectorAll("button").forEach((b) => { b.disabled = !!busy; });
}

function findMcpRow(name) {
  // Busca pela linha que contém um botão com data-name correspondente.
  const sel = `button[data-name="${CSS.escape(name)}"]`;
  const btn = (mcpList.querySelector(sel) || mcpGlobalList.querySelector(sel));
  return btn ? btn.closest(".mcp-row") : null;
}

async function loadMcps() {
  setMcpPanelBusy(true, "Carregando…");
  try {
    const r = await fetch("/api/mcps");
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const data = await r.json();
    mcpServersCache = data.servers || [];
    renderMcps(mcpServersCache);
  } catch (e) {
    mcpList.innerHTML = `<div class="mcp-empty">Falha lendo MCPs: ${e.message}</div>`;
    if (mcpGlobalList) mcpGlobalList.innerHTML = "";
  } finally {
    setMcpPanelBusy(false);
  }
}

function renderMcps(servers) {
  const locals = servers.filter((s) => (s.source || "local") === "local");
  const globals = servers.filter((s) => (s.source || "local") !== "local");

  mcpLocalMeta.textContent = locals.length
    ? `${locals.filter((s) => s.connected).length}/${locals.length} conectados`
    : "vazio";
  mcpGlobalMeta.textContent = globals.length
    ? `${globals.length} detectado(s)`
    : "nenhum";

  mcpList.innerHTML = "";
  if (!locals.length) {
    mcpList.innerHTML = `<div class="mcp-empty">Nenhum servidor local. Adicione abaixo, ou importe um detectado.</div>`;
  } else {
    for (const s of locals) mcpList.appendChild(buildMcpRow(s));
  }

  mcpGlobalList.innerHTML = "";
  if (!globals.length) {
    mcpGlobalList.innerHTML = `<div class="mcp-empty mcp-empty-soft">Nenhum MCP global encontrado em ~/.claude.json ou Claude Desktop.</div>`;
  } else {
    for (const s of globals) mcpGlobalList.appendChild(buildMcpRow(s));
  }

  mcpList.querySelectorAll("button[data-action]").forEach((btn) => {
    btn.addEventListener("click", () => mcpAction(btn.dataset.action, btn.dataset.name, btn.dataset.source || "local"));
  });
  mcpGlobalList.querySelectorAll("button[data-action]").forEach((btn) => {
    btn.addEventListener("click", () => mcpAction(btn.dataset.action, btn.dataset.name, btn.dataset.source || "local"));
  });
}

function buildMcpRow(s) {
  const row = document.createElement("div");
  const isGlobal = (s.source || "local") !== "local";
  let cls = "mcp-row";
  if (isGlobal) cls += " global";
  else if (!s.enabled) cls += " disabled";
  else if (s.connected) cls += " connected";
  else cls += " error";
  row.className = cls;

  const transport = s.transport || "stdio";
  const target = transport === "stdio"
    ? `${s.command || ""} ${(s.args || []).join(" ")}`.trim()
    : (s.url || "");
  const sourceLabel = sourceBadge(s.source || "local");
  const transportBadge = `<span class="mcp-badge transport-${escapeHtml(transport)}">${escapeHtml(transport)}</span>`;

  let toolsText;
  if (isGlobal) {
    toolsText = "não conectado (use Importar pra ativar no Jarvis)";
  } else if (s.error) {
    toolsText = `erro: ${s.error}`;
  } else if (!s.enabled) {
    toolsText = "desligado";
  } else if (s.tools && s.tools.length) {
    toolsText = `${s.tools.length} tool(s): ${s.tools.join(", ")}`;
  } else {
    toolsText = "sem tools";
  }

  const actions = isGlobal
    ? `<button class="icon-btn" data-action="import" data-name="${escapeHtml(s.name)}" data-source="${escapeHtml(s.source)}">Importar</button>`
    : `
        <button class="icon-btn" data-action="toggle" data-name="${escapeHtml(s.name)}">${s.enabled ? "Desligar" : "Ligar"}</button>
        <button class="icon-btn danger" data-action="remove" data-name="${escapeHtml(s.name)}">Remover</button>
      `;

  row.innerHTML = `
    <div class="mcp-info">
      <div class="mcp-name">
        ${escapeHtml(s.name)}
        ${transportBadge}
        ${sourceLabel}
      </div>
      <div class="mcp-cmd">${escapeHtml(target || "(sem alvo)")}</div>
      <div class="mcp-tools ${s.error ? "error" : ""}">${escapeHtml(toolsText)}</div>
    </div>
    <div class="mcp-actions">${actions}</div>
  `;
  return row;
}

function sourceBadge(source) {
  if (source === "local") return "";
  let label, cls;
  if (source === "claude_desktop") {
    label = "Claude Desktop";
    cls = "src-desktop";
  } else if (source === "claude_code") {
    label = "Claude Code (global)";
    cls = "src-code";
  } else if (source.startsWith("claude_code:")) {
    const proj = source.slice("claude_code:".length).split(/[\\/]/).pop() || "?";
    label = `Claude Code · ${proj}`;
    cls = "src-code";
  } else {
    label = source;
    cls = "src-other";
  }
  return `<span class="mcp-badge ${cls}" title="${escapeHtml(source)}">${escapeHtml(label)}</span>`;
}

async function mcpAction(action, name, source) {
  const row = findMcpRow(name);
  setRowBusy(row, true);

  // Labels diferentes por ação — o overlay do painel descreve o que tá rolando.
  const panelLabels = {
    import: `Importando ${name} e reconectando…`,
    remove: `Removendo ${name} e reconectando…`,
    toggle: `Atualizando ${name} e reconectando…`,
  };
  setMcpPanelBusy(true, panelLabels[action] || "Aguardando…");

  try {
    if (action === "import") {
      const r = await fetch("/api/mcps/import", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name, source }),
      });
      if (!r.ok) {
        const err = await r.text();
        showToast(`Falha importando ${name}: ${err}`, "error");
      } else {
        showToast(`${name} importado.`);
      }
    } else {
      // Toggle/remove só agem em locais.
      let servers = mcpServersCache
        .filter((s) => (s.source || "local") === "local")
        .map((s) => ({
          name: s.name,
          transport: s.transport || "stdio",
          command: s.command || "",
          args: s.args || [],
          env: {},
          url: s.url || "",
          headers: {},
          auth: s.auth || "auto",
          enabled: s.enabled,
        }));
      if (action === "remove") {
        servers = servers.filter((s) => s.name !== name);
      } else if (action === "toggle") {
        servers = servers.map((s) => s.name === name ? { ...s, enabled: !s.enabled } : s);
      }
      const r = await fetch("/api/mcps", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ servers }),
      });
      if (!r.ok) {
        const err = await r.text();
        showToast(`Falha em ${action} ${name}: ${err}`, "error");
      } else if (action === "remove") {
        showToast(`${name} removido.`);
      } else if (action === "toggle") {
        const wasEnabled = mcpServersCache.find((s) => s.name === name)?.enabled;
        showToast(`${name} ${wasEnabled ? "desligado" : "ligado"}.`);
      }
    }
    await loadMcps();
  } catch (e) {
    showToast(`Erro: ${e.message}`, "error");
  } finally {
    setMcpPanelBusy(false);
    // O loadMcps re-renderiza tudo, então a row antiga é substituída — mas
    // se algo falhou antes do re-render, garanto liberar.
    setRowBusy(row, false);
  }
}

function mcpToggleTransportFields() {
  const t = mcpTransportSel.value;
  const isStdio = t === "stdio";
  document.querySelectorAll(".mcp-stdio-only").forEach((el) => { el.hidden = !isStdio; });
  document.querySelectorAll(".mcp-http-only").forEach((el) => { el.hidden = isStdio; });
}
mcpTransportSel.addEventListener("change", mcpToggleTransportFields);
mcpToggleTransportFields();

mcpAddBtn.addEventListener("click", async () => {
  const name = document.getElementById("mcpName").value.trim();
  const transport = mcpTransportSel.value;
  const command = document.getElementById("mcpCommand").value.trim();
  const argsRaw = document.getElementById("mcpArgs").value.trim();
  const envRaw = document.getElementById("mcpEnv").value.trim();
  const url = document.getElementById("mcpUrl").value.trim();
  const headersRaw = document.getElementById("mcpHeaders").value.trim();
  const auth = (document.getElementById("mcpAuth")?.value || "auto");

  if (!name) { alert("Nome é obrigatório."); return; }
  if (transport === "stdio" && !command) { alert("Para stdio, comando é obrigatório."); return; }
  if (transport !== "stdio" && !url) { alert("Para http/sse, URL é obrigatória."); return; }

  const args = argsRaw ? argsRaw.split("\n").map((s) => s.trim()).filter(Boolean) : [];
  const env = parseKv(envRaw);
  const headers = parseKv(headersRaw);

  const existing = mcpServersCache
    .filter((s) => (s.source || "local") === "local")
    .map((s) => ({
      name: s.name,
      transport: s.transport || "stdio",
      command: s.command || "",
      args: s.args || [],
      env: {},
      url: s.url || "",
      headers: {},
      auth: s.auth || "auto",
      enabled: s.enabled,
    }));
  const merged = [
    ...existing.filter((s) => s.name !== name),
    { name, transport, command, args, env, url, headers, auth, enabled: true },
  ];

  const isUpdate = mcpServersCache.some((s) => s.name === name && (s.source || "local") === "local");
  setMcpPanelBusy(true, `${isUpdate ? "Atualizando" : "Adicionando"} ${name} e reconectando…`);
  mcpAddBtn.disabled = true;
  const originalLabel = mcpAddBtn.textContent;
  mcpAddBtn.textContent = isUpdate ? "Atualizando…" : "Adicionando…";

  try {
    const r = await fetch("/api/mcps", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ servers: merged }),
    });
    if (!r.ok) {
      const err = await r.text();
      showToast(`Falha salvando ${name}: ${err}`, "error");
      return;
    }

    document.getElementById("mcpName").value = "";
    document.getElementById("mcpCommand").value = "";
    document.getElementById("mcpArgs").value = "";
    document.getElementById("mcpEnv").value = "";
    document.getElementById("mcpUrl").value = "";
    document.getElementById("mcpHeaders").value = "";
    showToast(`${name} ${isUpdate ? "atualizado" : "adicionado"}.`);
    await loadMcps();
  } catch (e) {
    showToast(`Erro: ${e.message}`, "error");
  } finally {
    setMcpPanelBusy(false);
    mcpAddBtn.disabled = false;
    mcpAddBtn.textContent = originalLabel;
  }
});

function parseKv(raw) {
  const out = {};
  if (!raw) return out;
  for (const line of raw.split("\n")) {
    const idx = line.indexOf("=");
    if (idx > 0) out[line.slice(0, idx).trim()] = line.slice(idx + 1).trim();
  }
  return out;
}

mcpReloadBtn.addEventListener("click", async () => {
  setMcpPanelBusy(true, "Reconectando todos os servidores…");
  mcpReloadBtn.disabled = true;
  const originalLabel = mcpReloadBtn.textContent;
  mcpReloadBtn.textContent = "Reconectando…";
  try {
    const r = await fetch("/api/mcps/reload", { method: "POST" });
    if (!r.ok) {
      const err = await r.text();
      showToast(`Falha recarregando: ${err}`, "error");
    } else {
      showToast("Servidores reconectados.");
    }
    await loadMcps();
  } catch (e) {
    showToast(`Erro: ${e.message}`, "error");
  } finally {
    setMcpPanelBusy(false);
    mcpReloadBtn.disabled = false;
    mcpReloadBtn.textContent = originalLabel;
  }
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
//                       Home cockpit
// ============================================================

const WEEKDAY_PT = ["Domingo", "Segunda-feira", "Terça-feira", "Quarta-feira", "Quinta-feira", "Sexta-feira", "Sábado"];

function tickClock() {
  if (!sysDate || !sysTime) return;
  const now = new Date();
  const dd = String(now.getDate()).padStart(2, "0");
  const mm = String(now.getMonth() + 1).padStart(2, "0");
  const yy = String(now.getFullYear()).slice(2);
  const hh = String(now.getHours()).padStart(2, "0");
  const mi = String(now.getMinutes()).padStart(2, "0");
  const ss = String(now.getSeconds()).padStart(2, "0");
  sysDate.textContent = `${WEEKDAY_PT[now.getDay()].slice(0, 3).toUpperCase()} ${dd}/${mm}/${yy}`;
  sysTime.textContent = `${hh}:${mi}:${ss}`;
}

function setDot(refs, on, label) {
  if (!refs || !refs.dot || !refs.val) return;
  refs.dot.classList.remove("on", "off", "warn");
  refs.dot.classList.add(on ? "on" : "off");
  refs.val.classList.remove("ok", "off");
  refs.val.classList.add(on ? "ok" : "off");
  refs.val.textContent = label;
}

async function refreshHomeSystems() {
  try {
    const r = await fetch("/api/status");
    if (!r.ok) return;
    const data = await r.json();
    const caps = data.capabilities || {};
    setDot(homeStatusDots.agent, !!caps.agent, caps.agent ? "online" : "offline");
    setDot(homeStatusDots.stt, !!caps.stt, caps.stt ? "online" : "offline");
    setDot(homeStatusDots.vault, !!caps.vault, caps.vault ? "ok" : "off");
    setDot(homeStatusDots.spotify, !!caps.spotify, caps.spotify ? "online" : "offline");
    setDot(homeStatusDots.wake_word, !!caps.wake_word, caps.wake_word ? "ativa" : "off");
  } catch (_) { /* silencioso */ }
}

async function refreshHomeVault() {
  try {
    const r = await fetch("/api/vault/stats");
    if (!r.ok) return;
    const data = await r.json();
    const s = data.stats || {};
    if (ovNotes) ovNotes.textContent = s.notas_total ?? 0;
    if (ovLinks) ovLinks.textContent = s.wikilinks_total ?? 0;
    if (ovToday) ovToday.textContent = s.interacoes_hoje ?? 0;
    if (ovWeek) ovWeek.textContent = s.interacoes_7dias ?? 0;
    vaultRootPath = data.root || "";
    if (ovPath) ovPath.textContent = vaultRootPath;
    if (brainMeta) brainMeta.textContent = `${s.notas_total ?? 0} notas · ${s.wikilinks_total ?? 0} links`;
  } catch (_) { /* silencioso */ }
}

// Cores por pasta no grafo
const GRAPH_COLORS = {
  perfil: "#00d4ff",
  projetos: "#f5c453",
  decisoes: "#c084fc",
  conhecimento: "#6abf6a",
};

async function refreshBrainGraph() {
  if (!brainGraph) return;
  try {
    const r = await fetch("/api/vault/graph");
    if (!r.ok) return;
    const data = await r.json();
    renderBrainGraph(data.nodes || [], data.edges || []);
  } catch (_) { /* silencioso */ }
}

function renderBrainGraph(nodes, edges) {
  if (!brainGraph) return;
  // Limpa SVG
  while (brainGraph.firstChild) brainGraph.removeChild(brainGraph.firstChild);
  if (!nodes.length) {
    if (brainEmpty) brainEmpty.hidden = false;
    return;
  }
  if (brainEmpty) brainEmpty.hidden = true;

  const W = 800, H = 320;
  // Estado da simulação
  const state = nodes.map((n, i) => ({
    id: n.id,
    label: n.label,
    dir: n.dir,
    size: Math.max(3, Math.min(9, Math.sqrt(n.size || 1) * 2 + 3)),
    x: W / 2 + (Math.random() - 0.5) * 200,
    y: H / 2 + (Math.random() - 0.5) * 100,
    vx: 0,
    vy: 0,
  }));
  const idx = new Map(state.map((n, i) => [n.id, i]));
  const links = edges
    .map((e) => ({ s: idx.get(e.source), t: idx.get(e.target) }))
    .filter((l) => l.s !== undefined && l.t !== undefined);

  // Simulação force-directed simples (Fruchterman-Reingold-ish).
  // Repulsão O(n²) é aceitável até umas centenas de nós; pra vault pessoal vai sobrar.
  const ITER = 220;
  const REPEL = 1400;        // força repulsiva
  const SPRING = 0.012;      // constante elástica das arestas
  const REST = 70;           // distância de repouso da aresta
  const DAMP = 0.78;         // damping
  const CENTER_PULL = 0.005; // atração ao centro
  const cx = W / 2, cy = H / 2;

  for (let it = 0; it < ITER; it++) {
    // Repulsão entre todos os pares
    for (let i = 0; i < state.length; i++) {
      for (let j = i + 1; j < state.length; j++) {
        const a = state[i], b = state[j];
        let dx = a.x - b.x, dy = a.y - b.y;
        let dist2 = dx * dx + dy * dy;
        if (dist2 < 1) { dx = Math.random() - 0.5; dy = Math.random() - 0.5; dist2 = 1; }
        const dist = Math.sqrt(dist2);
        const f = REPEL / dist2;
        const fx = (dx / dist) * f;
        const fy = (dy / dist) * f;
        a.vx += fx; a.vy += fy;
        b.vx -= fx; b.vy -= fy;
      }
    }
    // Atração ao longo das arestas
    for (const l of links) {
      const a = state[l.s], b = state[l.t];
      const dx = b.x - a.x, dy = b.y - a.y;
      const dist = Math.sqrt(dx * dx + dy * dy) || 1;
      const f = SPRING * (dist - REST);
      const fx = (dx / dist) * f;
      const fy = (dy / dist) * f;
      a.vx += fx; a.vy += fy;
      b.vx -= fx; b.vy -= fy;
    }
    // Atração ao centro (evita nós dispararem pro infinito)
    for (const n of state) {
      n.vx += (cx - n.x) * CENTER_PULL;
      n.vy += (cy - n.y) * CENTER_PULL;
    }
    // Aplica deslocamento + damping + bounding box
    for (const n of state) {
      n.x += n.vx;
      n.y += n.vy;
      n.vx *= DAMP;
      n.vy *= DAMP;
      const margin = 20;
      if (n.x < margin) { n.x = margin; n.vx = 0; }
      if (n.x > W - margin) { n.x = W - margin; n.vx = 0; }
      if (n.y < margin) { n.y = margin; n.vy = 0; }
      if (n.y > H - margin) { n.y = H - margin; n.vy = 0; }
    }
  }

  // Renderiza
  const svgNS = "http://www.w3.org/2000/svg";
  // arestas primeiro (atrás)
  for (const l of links) {
    const a = state[l.s], b = state[l.t];
    const line = document.createElementNS(svgNS, "line");
    line.setAttribute("x1", a.x);
    line.setAttribute("y1", a.y);
    line.setAttribute("x2", b.x);
    line.setAttribute("y2", b.y);
    line.setAttribute("class", "brain-graph-edge");
    brainGraph.appendChild(line);
  }
  // nós
  for (const n of state) {
    const color = GRAPH_COLORS[n.dir] || "#7a8aa0";
    const c = document.createElementNS(svgNS, "circle");
    c.setAttribute("cx", n.x);
    c.setAttribute("cy", n.y);
    c.setAttribute("r", n.size);
    c.setAttribute("fill", color);
    c.setAttribute("class", "brain-graph-node");
    const title = document.createElementNS(svgNS, "title");
    title.textContent = `${n.dir}/${n.label}`;
    c.appendChild(title);
    brainGraph.appendChild(c);
  }
  // labels só pros maiores (evita poluição)
  const sorted = [...state].sort((a, b) => b.size - a.size);
  const labelLimit = Math.min(10, sorted.length);
  for (let i = 0; i < labelLimit; i++) {
    const n = sorted[i];
    if (n.size < 5) break;
    const t = document.createElementNS(svgNS, "text");
    t.setAttribute("x", n.x);
    t.setAttribute("y", n.y - n.size - 4);
    t.setAttribute("class", "brain-graph-label");
    t.textContent = n.label.length > 18 ? n.label.slice(0, 17) + "…" : n.label;
    brainGraph.appendChild(t);
  }
}

function openObsidianVault() {
  if (!vaultRootPath) return;
  // Encode path: Obsidian aceita o caminho absoluto via parâmetro `path`.
  // Se o vault não estiver registrado no Obsidian, ele vai oferecer abrir.
  const url = `obsidian://open?path=${encodeURIComponent(vaultRootPath)}`;
  window.open(url, "_blank");
}

function openVaultExplorer() {
  if (!vaultRootPath) return;
  // file:// URI funciona em Edge/Chrome no Windows pra abrir no Explorer.
  const url = `file:///${vaultRootPath.replace(/\\/g, "/")}`;
  window.open(url, "_blank");
}

if (openObsidianBtn) openObsidianBtn.addEventListener("click", openObsidianVault);
if (openExplorerBtn) openExplorerBtn.addEventListener("click", openVaultExplorer);

function bootHomeCockpit() {
  tickClock();
  setInterval(tickClock, 1000);
  refreshHomeSystems();
  setInterval(refreshHomeSystems, 30000);
  refreshHomeVault();
  refreshBrainGraph();
}

// ============================================================
//                          Boot
// ============================================================

connectWS();
loadCapabilities();
bootHomeCockpit();
inputEl.focus();
