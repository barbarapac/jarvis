"""Narrator: recebe um TTSEngine e cuida da reprodução do áudio."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

# IMPORTANTE: importar playsound3 ANTES de qualquer engine TTS que use HTTP/COM
# (fish_audio_sdk inicializa estado de Windows que conflita com o import tardio
# do playsound3 e causa crash 0xE0000067).
from playsound3 import playsound

from core.tts import TTSEngine


class Narrator:
    def __init__(
        self,
        engine: TTSEngine,
        volume: float = 0.9,
        cache_dir: Path | None = None,
    ) -> None:
        self._engine = engine
        self._volume = max(0.0, min(1.0, volume))
        self._cache_dir = cache_dir
        if cache_dir is not None:
            cache_dir.mkdir(parents=True, exist_ok=True)

    def speak(self, text: str) -> None:
        """Sintetiza e toca em modo bloqueante."""
        result = self._engine.synthesize(text)
        self._play(result.audio, result.format)

    def _play(self, audio_bytes: bytes, fmt: str) -> None:
        suffix = f".{fmt}"
        with tempfile.NamedTemporaryFile(
            suffix=suffix, delete=False, dir=self._cache_dir
        ) as tmp:
            tmp.write(audio_bytes)
            tmp_path = tmp.name

        try:
            playsound(tmp_path, block=True)
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
