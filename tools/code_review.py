"""Tool de review (código ou documentação): spawna `claude -p` em background.

Cada projeto pode ter um override em `project_dirs` (path local + type). Se
não tiver path, o WorkspaceManager faz shallow clone em `.jarvis_state/repos`.
Se não tiver type, default `code`.

Roda em thread separada e avisa via narrator.
"""

from __future__ import annotations

import shlex
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from core.narrator import SpeakingNarrator
from core.persona import Persona
from core.workspace import WorkspaceError, WorkspaceManager


@dataclass(frozen=True)
class ReviewRequest:
    project_full_path: str  # ex: "softplan/justica/projeto-x"
    source_branch: str
    mr_url: str
    mr_title: str


@dataclass(frozen=True)
class ProjectConfig:
    path: Optional[Path]  # None = usar workspace (auto-clone)
    type: str  # "code" | "docs" | qualquer chave em commands


class CodeReviewTool:
    name = "code_review"

    def __init__(
        self,
        project_dirs: dict[str, ProjectConfig],
        commands: dict[str, list[str]],
        narrator: SpeakingNarrator,
        persona: Persona,
        dry_run: bool = False,
        workspace: Optional[WorkspaceManager] = None,
    ) -> None:
        self._project_dirs = project_dirs
        self._commands = commands
        self._narrator = narrator
        self._persona = persona
        self.dry_run = dry_run
        self._workspace = workspace
        self._busy_lock = threading.Lock()

    def can_review(self, project_full_path: str) -> bool:
        """True se conseguimos resolver path (override OU workspace) e type tem comando."""
        override = self._project_dirs.get(project_full_path)
        review_type = (override.type if override else None) or "code"
        if review_type not in self._commands:
            return False
        if override and override.path:
            return True
        return self._workspace is not None

    def run_async(self, request: ReviewRequest) -> None:
        if not self.can_review(request.project_full_path):
            self._narrator.speak(
                f"{self._persona.honorific}, não posso revisar "
                f"{request.project_full_path} — sem path local nem auto-clone."
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
        cwd: Optional[Path] = None
        is_managed = False
        try:
            cwd, review_type, is_managed = self._resolve(request)
            template = self._commands[review_type]
            command = [
                part.format(
                    source_branch=request.source_branch,
                    mr_url=request.mr_url,
                )
                for part in template
            ]

            print(
                f"[{self.name}] type={review_type} cwd={cwd}\n"
                f"              cmd={shlex.join(command)}"
            )
            self._narrator.speak(
                f"Iniciando revisão {self._review_label(review_type)} "
                f"da branch {request.source_branch}, {self._persona.honorific}."
            )

            if self.dry_run:
                print(f"[{self.name}] dry-run: comando não executado.")
                self._narrator.speak("Modo simulação ativo. Nenhuma revisão foi executada.")
                return

            result = subprocess.run(
                command,
                cwd=str(cwd),
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
        except WorkspaceError as e:
            print(f"[{self.name}] falha de workspace: {e}")
            self._narrator.speak(
                f"{self._persona.honorific}, não consegui preparar o repositório."
            )
        except Exception as e:
            print(f"[{self.name}] erro inesperado: {e!r}")
            self._narrator.speak(
                f"{self._persona.honorific}, erro inesperado durante a revisão."
            )
        finally:
            if is_managed and self._workspace is not None:
                try:
                    self._workspace.cleanup(request.project_full_path)
                    print(f"[{self.name}] clone temporário removido.")
                except Exception as e:
                    print(f"[{self.name}] falha ao limpar clone: {e!r}")
            self._busy_lock.release()

    def _resolve(self, request: ReviewRequest) -> tuple[Path, str, bool]:
        """Returns (cwd, review_type, is_managed_by_workspace)."""
        override = self._project_dirs.get(request.project_full_path)
        review_type = (override.type if override else None) or "code"

        if override and override.path:
            if not override.path.is_dir():
                raise FileNotFoundError(
                    f"path configurado não existe: {override.path}"
                )
            return override.path, review_type, False

        if self._workspace is None:
            raise RuntimeError(
                f"projeto {request.project_full_path} sem path e sem auto-clone"
            )

        print(f"[{self.name}] preparando workspace para {request.project_full_path}@{request.source_branch}...")
        cwd = self._workspace.prepare(request.project_full_path, request.source_branch)
        return cwd, review_type, True

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
            path_str = entry.get("path")
            configs[full_path] = ProjectConfig(
                path=Path(path_str) if path_str else None,
                type=entry.get("type", "code"),
            )
    return configs
