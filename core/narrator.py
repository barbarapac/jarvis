"""Narrator: recebe um TTSEngine e cuida da reprodução do áudio.

A síntese é feita por **chunks** (sentenças agrupadas). Um produtor em
thread sintetiza o próximo chunk enquanto o atual ainda toca, então a
primeira frase começa a tocar muito antes da resposta inteira terminar
de sintetizar — drasticamente reduz o "time-to-first-audio" em respostas
longas.

Antes de mandar pro TTS, o texto é sanitizado de markdown: `**negrito**`,
`*itálico*`, `` `código` ``, `# cabeçalho`, `[link](url)` e marcadores de
lista são removidos pra não serem lidos literalmente pela voz.
"""

from __future__ import annotations

import os
import queue
import re
import tempfile
import threading
from pathlib import Path
from typing import Protocol

# IMPORTANTE: importar playsound3 ANTES de qualquer engine TTS que use HTTP/COM
# (fish_audio_sdk inicializa estado de Windows que conflita com o import tardio
# do playsound3 e causa crash 0xE0000067).
from playsound3 import playsound

from core.event_bus import EventBus, EventType
from core.tts import TTSEngine

# Tamanho-alvo de chunk pra síntese. Curto demais soa picotado; longo demais
# atrasa o time-to-first-audio. ~80 a 220 chars dá frases naturais.
_CHUNK_MIN_CHARS = 80
_CHUNK_MAX_CHARS = 220


class SpeakingNarrator(Protocol):
    def speak(self, text: str) -> None: ...


class DryRunNarrator:
    """Narrator que só imprime — não chama TTS nem toca áudio.

    Uso: testes e desenvolvimento, pra evitar chamadas desnecessárias à API do
    TTS (que custam créditos e geram tráfego que pode chamar atenção do time
    de segurança).
    """

    def speak(self, text: str) -> None:
        print(f"[narrator dry-run] {text}")


class Narrator:
    def __init__(
        self,
        engine: TTSEngine,
        volume: float = 0.9,
        cache_dir: Path | None = None,
        event_bus: EventBus | None = None,
    ) -> None:
        self._engine = engine
        self._volume = max(0.0, min(1.0, volume))
        self._cache_dir = cache_dir
        if cache_dir is not None:
            cache_dir.mkdir(parents=True, exist_ok=True)
        # Serializa speak() entre threads (watcher, code_review, etc.).
        self._lock = threading.Lock()
        self._event_bus = event_bus
        # Histórico curto da fala atual — quem orquestra um turno (ex.:
        # VoiceCommander) reseta antes do dispatch e lê depois pra registrar
        # no vault. Lista porque um turno pode emitir múltiplas falas curtas.
        self._spoken_buffer: list[str] = []

    def speak(self, text: str) -> None:
        """Sintetiza e toca em modo bloqueante. Thread-safe.

        Quebra o texto em chunks e usa pipeline produtor/consumidor: enquanto
        um chunk toca, o próximo já vai sendo sintetizado.
        """
        text = (text or "").strip()
        if not text:
            return

        # Pro buffer e eventos a gente preserva o texto original (com
        # acentos e tudo) — ele entra no histórico/vault. O que vai pro TTS
        # é a versão higienizada, sem markdown.
        speakable = _strip_markdown_for_speech(text)
        if not speakable:
            return
        chunks = _chunk_for_speech(speakable)

        with self._lock:
            self._spoken_buffer.append(text)
            if self._event_bus:
                self._event_bus.publish(EventType.SPEAKING_STARTED, text=text)
            try:
                self._stream_chunks(chunks)
            finally:
                if self._event_bus:
                    self._event_bus.publish(EventType.SPEAKING_ENDED, text=text)

    def reset_spoken_buffer(self) -> None:
        with self._lock:
            self._spoken_buffer.clear()

    def drain_spoken_buffer(self) -> str:
        """Devolve as falas acumuladas concatenadas e zera o buffer."""
        with self._lock:
            joined = "\n\n".join(self._spoken_buffer).strip()
            self._spoken_buffer.clear()
            return joined

    def _stream_chunks(self, chunks: list[str]) -> None:
        """Pipeline: produtor sintetiza em thread, consumidor (este) toca."""
        if not chunks:
            return

        # Fila pequena: até 2 chunks pré-sintetizados na frente do que toca.
        # Mais que isso só atrasa o shutdown se a usuária interromper.
        audio_q: queue.Queue = queue.Queue(maxsize=2)
        sentinel: object = object()

        def producer() -> None:
            for chunk in chunks:
                if not chunk.strip():
                    continue
                try:
                    result = self._engine.synthesize(chunk)
                except Exception as e:  # rede, API, etc.
                    audio_q.put(e)
                    return
                audio_q.put((result.audio, result.format))
            audio_q.put(sentinel)

        t = threading.Thread(target=producer, name="tts-producer", daemon=True)
        t.start()

        try:
            while True:
                item = audio_q.get()
                if item is sentinel:
                    break
                if isinstance(item, Exception):
                    raise item
                audio, fmt = item
                self._play(audio, fmt)
        finally:
            t.join(timeout=2.0)

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


# ---------- Sanitização de markdown ----------

# Code fences ``` ... ``` (multi-linha) — mantém o conteúdo, descarta cercas.
_RE_FENCE = re.compile(r"```[a-zA-Z0-9_-]*\n?([\s\S]*?)```", re.MULTILINE)
# Inline code `texto`.
_RE_INLINE_CODE = re.compile(r"`([^`\n]+)`")
# Bold/italic — **x**, __x__, *x*, _x_. Ordem importa (bold antes de italic).
_RE_BOLD_STAR = re.compile(r"\*\*([^*\n]+?)\*\*")
_RE_BOLD_UNDER = re.compile(r"__([^_\n]+?)__")
_RE_ITALIC_STAR = re.compile(r"(?<!\*)\*([^*\n]+?)\*(?!\*)")
_RE_ITALIC_UNDER = re.compile(r"(?<!_)_([^_\n]+?)_(?!_)")
_RE_STRIKE = re.compile(r"~~([^~\n]+?)~~")
# Links [label](url) — mantém só o label.
_RE_LINK = re.compile(r"\[([^\]]+)\]\((?:[^)\s]+)\)")
# Cabeçalhos: linhas começando com #, ##, etc. — remove o # mas mantém o texto.
_RE_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+", re.MULTILINE)
# Marcadores de lista no início da linha: -, *, +, ou "1."  — remove o marcador.
_RE_LIST_BULLET = re.compile(r"^\s*[-*+]\s+", re.MULTILINE)
_RE_LIST_NUM = re.compile(r"^\s*\d+\.\s+", re.MULTILINE)
# Blockquote ">".
_RE_QUOTE = re.compile(r"^\s*>\s?", re.MULTILINE)
# Emphasis residual com asteriscos isolados ou backticks órfãos.
_RE_RESIDUAL = re.compile(r"[*`_~]+")
# Múltiplas quebras de linha viram só uma pausa.
_RE_BLANK_LINES = re.compile(r"\n\s*\n+")


def _strip_markdown_for_speech(text: str) -> str:
    """Remove formatação markdown que o TTS leria literalmente.

    Não pretende ser um parser markdown completo — só limpa o que aparece
    nas respostas do LLM e atrapalha a fala (asteriscos, crases, hashes,
    bullets, sintaxe de link).
    """
    if not text:
        return ""
    s = _RE_FENCE.sub(lambda m: " " + m.group(1).strip() + " ", text)
    s = _RE_INLINE_CODE.sub(r"\1", s)
    s = _RE_LINK.sub(r"\1", s)
    s = _RE_BOLD_STAR.sub(r"\1", s)
    s = _RE_BOLD_UNDER.sub(r"\1", s)
    s = _RE_ITALIC_STAR.sub(r"\1", s)
    s = _RE_ITALIC_UNDER.sub(r"\1", s)
    s = _RE_STRIKE.sub(r"\1", s)
    s = _RE_HEADING.sub("", s)
    s = _RE_LIST_BULLET.sub("", s)
    s = _RE_LIST_NUM.sub("", s)
    s = _RE_QUOTE.sub("", s)
    s = _RE_RESIDUAL.sub("", s)
    s = _RE_BLANK_LINES.sub(". ", s)
    s = s.replace("\n", " ")
    return " ".join(s.split()).strip()


# ---------- Chunking por sentenças ----------

# Separa após ., !, ? ou ; mantendo o terminador. Não quebra em decimais
# (\d.\d) nem em abreviações curtas (Sr., Dr.) graças ao lookbehind por
# letra ou espaço seguido de espaço + letra maiúscula/início.
_RE_SENT_SPLIT = re.compile(r"(?<=[\.\?\!;])\s+(?=[A-ZÀ-ÚŒ\"\'¿¡])")


def _split_sentences(text: str) -> list[str]:
    parts = [p.strip() for p in _RE_SENT_SPLIT.split(text) if p.strip()]
    return parts or ([text] if text.strip() else [])


def _chunk_for_speech(text: str) -> list[str]:
    """Agrupa sentenças em chunks entre _CHUNK_MIN_CHARS e _CHUNK_MAX_CHARS."""
    sentences = _split_sentences(text)
    chunks: list[str] = []
    current = ""
    for s in sentences:
        if not current:
            current = s
            continue
        joined = current + " " + s
        # Se o atual ainda é curto demais, força agrupar.
        if len(current) < _CHUNK_MIN_CHARS:
            current = joined
            continue
        # Se juntar caberia e segue natural, junta.
        if len(joined) <= _CHUNK_MAX_CHARS:
            current = joined
            continue
        chunks.append(current)
        current = s
    if current:
        chunks.append(current)
    return chunks
