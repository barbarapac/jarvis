"use strict";

const transcript = document.getElementById("transcript");
const statusEl = document.getElementById("status");
const reactorEl = document.getElementById("reactor");
const inputEl = document.getElementById("input");
const sendEl = document.getElementById("send");

const MAX_ENTRIES = 200;

const STATUS_LABELS = {
  idle: "online",
  listening: "ouvindo...",
  speaking: "falando...",
  working: "executando...",
};

function setStatus(state) {
  statusEl.textContent = STATUS_LABELS[state] || state;
  reactorEl.classList.remove("listening", "speaking", "working");
  if (state !== "idle") reactorEl.classList.add(state);
}

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

function handleEvent(evt) {
  const { type, data } = evt;

  switch (type) {
    case "status":
      setStatus(data.state);
      break;

    case "speaking_started":
      setStatus("speaking");
      addEntry({ kind: "jarvis", badge: "JARVIS", text: data.text || "" });
      break;

    case "speaking_ended":
      setStatus("idle");
      break;

    case "listening_started":
      setStatus("listening");
      break;

    case "listening_ended":
      setStatus("idle");
      break;

    case "user_voice_transcribed":
      if (data.text) {
        addEntry({ kind: "user", badge: "VOCÊ", text: data.text });
      }
      break;

    case "user_text_input":
      addEntry({ kind: "user", badge: "VOCÊ", text: data.text });
      break;

    case "gitlab_announce":
      addEntry({
        kind: "gitlab",
        badge: "GITLAB",
        text: data.text,
        url: data.url,
      });
      break;

    case "command_dispatched":
      // já capturado via speaking events; aqui é só log
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

function connectWS() {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  const ws = new WebSocket(`${proto}//${location.host}/ws`);

  ws.onopen = () => setStatus("idle");
  ws.onmessage = (msg) => {
    try {
      handleEvent(JSON.parse(msg.data));
    } catch (e) {
      console.error("evento inválido", e, msg.data);
    }
  };
  ws.onclose = () => {
    setStatus("idle");
    statusEl.textContent = "desconectado — reconectando...";
    setTimeout(connectWS, 1500);
  };
  ws.onerror = (e) => console.error("ws error", e);
}

sendEl.addEventListener("click", () => {
  const text = inputEl.value;
  inputEl.value = "";
  sendCommand(text);
});

inputEl.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    const text = inputEl.value;
    inputEl.value = "";
    sendCommand(text);
  }
});

connectWS();
inputEl.focus();
