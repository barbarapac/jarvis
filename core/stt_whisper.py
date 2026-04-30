"""STT via faster-whisper.

Mais lento que Vosk (1-2s extras) mas muito mais preciso em PT-BR.
Modelo é baixado automaticamente em ~/.cache/huggingface/hub na primeira
execução. Tamanhos: tiny (~75MB) < base (~150MB) < small (~500MB) <
medium (~1.5GB) < large-v3 (~3GB).

Para uma máquina típica sem GPU, "small" é o melhor compromisso de
qualidade × latência.
"""

from __future__ import annotations

from typing import Optional

import numpy as np


class WhisperSTT:
    name = "whisper"

    def __init__(
        self,
        model_size: str = "small",
        device: str = "auto",
        compute_type: str = "auto",
        language: str = "pt",
        beam_size: int = 1,
    ) -> None:
        # Importa lazy pra não pagar o custo se a engine não for usada.
        from faster_whisper import WhisperModel

        self._model = WhisperModel(
            model_size,
            device=device,
            compute_type=compute_type,
        )
        self._language = language
        self._beam_size = beam_size

    def transcribe(self, pcm_bytes: bytes) -> str:
        """PCM mono 16kHz int16 → texto."""
        if not pcm_bytes:
            return ""
        # int16 → float32 normalizado em [-1, 1] (formato que Whisper espera)
        audio = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0
        segments, _info = self._model.transcribe(
            audio,
            language=self._language,
            beam_size=self._beam_size,
            vad_filter=True,
        )
        return " ".join(s.text.strip() for s in segments).strip()


def try_build_whisper(cfg: dict) -> Optional["WhisperSTT"]:
    """Constrói se possível, devolve None reportando o erro caso contrário."""
    try:
        return WhisperSTT(
            model_size=cfg.get("model_size", "small"),
            device=cfg.get("device", "auto"),
            compute_type=cfg.get("compute_type", "auto"),
            language=cfg.get("language", "pt"),
            beam_size=int(cfg.get("beam_size", 1)),
        )
    except Exception as e:
        print(f"[jarvis] Whisper STT falhou: {e!r}")
        return None
