"""Pluggable TTS engines.

Cada engine sabe sintetizar texto em áudio. O `Narrator` cuida da reprodução.
Adicionar um novo engine = nova classe que implementa `TTSEngine` + entrada
no factory `build_engine`.
"""

from __future__ import annotations

import asyncio
import io
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class SynthesisResult:
    audio: bytes
    format: str  # "mp3" | "wav"


class TTSEngine(ABC):
    name: str

    @abstractmethod
    def synthesize(self, text: str) -> SynthesisResult: ...


class FishAudioEngine(TTSEngine):
    """Voz clonada via fish.audio. Requer cartão cadastrado (pay-as-you-go)."""

    name = "fish_audio"

    def __init__(self, api_key: str, voice_id: str) -> None:
        if not api_key:
            raise ValueError("FISH_AUDIO_API_KEY ausente")
        if not voice_id:
            raise ValueError("FISH_AUDIO_VOICE_ID ausente")
        from fish_audio_sdk import Session

        self._session = Session(api_key)
        self._voice_id = voice_id

    def synthesize(self, text: str) -> SynthesisResult:
        from fish_audio_sdk import TTSRequest

        request = TTSRequest(
            reference_id=self._voice_id,
            text=text,
            format="wav",
        )
        buffer = io.BytesIO()
        for chunk in self._session.tts(request):
            buffer.write(chunk)
        return SynthesisResult(audio=buffer.getvalue(), format="wav")


class EdgeTTSEngine(TTSEngine):
    """Voz neural Microsoft via Edge browser endpoint. Grátis, sem API key."""

    name = "edge_tts"

    def __init__(
        self,
        voice: str = "pt-BR-AntonioNeural",
        rate: str = "+0%",
        pitch: str = "+0Hz",
    ) -> None:
        self._voice = voice
        self._rate = rate
        self._pitch = pitch

    def synthesize(self, text: str) -> SynthesisResult:
        audio = asyncio.run(self._stream(text))
        return SynthesisResult(audio=audio, format="mp3")

    async def _stream(self, text: str) -> bytes:
        import edge_tts

        communicate = edge_tts.Communicate(
            text, self._voice, rate=self._rate, pitch=self._pitch
        )
        buffer = io.BytesIO()
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                buffer.write(chunk["data"])
        return buffer.getvalue()


def build_engine(config: dict, env: dict[str, str] | None = None) -> TTSEngine:
    """Cria o engine selecionado em config['narrator']['engine']."""
    env = env if env is not None else dict(os.environ)
    narrator_cfg = config.get("narrator", {})
    engine_name = narrator_cfg.get("engine", "edge_tts")

    if engine_name == "fish_audio":
        return FishAudioEngine(
            api_key=env.get("FISH_AUDIO_API_KEY", ""),
            voice_id=env.get("FISH_AUDIO_VOICE_ID", ""),
        )

    if engine_name == "edge_tts":
        edge_cfg = narrator_cfg.get("edge_tts", {}) or {}
        return EdgeTTSEngine(
            voice=edge_cfg.get("voice", "pt-BR-AntonioNeural"),
            rate=edge_cfg.get("rate", "+0%"),
            pitch=edge_cfg.get("pitch", "+0Hz"),
        )

    raise ValueError(f"TTS engine desconhecido: {engine_name!r}")
