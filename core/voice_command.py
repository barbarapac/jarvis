"""Orquestrador de comandos por voz.

Entry points:
- `on_hotkey()` — push-to-talk no teclado
- `on_wake_word()` — wake word detectado, mesmo fluxo do PTT
- `handle_audio(pcm_bytes)` — áudio já gravado (vindo do botão mic da UI)
- `handle_text(text)` — texto digitado na UI

Dispatch:
1. Tenta keyword/regex local (Spotify, etc.) — rápido, offline.
2. Se não casar e houver agente Claude, chama agente com tools dos MCPs.
3. Senão, fala "não reconhecido".
"""

from __future__ import annotations

import re
import sys
import threading
from typing import Optional

from core.audio_recorder import record_with_vad
from core.event_bus import EventBus, EventType
from core.narrator import SpeakingNarrator
from core.persona import Persona
from core.stt import VoskSTT
from tools.spotify import SpotifyError, SpotifyTool

COMMAND_MAX_DURATION_SECONDS = 5.0

BEEP_FREQ_HZ = 880
BEEP_DURATION_MS = 80

_PAUSE_KEYWORDS = ("pausa", "pausar", "pause", "para", "pare", "parar")
_RESUME_KEYWORDS = ("retoma", "retomar", "continua", "continuar", "play", "voltar a tocar")
_NEXT_KEYWORDS = ("próxima", "proxima", "next", "skip", "pula", "pular")
_PREV_KEYWORDS = ("anterior", "voltar", "volta", "previous")
_CURRENT_KEYWORDS = ("qual música", "qual musica", "que música", "que musica", "que canção", "que cancao")

_PLAY_PATTERN = re.compile(
    r"\b(?:toca|tocar|coloca|colocar|p[oõ]e|por|botar?|bota)\s+(.+?)$"
)


class VoiceCommander:
    def __init__(
        self,
        stt: VoskSTT,
        narrator: SpeakingNarrator,
        persona: Persona,
        spotify: Optional[SpotifyTool] = None,
        event_bus: Optional[EventBus] = None,
        agent: Optional[object] = None,  # JarvisAgent — duck-typed pra evitar import circular
    ) -> None:
        self._stt = stt
        self._narrator = narrator
        self._persona = persona
        self._spotify = spotify
        self._event_bus = event_bus
        self._agent = agent
        self._busy_lock = threading.Lock()

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
        if self._spotify is None:
            return False
        try:
            if any(kw in text for kw in _PAUSE_KEYWORDS):
                self._spotify.pause()
                self._narrator.speak("Pausado.")
                return True
            if any(kw in text for kw in _RESUME_KEYWORDS):
                self._spotify.resume()
                self._narrator.speak("Retomando.")
                return True
            if any(kw in text for kw in _NEXT_KEYWORDS):
                self._spotify.next_track()
                self._narrator.speak("Próxima.")
                return True
            if any(kw in text for kw in _PREV_KEYWORDS):
                self._spotify.previous_track()
                self._narrator.speak("Anterior.")
                return True
            if any(kw in text for kw in _CURRENT_KEYWORDS):
                track = self._spotify.current_track()
                if track:
                    self._narrator.speak(f"{track}, {self._persona.honorific}.")
                else:
                    self._narrator.speak(f"Nada tocando, {self._persona.honorific}.")
                return True
            match = _PLAY_PATTERN.search(text)
            if match:
                query = match.group(1).strip()
                if query:
                    found = self._spotify.play_playlist(query)
                    self._narrator.speak(f"Tocando {found}.")
                    return True
        except SpotifyError as e:
            print(f"[voice] spotify error: {e}")
            self._narrator.speak(
                f"{self._persona.honorific}, não consegui executar — {e}."
            )
            return True
        return False

    @staticmethod
    def _beep() -> None:
        if sys.platform != "win32":
            return
        try:
            import winsound
            winsound.Beep(BEEP_FREQ_HZ, BEEP_DURATION_MS)
        except Exception:
            pass
