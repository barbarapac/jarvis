"""Orquestrador de comandos por voz.

Entry points:
- `on_hotkey()` — push-to-talk no teclado
- `on_wake_word()` — wake word detectado, mesmo fluxo do PTT
- `handle_audio(pcm_bytes)` — áudio já gravado (vindo do botão mic da UI)
- `handle_text(text)` — texto digitado na UI

Dispatch:
1. Built-ins (pause/resume/next/previous/current + pattern "<verbo> <playlist>").
2. Custom commands definidos no YAML — match por substring no trigger.
3. Se nada casar e houver agente Claude, chama agente com tools dos MCPs.
4. Senão, fala "não reconhecido".

Custom commands têm prioridade SOBRE built-ins quando o trigger é mais
específico — checamos custom primeiro, se nada casa caímos nos built-ins.
"""

from __future__ import annotations

import re
import sys
import threading
from typing import Optional

from core.audio_recorder import record_with_vad
from core.command_registry import Tool
from core.event_bus import EventBus, EventType
from core.narrator import SpeakingNarrator
from core.persona import Persona
from core.stt import VoskSTT
from tools.spotify import SpotifyError, SpotifyTool

COMMAND_MAX_DURATION_SECONDS = 5.0

BEEP_FREQ_HZ = 880
BEEP_DURATION_MS = 80

# Built-ins do Spotify — reconhecimento padrão por palavras-chave em PT-BR.
_BUILTIN_KEYWORDS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("spotify", "pause", ("pausa", "pausar", "pause", "para", "pare", "parar")),
    ("spotify", "resume", ("retoma", "retomar", "continua", "continuar", "play", "voltar a tocar")),
    ("spotify", "next", ("próxima", "proxima", "next", "skip", "pula", "pular")),
    ("spotify", "previous", ("anterior", "voltar", "volta", "previous")),
    ("spotify", "current", ("qual música", "qual musica", "que música", "que musica", "que canção", "que cancao")),
)
_PLAY_VERBS = ("toca", "tocar", "toque", "coloca", "colocar", "põe", "poe", "por", "bota", "botar", "manda")
_PLAY_PATTERN = re.compile(rf"\b(?:{'|'.join(re.escape(v) for v in _PLAY_VERBS)})\s+(.+?)$")

# Pontuação removida antes do match (STT não devolve pontuação).
_NORMALIZE_RE = re.compile(r"[,.!?;:¿¡]+")


def _normalize_text(text: str) -> str:
    """Lowercase, remove pontuação, colapsa espaços."""
    s = _NORMALIZE_RE.sub(" ", text.lower())
    return " ".join(s.split())


class VoiceCommander:
    def __init__(
        self,
        stt: VoskSTT,
        narrator: SpeakingNarrator,
        persona: Persona,
        spotify: Optional[SpotifyTool] = None,
        event_bus: Optional[EventBus] = None,
        agent: Optional[object] = None,  # JarvisAgent — duck-typed pra evitar import circular
        commands: Optional[list[dict]] = None,
        tool_registry: Optional[dict[str, Tool]] = None,
    ) -> None:
        self._stt = stt
        self._narrator = narrator
        self._persona = persona
        self._spotify = spotify
        self._event_bus = event_bus
        self._agent = agent
        self._busy_lock = threading.Lock()

        self._tools: dict[str, Tool] = tool_registry or {}
        # Lista normalizada de custom commands. Ordenada por tamanho do trigger
        # decrescente: gatilhos mais específicos ganham de mais curtos quando
        # ambos casariam o mesmo texto (ex.: "iniciar modo foco" > "modo foco").
        self._commands: list[dict] = []
        for raw in commands or []:
            entry = _normalize_command(raw)
            if entry:
                self._commands.append(entry)
        self._commands.sort(key=lambda c: -len(c["trigger_normalized"]))

    # ---------- Entry points ----------

    def handle_text(self, text: str) -> None:
        if self._event_bus:
            self._event_bus.publish(EventType.USER_TEXT_INPUT, text=text)
        if not text.strip():
            return
        if not self._busy_lock.acquire(blocking=False):
            return
        try:
            self._dispatch_or_fallback(text)
        finally:
            self._busy_lock.release()

    def on_hotkey(self) -> None:
        if not self._busy_lock.acquire(blocking=False):
            print("[voice] já processando — ignorando hotkey duplo.")
            return
        try:
            self._capture_and_dispatch()
        finally:
            self._busy_lock.release()

    def on_wake_word(self) -> None:
        """Acionado quando o wake word listener pega 'jarvis'."""
        if not self._busy_lock.acquire(blocking=False):
            return
        try:
            self._narrator.speak(f"Sim, {self._persona.honorific}?")
            self._capture_and_dispatch()
        finally:
            self._busy_lock.release()

    def handle_audio(self, pcm_bytes: bytes) -> None:
        """Áudio PCM mono 16kHz int16 (vindo do browser, por exemplo)."""
        if not self._busy_lock.acquire(blocking=False):
            return
        try:
            text = self._stt.transcribe(pcm_bytes)
            print(f"[voice] (ui-mic) transcrição: {text!r}")
            if self._event_bus:
                self._event_bus.publish(EventType.USER_VOICE_TRANSCRIBED, text=text)
            if not text:
                self._narrator.speak(f"Não captei, {self._persona.honorific}.")
                return
            self._dispatch_or_fallback(text)
        finally:
            self._busy_lock.release()

    # ---------- Internals ----------

    def _capture_and_dispatch(self) -> None:
        self._beep()
        print("[voice] ouvindo...")
        if self._event_bus:
            self._event_bus.publish(EventType.LISTENING_STARTED)
        try:
            audio = record_with_vad(max_duration_seconds=COMMAND_MAX_DURATION_SECONDS)
        finally:
            if self._event_bus:
                self._event_bus.publish(EventType.LISTENING_ENDED)
        text = self._stt.transcribe(audio)
        print(f"[voice] transcrição: {text!r}")
        if self._event_bus:
            self._event_bus.publish(EventType.USER_VOICE_TRANSCRIBED, text=text)

        if not text:
            self._narrator.speak(f"Não captei, {self._persona.honorific}.")
            return

        self._dispatch_or_fallback(text)

    def _dispatch_or_fallback(self, text: str) -> None:
        if self._dispatch_local(text.lower()):
            return
        if self._agent is not None:
            try:
                if self._event_bus:
                    self._event_bus.publish(EventType.STATUS, state="working")
                reply = self._agent.respond(text)
                if reply:
                    self._narrator.speak(reply)
                else:
                    self._narrator.speak(f"Sem resposta, {self._persona.honorific}.")
                return
            except Exception as e:
                print(f"[voice] agente falhou: {e!r}")
                self._narrator.speak(
                    f"{self._persona.honorific}, falha consultando o cérebro: {e}."
                )
                return
        self._narrator.speak(f"Comando não reconhecido, {self._persona.honorific}.")

    def _dispatch_local(self, text: str) -> bool:
        normalized = _normalize_text(text)

        # 1) Custom commands — substring match no trigger normalizado.
        #    Lista já vem ordenada por specificidade (trigger mais longo primeiro).
        for cmd in self._commands:
            if cmd["trigger_normalized"] in normalized:
                if self._run_action(cmd["tool"], cmd["action"], cmd["params"]):
                    return True

        # 2) Built-ins por palavra-chave.
        for tool_name, action_name, keywords in _BUILTIN_KEYWORDS:
            if any(kw in normalized for kw in keywords):
                if self._run_action(tool_name, action_name, {}):
                    return True

        # 3) Pattern aberto: "<verbo> <playlist>" → spotify.play_playlist
        match = _PLAY_PATTERN.search(normalized)
        if match:
            query = match.group(1).strip()
            if query and self._run_action("spotify", "play_playlist", {"playlist": query}):
                return True

        return False

    def _run_action(self, tool_name: str, action_name: str, params: dict) -> bool:
        tool = self._tools.get(tool_name)
        if tool is None:
            print(f"[voice] tool indisponível: {tool_name}")
            return False
        action = tool.get(action_name)
        if action is None:
            print(f"[voice] action desconhecida: {tool_name}.{action_name}")
            return False
        ctx = {"persona": self._persona, "honorific": self._persona.honorific}
        try:
            reply = action.handler(tool, params, ctx)
            if reply:
                self._narrator.speak(reply)
            return True
        except SpotifyError as e:
            print(f"[voice] spotify error: {e}")
            self._narrator.speak(
                f"{self._persona.honorific}, não consegui executar — {e}."
            )
            return True
        except Exception as e:
            print(f"[voice] action {tool_name}.{action_name} falhou: {e!r}")
            self._narrator.speak(
                f"{self._persona.honorific}, falha em {tool_name}."
            )
            return True

    @staticmethod
    def _beep() -> None:
        if sys.platform != "win32":
            return
        try:
            import winsound
            winsound.Beep(BEEP_FREQ_HZ, BEEP_DURATION_MS)
        except Exception:
            pass


def _normalize_command(raw: dict) -> Optional[dict]:
    if not isinstance(raw, dict):
        return None
    trigger = (raw.get("trigger") or "").strip()
    tool = (raw.get("tool") or "").strip()
    action = (raw.get("action") or "").strip()
    if not (trigger and tool and action):
        return None
    params = raw.get("params") or {}
    if not isinstance(params, dict):
        params = {}
    return {
        "trigger": trigger,
        "trigger_normalized": _normalize_text(trigger),
        "tool": tool,
        "action": action,
        "params": dict(params),
        "description": (raw.get("description") or "").strip(),
    }
