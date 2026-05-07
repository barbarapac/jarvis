"""Integração com Claude Code (CLI). Abre uma janela interativa do Claude Code
num projeto específico, com um comando slash (skill) já pré-preenchido pra
execução.

Skills/slash commands são interativas e precisam rodar dentro de um diretório
que tenha as definições instaladas (geralmente `.claude/commands/<plugin>/`).
Por isso a tool exige um `project` — chave amigável mapeada pra diretório no
config (`tools.claude_code.projects`). Se a Senhora pedir uma skill sem dizer
em qual projeto rodar, o agent pergunta antes de abrir nada.

Estratégia de spawn: delega pra `core.terminal.spawn_terminal`, que respeita
a preferência da Senhora em `terminal.preferred` (wt, cmd, powershell, warp,
ou auto). Pra Warp, o terminal abre só no diretório (limitação do URI scheme)
e devolvemos o slash como instrução manual pra colar.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from core.terminal import spawn_terminal

# Env vars que pertencem ao cérebro do Jarvis e não podem vazar pro `claude`
# CLI — senão o CLI dispara "Auth conflict" entre o token claude.ai (login web
# da Senhora) e a API key direta da Anthropic (que é só pro backend).
_BRAIN_ONLY_ENV_VARS = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")


def _spawn_env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if k not in _BRAIN_ONLY_ENV_VARS}


@dataclass
class ClaudeProject:
    name: str
    path: Path


class ClaudeCodeTool:
    def __init__(
        self,
        projects_provider: Callable[[], dict[str, str | Path]],
        terminal_provider: Callable[[], str] | None = None,
    ) -> None:
        # Provider em vez de dict fixo: mudanças via UI/config refletem na
        # hora sem reinicializar o agente.
        self._projects_provider = projects_provider
        # `terminal_provider` lê `terminal.preferred` a cada chamada — assim
        # trocas pela UI valem na próxima abertura sem restart.
        self._terminal_provider = terminal_provider or (lambda: "auto")

    def _current_projects(self) -> dict[str, ClaudeProject]:
        try:
            raw = self._projects_provider() or {}
        except Exception as e:
            print(f"[claude_code] projects_provider falhou: {e!r}")
            return {}
        return {
            name: ClaudeProject(name=name, path=Path(str(p)).expanduser())
            for name, p in raw.items()
        }

    def list_projects(self) -> list[dict[str, Any]]:
        return [
            {
                "name": p.name,
                "path": str(p.path),
                "exists": p.path.is_dir(),
            }
            for p in self._current_projects().values()
        ]

    def open_skill(
        self,
        skill: str,
        args: str = "",
        project: str | None = None,
    ) -> str:
        skill = (skill or "").strip().lstrip("/")
        if not skill:
            return "[claude_code] skill obrigatória"

        projects = self._current_projects()
        chosen = self._resolve_project(projects, project)
        if chosen is None:
            opts = sorted(projects.keys())
            if not opts:
                return (
                    "[claude_code] nenhum projeto cadastrado em "
                    "tools.claude_code.projects no config. Pergunte à Senhora "
                    "qual diretório usar e peça pra ela cadastrar."
                )
            return (
                f"[claude_code] preciso saber em qual projeto rodar /{skill}. "
                f"Opções cadastradas: {', '.join(opts)}. Pergunte à Senhora "
                f"qual usar e chame de novo passando project=<nome>."
            )

        if not chosen.path.is_dir():
            return (
                f"[claude_code] diretório do projeto {chosen.name!r} "
                f"não existe: {chosen.path}"
            )

        slash = f"/{skill}"
        if args:
            slash = f"{slash} {args.strip()}"

        try:
            result = spawn_terminal(
                preferred=self._terminal_provider(),
                cwd=chosen.path,
                initial_command=["claude", slash],
                env=_spawn_env(),
            )
        except Exception as e:
            return f"[claude_code] falha abrindo terminal: {e!r}"

        if result.manual_instruction:
            # Warp não pré-preenche o comando — devolve a instrução pra colar.
            return (
                f"[claude_code] abri o Claude Code em {chosen.name} "
                f"({chosen.path}) usando {result.terminal_used}. "
                f"O Warp não suporta pré-preencher comando — "
                f"cole no terminal: {result.manual_instruction}"
            )
        return (
            f"[claude_code] abri o Claude Code em {chosen.name} "
            f"({chosen.path}) com {slash} pré-preenchido "
            f"(terminal: {result.terminal_used})."
        )

    def _resolve_project(
        self,
        projects: dict[str, ClaudeProject],
        project: str | None,
    ) -> ClaudeProject | None:
        if project:
            return projects.get(project)
        if len(projects) == 1:
            return next(iter(projects.values()))
        return None


def build_claude_code_anthropic_tools(_tool: ClaudeCodeTool) -> list[dict[str, Any]]:
    return [
        {
            "name": "claude_code_list_projects",
            "description": (
                "Lista projetos do Claude Code cadastrados no Jarvis (chave "
                "amigável + diretório). Use antes de claude_code_open_skill "
                "se não souber em qual projeto rodar a skill."
            ),
            "input_schema": {"type": "object", "properties": {}},
        },
        {
            "name": "claude_code_open_skill",
            "description": (
                "Abre uma janela do Claude Code (CLI) num projeto específico "
                "com um comando slash (skill) pré-preenchido pra execução. "
                "Use quando a Senhora pedir pra rodar uma skill do Claude "
                "Code, ex: 'rode /softflow:concept na MPC-158' → "
                "skill='softflow:concept', args='MPC-158'. Se não souber em "
                "qual projeto rodar, primeiro chame "
                "claude_code_list_projects e/ou pergunte à Senhora."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "skill": {
                        "type": "string",
                        "description": (
                            "Nome do comando slash sem a barra inicial. "
                            "Ex: 'softflow:concept', 'review', 'hp:dev'."
                        ),
                    },
                    "args": {
                        "type": "string",
                        "description": (
                            "Argumentos do comando slash. Ex: ID ou URL da "
                            "issue ('MPC-158'). Opcional."
                        ),
                    },
                    "project": {
                        "type": "string",
                        "description": (
                            "Chave do projeto cadastrado (ex: 'softflow', "
                            "'jarvis'). Omita se quiser que a tool resolva "
                            "automaticamente — ela pede escolha quando há "
                            "ambiguidade."
                        ),
                    },
                },
                "required": ["skill"],
            },
        },
    ]


def build_claude_code_caller(
    tool: ClaudeCodeTool,
) -> Callable[[str, dict[str, Any]], str | None]:
    def call(name: str, args: dict[str, Any]) -> str | None:
        if name == "claude_code_list_projects":
            projects = tool.list_projects()
            return json.dumps(projects, ensure_ascii=False) if projects else "[]"
        if name == "claude_code_open_skill":
            return tool.open_skill(
                skill=str(args.get("skill") or ""),
                args=str(args.get("args") or ""),
                project=(args.get("project") or None),
            )
        return None

    return call
