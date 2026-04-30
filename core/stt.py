"""Speech-to-text local via Vosk (offline, sem cloud)."""

from __future__ import annotations

import json
from pathlib import Path

from vosk import KaldiRecognizer, Model, SetLogLevel

from core.audio_recorder import SAMPLE_RATE_HZ

# Silencia logs do Vosk pra não poluir o stdout.
SetLogLevel(-1)


class VoskSTT:
    def __init__(self, model_dir: Path) -> None:
        if not model_dir.is_dir():
            raise FileNotFoundError(
                f"Modelo Vosk não encontrado em {model_dir}. "
                f"Rode `py scripts/setup_stt.py` primeiro."
            )
        self._model = Model(str(model_dir))

    @property
    def model(self) -> Model:
        """Modelo Vosk carregado — para reuso (ex: wake word listener)."""
        return self._model

    def transcribe(self, audio_pcm: bytes) -> str:
        """Recebe PCM mono 16kHz int16 e devolve a transcrição final."""
        recognizer = KaldiRecognizer(self._model, SAMPLE_RATE_HZ)
        recognizer.SetWords(False)
        recognizer.AcceptWaveform(audio_pcm)
        result = json.loads(recognizer.FinalResult())
        return (result.get("text") or "").strip()
