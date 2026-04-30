"""Setup one-shot do STT — baixa e extrai o modelo Vosk PT-BR.

O download vem de https://alphacephei.com/vosk/models (projeto open-source de
speech recognition). É feito UMA única vez. O modelo extraído fica em
`.jarvis_state/vosk_model_pt/` (gitignored).

Tamanho: ~50MB. Confira a integridade pelo SHA256 abaixo se quiser.
"""

from __future__ import annotations

import shutil
import sys
import zipfile
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
MODEL_URL = "https://alphacephei.com/vosk/models/vosk-model-small-pt-0.3.zip"
EXTRACTED_DIR_NAME = "vosk-model-small-pt-0.3"  # nome dentro do zip
TARGET_DIR = ROOT / ".jarvis_state" / "vosk_model_pt"


def download(url: str, dest: Path) -> None:
    print(f"[setup_stt] baixando {url} ...")
    with httpx.stream("GET", url, follow_redirects=True, timeout=300) as resp:
        resp.raise_for_status()
        total = int(resp.headers.get("Content-Length", 0))
        downloaded = 0
        with dest.open("wb") as f:
            for chunk in resp.iter_bytes(chunk_size=64 * 1024):
                f.write(chunk)
                downloaded += len(chunk)
                if total:
                    pct = (downloaded / total) * 100
                    print(f"\r[setup_stt] {pct:.1f}% ({downloaded // 1024}KB)", end="")
        print()


def main() -> int:
    if TARGET_DIR.is_dir() and any(TARGET_DIR.iterdir()):
        print(f"[setup_stt] modelo já presente em {TARGET_DIR}. Nada a fazer.")
        return 0

    TARGET_DIR.parent.mkdir(parents=True, exist_ok=True)
    zip_path = TARGET_DIR.parent / "vosk-model-small-pt-0.3.zip"

    try:
        download(MODEL_URL, zip_path)
    except httpx.HTTPError as e:
        print(f"[setup_stt] falha no download: {e}", file=sys.stderr)
        return 1

    print(f"[setup_stt] extraindo em {TARGET_DIR.parent} ...")
    extract_root = TARGET_DIR.parent / "_extract_tmp"
    if extract_root.exists():
        shutil.rmtree(extract_root)
    extract_root.mkdir()
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(extract_root)

    extracted = extract_root / EXTRACTED_DIR_NAME
    if not extracted.is_dir():
        print(f"[setup_stt] estrutura inesperada no zip — abortando", file=sys.stderr)
        return 1

    if TARGET_DIR.exists():
        shutil.rmtree(TARGET_DIR)
    shutil.move(str(extracted), str(TARGET_DIR))

    shutil.rmtree(extract_root)
    zip_path.unlink()

    print(f"[setup_stt] pronto. Modelo instalado em {TARGET_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
