"""Tool de code review: spawna `claude -p "/hp:review ..."` em background.

Recebe info do MR (project path, source branch), localiza o working_dir do
projeto pelo mapa de config, executa a skill em thread separada pra não
bloquear o event loop, e avisa via narrator quando termina.
"""

from __future__ import annotations

import shlex
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path

from core.narrator import SpeakingNarrator
from core.persona import Persona


@dataclass(frozen=True)
class ReviewRequest:
    project_full_path: str  # ex: "softplan/justica/projeto-x"
    source_branch: str
    mr_url: str
    mr_title: str


class CodeReviewTool:
    name = "code_review"

    def __init__(
        self,
        project_dirs: dict[str, str],
        command_template: list[str],
        narrator: SpeakingNarrator,
        persona: Persona,
        dry_run: bool = False,
    ) -> None:
        self._project_dirs = {k: Path(v) for k, v in project_dirs.items()}
        self._command_template = command_template
        self._narrator = narrator
        self._persona = persona
        self.dry_run = dry_run
        self._busy_lock = threading.Lock()

    def can_review(self, project_full_path: str) -> bool:
        """True se o projeto está mapeado pra um working_dir local."""
        return project_full_path in self._project_dirs

    def run_async(self, request: ReviewRequest) -> None:
        """Dispara a review em thread; retorna imediatamente."""
        if not self.can_review(request.project_full_path):
            self._narrator.speak(
                f"{self._persona.honorific}, não tenho diretório local "
                f"configurado para o projeto {request.project_full_path}."
            )
            return

        if not self._busy_lock.acquire(blocking=False):
            self._narrator.speak(
                f"{self._persona.honorific}, já estou executando uma revisão. "
                f"Aguardarei terminar antes de iniciar outra."
            )
            return

        thread = threading.Thread(
            target=self._run, args=(request,), daemon=True, name="code-review"
        )
        thread.start()

    def _run(self, request: ReviewRequest) -> None:
        try:
            working_dir = self._project_dirs[request.project_full_path]
            command = [
                part.format(
                    source_branch=request.source_branch,
                    mr_url=request.mr_url,
                )
                for part in self._command_template
            ]

            print(
                f"[code_review] cwd={working_dir}\n"
                f"              cmd={shlex.join(command)}"
            )
            self._narrator.speak(
                f"Iniciando revisão da branch {request.source_branch}, "
                f"{self._persona.honorific}."
            )

            if self.dry_run:
                print("[code_review] dry-run: comando não executado.")
                self._narrator.speak(
                    f"Modo simulação ativo. Nenhuma revisão foi executada."
                )
                return

            result = subprocess.run(
                command,
                cwd=str(working_dir),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )

            if result.returncode == 0:
                print(f"[code_review] concluída (rc=0).\n{result.stdout[-2000:]}")
                self._narrator.speak(
                    f"Revisão concluída, {self._persona.honorific}. "
                    f"Findings disponíveis no projeto."
                )
            else:
                print(
                    f"[code_review] falhou (rc={result.returncode})\n"
                    f"stderr: {result.stderr[-1000:]}"
                )
                self._narrator.speak(
                    f"{self._persona.honorific}, a revisão falhou. "
                    f"Verifique o terminal para detalhes."
                )
        except Exception as e:
            print(f"[code_review] erro inesperado: {e!r}")
            self._narrator.speak(
                f"{self._persona.honorific}, erro inesperado durante a revisão."
            )
        finally:
            self._busy_lock.release()
