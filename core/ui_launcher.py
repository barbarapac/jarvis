"""Lança Edge em modo --app pra criar uma janela tipo desktop apontando pro server."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

# Caminhos comuns onde o Edge fica instalado no Windows.
EDGE_PATHS = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]


def find_edge() -> str | None:
    for path in EDGE_PATHS:
        if Path(path).exists():
            return path
    on_path = shutil.which("msedge") or shutil.which("microsoft-edge")
    return on_path


def launch_app_window(url: str, *, width: int = 460, height: int = 720) -> bool:
    """Abre o Edge em --app mode (sem chrome de browser). Retorna True se conseguiu."""
    edge = find_edge()
    if not edge:
        print("[ui] Edge não encontrado — abrindo no browser default.", file=sys.stderr)
        import webbrowser
        webbrowser.open(url)
        return False

    try:
        subprocess.Popen(
            [
                edge,
                f"--app={url}",
                f"--window-size={width},{height}",
                "--disable-features=msPdfReader",
                "--no-first-run",
            ]
        )
        return True
    except Exception as e:
        print(f"[ui] falha ao abrir Edge --app: {e}", file=sys.stderr)
        return False
