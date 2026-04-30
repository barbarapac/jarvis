"""Captura de áudio do microfone via sounddevice.

Saída padrão: PCM mono 16kHz int16 — formato esperado pelo Vosk.
"""

from __future__ import annotations

import sounddevice as sd

SAMPLE_RATE_HZ = 16000
CHANNELS = 1
DTYPE = "int16"


def record(duration_seconds: float) -> bytes:
    """Grava `duration_seconds` segundos do microfone padrão.

    Bloqueia até a gravação terminar. Retorna PCM bruto (sem header WAV).
    """
    frames = int(duration_seconds * SAMPLE_RATE_HZ)
    recording = sd.rec(
        frames=frames,
        samplerate=SAMPLE_RATE_HZ,
        channels=CHANNELS,
        dtype=DTYPE,
    )
    sd.wait()
    return recording.tobytes()


def list_devices() -> str:
    """Devolve a lista de devices de áudio disponíveis (debug)."""
    return str(sd.query_devices())
