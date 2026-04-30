"""Captura de áudio do microfone via sounddevice.

Saída padrão: PCM mono 16kHz int16 — formato esperado pelo Vosk.
"""

from __future__ import annotations

import time

import numpy as np
import sounddevice as sd

SAMPLE_RATE_HZ = 16000
CHANNELS = 1
DTYPE = "int16"


def record(duration_seconds: float) -> bytes:
    """Grava `duration_seconds` segundos do microfone padrão (duração fixa)."""
    frames = int(duration_seconds * SAMPLE_RATE_HZ)
    recording = sd.rec(
        frames=frames,
        samplerate=SAMPLE_RATE_HZ,
        channels=CHANNELS,
        dtype=DTYPE,
    )
    sd.wait()
    return recording.tobytes()


def record_with_vad(
    max_duration_seconds: float = 6.0,
    silence_after_speech_ms: int = 700,
    initial_silence_grace_seconds: float = 2.0,
    rms_threshold: int = 600,
    chunk_ms: int = 50,
) -> bytes:
    """Grava do microfone até detectar silêncio prolongado, com cap em max_duration.

    Energy-based VAD (sem deps adicionais). Retorna PCM bruto.

    - `initial_silence_grace_seconds`: tempo antes de assumir "sem fala" e devolver
    - `silence_after_speech_ms`: silêncio que precisa persistir DEPOIS da fala
      pra encerrar a gravação
    - `rms_threshold`: amplitude RMS abaixo da qual é considerado silêncio
      (ajuste conforme seu microfone — 600 funciona pra mics típicos de notebook)
    """
    chunk_frames = int(SAMPLE_RATE_HZ * chunk_ms / 1000)
    silence_chunks_needed = max(1, silence_after_speech_ms // chunk_ms)

    audio_chunks: list[np.ndarray] = []
    silent_count = 0
    speech_started = False

    with sd.InputStream(
        samplerate=SAMPLE_RATE_HZ,
        channels=CHANNELS,
        dtype=DTYPE,
        blocksize=chunk_frames,
    ) as stream:
        start = time.monotonic()
        while time.monotonic() - start < max_duration_seconds:
            data, _ = stream.read(chunk_frames)
            audio_chunks.append(np.array(data, copy=True))

            rms = float(np.sqrt(np.mean(data.astype(np.float32) ** 2)))
            if rms > rms_threshold:
                speech_started = True
                silent_count = 0
            else:
                silent_count += 1

            if speech_started and silent_count >= silence_chunks_needed:
                break

            # Sem fala detectada na janela de tolerância → encerra cedo.
            if (
                not speech_started
                and (time.monotonic() - start) >= initial_silence_grace_seconds
            ):
                break

    return b"".join(chunk.tobytes() for chunk in audio_chunks)


def list_devices() -> str:
    """Devolve a lista de devices de áudio disponíveis (debug)."""
    return str(sd.query_devices())
