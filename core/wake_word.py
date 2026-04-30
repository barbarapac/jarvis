"""Detector de wake word ("Jarvis") via Vosk em streaming contínuo.

Roda em thread daemon. Quando detecta a palavra-chave no transcript
parcial, fecha o stream, dispara o callback (em outra thread) e
reabre o stream após o callback retornar — pra liberar o mic durante
o fluxo de comando.

Mic compartilhado: só um stream ativo por vez. PTT e wake word usam o
mesmo device; durante o comando, o wake word fica pausado.
"""

from __future__ import annotations

import json
import queue
import threading
import time
from pathlib import Path
from typing import Callable

import sounddevice as sd
from vosk import KaldiRecognizer, Model

from core.audio_recorder import SAMPLE_RATE_HZ

# Variantes que o Vosk PT-BR costuma cuspir pra "jarvis" — alternativas comuns.
WAKE_WORD_VARIANTS = (
    "jarvis",
    "jarvi",
    "jarves",
    "javis",
    "jervis",
)

# Tamanho do bloco em frames pra processamento incremental.
BLOCK_FRAMES = 4000  # 250ms @ 16kHz


class WakeWordListener:
    def __init__(
        self,
        model: Model,
        on_detected: Callable[[], None],
        cooldown_seconds: float = 1.0,
    ) -> None:
        self._model = model
        self._on_detected = on_detected
        self._cooldown_seconds = cooldown_seconds
        self._stop_evt = threading.Event()
        self._paused_evt = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop_evt.clear()
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="wake-word"
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_evt.set()
        if self._thread:
            self._thread.join(timeout=2.0)
        self._thread = None

    def pause(self) -> None:
        """Pausa o consumo de áudio (libera o mic)."""
        self._paused_evt.set()

    def resume(self) -> None:
        self._paused_evt.clear()

    def _run(self) -> None:
        recognizer = KaldiRecognizer(self._model, SAMPLE_RATE_HZ)
        recognizer.SetWords(False)
        last_detection = 0.0

        while not self._stop_evt.is_set():
            if self._paused_evt.is_set():
                time.sleep(0.1)
                continue
            try:
                audio_q: queue.Queue[bytes] = queue.Queue(maxsize=10)

                def callback(indata, frames, time_info, status):
                    if status:
                        # overruns/etc — só logamos, não interrompemos
                        pass
                    try:
                        audio_q.put_nowait(bytes(indata))
                    except queue.Full:
                        pass

                with sd.RawInputStream(
                    samplerate=SAMPLE_RATE_HZ,
                    blocksize=BLOCK_FRAMES,
                    dtype="int16",
                    channels=1,
                    callback=callback,
                ):
                    while not self._stop_evt.is_set() and not self._paused_evt.is_set():
                        try:
                            chunk = audio_q.get(timeout=0.2)
                        except queue.Empty:
                            continue
                        recognizer.AcceptWaveform(chunk)
                        partial = json.loads(recognizer.PartialResult()).get("partial", "")
                        if not partial:
                            continue
                        if not _contains_wake_word(partial):
                            continue
                        now = time.monotonic()
                        if now - last_detection < self._cooldown_seconds:
                            continue
                        last_detection = now
                        # Limpa o recognizer pra próxima rodada não casar de novo
                        recognizer = KaldiRecognizer(self._model, SAMPLE_RATE_HZ)
                        recognizer.SetWords(False)
                        break

            except Exception as e:
                print(f"[wake] erro no stream: {e!r}")
                time.sleep(1.0)
                continue

            # Se chegou aqui via wake word detectada, dispara callback
            # (fora do `with` pra liberar o mic).
            if self._stop_evt.is_set():
                break
            if self._paused_evt.is_set():
                continue

            try:
                self._on_detected()
            except Exception as e:
                print(f"[wake] callback falhou: {e!r}")

            # cooldown extra antes de reabrir o stream
            time.sleep(0.3)


def _contains_wake_word(text: str) -> bool:
    norm = text.lower().strip()
    if not norm:
        return False
    # Match em qualquer token isolado (evita falso-positivo em palavras maiores)
    tokens = norm.split()
    return any(tok in WAKE_WORD_VARIANTS for tok in tokens)
