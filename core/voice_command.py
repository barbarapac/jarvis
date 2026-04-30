"""Orquestrador de comandos por voz (push-to-talk).

Fluxo: hotkey → beep → record com VAD → STT → parse local → executa tool.
Parsing é regex/keywords PT-BR (sem LLM). Falha graciosamente quando não
entende.
"""

from __future__ import annotations

import re
import sys
import threading
from typing import Optional

from core.audio_recorder import record_with_vad
from core.narrator import SpeakingNarrator
from core.persona import Persona
from core.stt import VoskSTT
from tools.spotify import SpotifyError, SpotifyTool

# Quanto a janela de gravação pode durar quando você pressiona o hotkey.
COMMAND_MAX_DURATION_SECONDS = 5.0

# Beep curto pra confirmar que o hotkey foi reconhecido. Frequência/duração
# escolhidas pra não atrapalhar (curto, agudo).
BEEP_FREQ_HZ = 880
BEEP_DURATION_MS = 80

_PAUSE_KEYWORDS = ("pausa", "pausar", "pause", "para", "pare", "parar")
_RESUME_KEYWORDS = ("retoma", "retomar", "continua", "continuar", "play", "voltar a tocar")
_NEXT_KEYWORDS = ("próxima", "proxima", "next", "skip", "pula", "pular")
_PREV_KEYWORDS = ("anterior", "voltar", "volta", "previous")
_CURRENT_KEYWORDS = ("qual música", "qual musica", "que música", "que musica", "que canção", "que cancao")

# Regex pra extrair query de "toca/coloca/põe/bota X"
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
    ) -> None:
        self._stt = stt
        self._narrator = narrator
        self._persona = persona
        self._spotify = spotify
        # Garante que só um comando seja processado por vez.
        self._busy_lock = threading.Lock()

    def on_hotkey(self) -> None:
        """Callback do HotkeyListener. Pula se já estiver processando outro."""
        if not self._busy_lock.acquire(blocking=False):
            print("[voice] já processando um comando — ignorando hotkey duplo.")
            return
        try:
            self._capture_and_dispatch()
        finally:
            self._busy_lock.release()

    def _capture_and_dispatch(self) -> None:
        self._beep()
        print("[voice] ouvindo...")
        audio = record_with_vad(max_duration_seconds=COMMAND_MAX_DURATION_SECONDS)
        text = self._stt.transcribe(audio)
        print(f"[voice] transcrição: {text!r}")

        if not text:
            self._narrator.speak(f"Não captei, {self._persona.honorific}.")
            return

        if not self._dispatch(text.lower()):
            self._narrator.speak(
                f"Comando não reconhecido, {self._persona.honorific}."
            )

    def _dispatch(self, text: str) -> bool:
        """Roteia para o handler apropriado. Retorna True se reconheceu."""
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
            pass  # beep falhar não pode quebrar o fluxo
