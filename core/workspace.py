"""Gerencia clones locais efêmeros pra revisão automática.

Quando o usuário não tem o repo da MR clonado, o WorkspaceManager faz um
shallow clone em `.jarvis_state/repos/<project_full_path>/` e mantém aquilo
sincronizado em chamadas seguintes (fetch + checkout). É gitignored.

Auth: usa GITLAB_TOKEN como usuário oauth2 na URL — funciona pra repositórios
privados sem depender de Git Credential Manager.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from urllib.parse import urlparse

GIT_CLONE_DEPTH = 50  # commits de profundidade — suficiente pra diffs típicos


class WorkspaceError(RuntimeError):
    pass


class WorkspaceManager:
    def __init__(
        self,
        root: Path,
        gitlab_base_url: str,
        gitlab_token: str,
    ) -> None:
        self._root = root
        self._gitlab_base_url = gitlab_base_url
        self._gitlab_token = gitlab_token
        self._root.mkdir(parents=True, exist_ok=True)

    def prepare(self, project_full_path: str, source_branch: str) -> Path:
        """Garante um clone local com `source_branch` checked out."""
        local = self._root / project_full_path
        if local.is_dir() and (local / ".git").is_dir():
            self._update(local, source_branch)
        else:
            self._clone(local, project_full_path, source_branch)
        return local

    def _clone(self, local: Path, project_full_path: str, source_branch: str) -> None:
        local.parent.mkdir(parents=True, exist_ok=True)
        if local.exists():
            # Diretório existe mas não tem .git — limpa pra começar do zero.
            self._remove_dir(local)

        url = self._authenticated_url(project_full_path)
        cmd = [
            "git", "clone",
            "--depth", str(GIT_CLONE_DEPTH),
            "--no-single-branch",
            "-b", source_branch,
            url,
            str(local),
        ]
        self._run(cmd, label="clone", redact_token=True)

    def cleanup(self, project_full_path: str) -> None:
        """Remove o clone gerenciado. No-op se não existir."""
        local = self._root / project_full_path
        if local.is_dir():
            self._remove_dir(local)

    def _update(self, local: Path, source_branch: str) -> None:
        for cmd, label in [
            (["git", "-C", str(local), "fetch", "--depth", str(GIT_CLONE_DEPTH),
              "origin", source_branch], "fetch"),
            (["git", "-C", str(local), "checkout", source_branch], "checkout"),
            (["git", "-C", str(local), "reset", "--hard", f"origin/{source_branch}"], "reset"),
        ]:
            self._run(cmd, label=label)

    def _authenticated_url(self, project_full_path: str) -> str:
        parsed = urlparse(self._gitlab_base_url)
        host = parsed.netloc or parsed.path  # cobre formas com/sem schema
        return f"https://oauth2:{self._gitlab_token}@{host}/{project_full_path}.git"

    def _run(self, cmd: list[str], *, label: str, redact_token: bool = False) -> None:
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            stderr = result.stderr or "(empty)"
            if redact_token and self._gitlab_token:
                stderr = stderr.replace(self._gitlab_token, "***")
            raise WorkspaceError(f"git {label} falhou: {stderr.strip()}")

    @staticmethod
    def _remove_dir(path: Path) -> None:
        import shutil
        shutil.rmtree(path, ignore_errors=True)
