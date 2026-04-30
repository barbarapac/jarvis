"""Tool de review (código ou documentação): spawna `claude -p` em background.

Cada projeto mapeado tem um `path` (clone local) e um `type` (`code` ou `docs`,
ou customizado). O tipo seleciona o template de comando a ser executado. Roda
em thread separada pra não bloquear o event loop e avisa via narrator quando
termina.
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


@dataclass(frozen=True)
class ProjectConfig:
    path: Path
    type: str  # "code" | "docs" | qualquer chave em `commands`


class CodeReviewTool:
    name = "code_review"

    def __init__(
        self,
        project_dirs: dict[str, ProjectConfig],
        commands: dict[str, list[str]],
        narrator: SpeakingNarrator,
        persona: Persona,
        dry_run: bool = False,
    ) -> None:
        self._project_dirs = project_dirs
        self._commands = commands
        self._narrator = narrator
        self._persona = persona
        self.dry_run = dry_run
        self._busy_lock = threading.Lock()

    def can_review(self, project_full_path: str) -> bool:
        """True se o projeto está mapeado E o tipo dele tem comando definido."""
        proj = self._project_dirs.get(project_full_path)
        if proj is None:
            return False
        return proj.type in self._commands

    def run_async(self, request: ReviewRequest) -> None:
        """Dispara a review em thread; retorna imediatamente."""
        if not self.can_review(request.project_full_path):
            self._narrator.speak(
                f"{self._persona.honorific}, projeto {request.project_full_path} "
                f"não está configurado para revisão automática."
            )
            return

        if not self._busy_lock.acquire(blocking=False):
            self._narrator.speak(
                f"{self._persona.honorific}, já estou executando uma revisão. "
                f"Aguarde o término antes de iniciar outra."
            )
            return

        thread = threading.Thread(
            target=self._run, args=(request,), daemon=True, name="code-review"
        )
        thread.start()

    def _run(self, request: ReviewRequest) -> None:
        try:
            project = self._project_dirs[request.project_full_path]
            template = self._commands[project.type]
            command = [
                part.format(
                    source_branch=request.source_branch,
                    mr_url=request.mr_url,
                )
                for part in template
            ]

            print(
                f"[{self.name}] type={project.type} cwd={project.path}\n"
                f"              cmd={shlex.join(command)}"
            )
            self._narrator.speak(
                f"Iniciando revisão {self._review_label(project.type)} "
                f"da branch {request.source_branch}, {self._persona.honorific}."
            )

            if self.dry_run:
                print(f"[{self.name}] dry-run: comando não executado.")
                self._narrator.speak("Modo simulação ativo. Nenhuma revisão foi executada.")
                return

            result = subprocess.run(
                command,
                cwd=str(project.path),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )

            if result.returncode == 0:
                tail = result.stdout[-2000:]
                print(f"[{self.name}] concluída (rc=0).\n{tail}")
                self._narrator.speak(
                    f"Revisão concluída, {self._persona.honorific}."
                )
            else:
                stderr_tail = result.stderr[-1000:]
                print(f"[{self.name}] falhou (rc={result.returncode})\nstderr: {stderr_tail}")
                self._narrator.speak(
                    f"{self._persona.honorific}, a revisão falhou. "
                    f"Verifique o terminal."
                )
        except Exception as e:
            print(f"[{self.name}] erro inesperado: {e!r}")
            self._narrator.speak(
                f"{self._persona.honorific}, erro inesperado durante a revisão."
            )
        finally:
            self._busy_lock.release()

    @staticmethod
    def _review_label(review_type: str) -> str:
        labels = {"code": "de código", "docs": "de documentação"}
        return labels.get(review_type, "")


def project_configs_from_yaml(raw: dict) -> dict[str, ProjectConfig]:
    """Converte o bloco YAML de project_dirs em ProjectConfig tipados."""
    configs: dict[str, ProjectConfig] = {}
    for full_path, entry in (raw or {}).items():
        if isinstance(entry, str):
            # Forma curta: só o path → assume type=code
            configs[full_path] = ProjectConfig(path=Path(entry), type="code")
        elif isinstance(entry, dict):
            configs[full_path] = ProjectConfig(
                path=Path(entry["path"]),
                type=entry.get("type", "code"),
            )
    return configs
