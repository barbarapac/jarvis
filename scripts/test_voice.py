"""Smoke test do pipeline de voz: grava 5s, transcreve via Vosk, mostra intent.

NÃO chama TTS — totalmente local, sem rede.
"""

from __future__ import annotations

import sys
from pathlib import Path

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.audio_recorder import record
from core.intent import parse_yes_no
from core.stt import VoskSTT

DURATION = 5.0
MODEL_DIR = ROOT / ".jarvis_state" / "vosk_model_pt"


def main() -> int:
    if not MODEL_DIR.is_dir():
        print(
            f"Modelo Vosk não encontrado em {MODEL_DIR}.\n"
            f"Rode primeiro: py scripts/setup_stt.py",
            file=sys.stderr,
        )
        return 1

    stt = VoskSTT(MODEL_DIR)
    print(f"[test] gravando {DURATION:.0f}s — fale agora (tente 'sim' ou 'não')...")
    audio = record(DURATION)
    print(f"[test] {len(audio)} bytes capturados. Transcrevendo...")
    text = stt.transcribe(audio)
    intent = parse_yes_no(text)
    print(f"[test] transcrição: {text!r}")
    print(f"[test] intent: {intent.value}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
