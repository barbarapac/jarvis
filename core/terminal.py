"""Helper de spawn de janelas de terminal interativo no Windows.

Centraliza a lógica de "abrir uma janela e deixar um comando pré-preenchido"
pra que diferentes tools (hoje só claude_code, amanhã possivelmente outras)
respeitem a preferência da Senhora configurada em `terminal.preferred`.

Valores aceitos:
- auto       → wt se disponível, senão cmd. Default seguro.
- wt         → Windows Terminal (`wt -d <dir> <cmd>`).
- cmd        → cmd.exe clássico (`cmd /c start "" <cmd>` com cwd).
- powershell → PowerShell 7+ ou 5 (`powershell -NoExit -Command "..."`).
- warp       → Warp via URI scheme (`warp://action/new_window?path=<dir>`).
               Limitação: Warp no Windows não aceita comando inicial,
               então abrimos só no diretório e devolvemos `manual_instruction`
               com o comando pra Senhora colar.

Em caso de FileNotFoundError no terminal escolhido, faz fallback automático
pra cmd — que sempre existe no Windows.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

VALID_TERMINALS: tuple[str, ...] = ("auto", "wt", "cmd", "powershell", "warp")


@dataclass
class SpawnResult:
    terminal_used: str
    # Quando o terminal escolhido não suporta pré-preencher comando (Warp),
    # devolvemos a string aqui pra UI/tool exibir pro usuário.
    manual_instruction: str | None = None


def normalize_preferred(value: str | None) -> str:
    v = (value or "auto").strip().lower()
    return v if v in VALID_TERMINALS else "auto"


def _resolve_auto() -> str:
    return "wt" if shutil.which("wt") else "cmd"


def _ps_quote(s: str) -> str:
    """Aspas simples PowerShell — escapa apóstrofo duplicando."""
    return "'" + s.replace("'", "''") + "'"


def spawn_terminal(
    *,
    preferred: str,
    cwd: Path,
    initial_command: list[str],
    env: dict[str, str] | None = None,
) -> SpawnResult:
    """Abre uma janela de terminal no `cwd` com `initial_command` pronto pra rodar.

    `initial_command` é a lista argv do comando que deve aparecer/rodar no
    terminal (ex: ["claude", "/softflow:concept MPC-158"]).
    """
    pref = normalize_preferred(preferred)
    if pref == "auto":
        pref = _resolve_auto()

    cwd = cwd.expanduser()
    spawn_env = dict(env) if env is not None else dict(os.environ)

    if pref == "warp":
        return _spawn_warp(cwd, initial_command, spawn_env)

    chain = [pref] if pref == "cmd" else [pref, "cmd"]
    last_error: Exception | None = None
    for term in chain:
        try:
            _spawn_concrete(term, cwd, initial_command, spawn_env)
            return SpawnResult(terminal_used=term)
        except FileNotFoundError as e:
            last_error = e
            continue

    raise RuntimeError(f"falha abrindo terminal (tentei {chain}): {last_error!r}")


def _spawn_concrete(
    terminal: str,
    cwd: Path,
    initial_command: list[str],
    env: dict[str, str],
) -> None:
    cwd_str = str(cwd)

    if terminal == "wt":
        argv = ["wt", "-d", cwd_str, *initial_command]
        subprocess.Popen(argv, shell=False, env=env)
        return

    if terminal == "cmd":
        # `start ""` abre janela nova; cwd do Popen é herdado pelo cmd.exe.
        argv = ["cmd", "/c", "start", "", *initial_command]
        subprocess.Popen(argv, cwd=cwd_str, shell=False, env=env)
        return

    if terminal == "powershell":
        if initial_command:
            invoked = "& " + " ".join(_ps_quote(p) for p in initial_command)
        else:
            invoked = ""
        script = f"Set-Location -LiteralPath {_ps_quote(cwd_str)}; {invoked}".rstrip("; ")
        # -NoExit deixa a janela aberta depois que o comando termina.
        argv = ["powershell", "-NoExit", "-Command", script]
        subprocess.Popen(argv, shell=False, env=env)
        return

    raise ValueError(f"terminal sem template: {terminal!r}")


def _spawn_warp(
    cwd: Path,
    initial_command: list[str],
    env: dict[str, str],
) -> SpawnResult:
    uri = f"warp://action/new_window?path={cwd}"
    try:
        # os.startfile dispara o handler do Windows pro warp:// URI.
        os.startfile(uri)  # type: ignore[attr-defined]
    except (OSError, AttributeError):
        # Fallback pra cmd se Warp não estiver registrado / SO não for Windows.
        try:
            _spawn_concrete("cmd", cwd, initial_command, env)
            return SpawnResult(terminal_used="cmd")
        except FileNotFoundError as e:
            raise RuntimeError(f"warp indisponível e fallback cmd falhou: {e!r}")

    instruction = " ".join(initial_command).strip() if initial_command else None
    return SpawnResult(terminal_used="warp", manual_instruction=instruction)
