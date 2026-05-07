"""Descobre skills e slash commands do Claude Code instalados localmente.

O Jarvis não as executa — só precisa saber que existem pra que, quando a
Senhora perguntar "que skills eu tenho?" ou "tem alguma skill pra X?", o
agente possa responder com nomes corretos em vez de inventar.

Indexa três fontes:
1. Skills pessoais — `~/.claude/skills/<name>/SKILL.md`.
2. Slash commands pessoais — `~/.claude/commands/**/*.md` (frontmatter
   define `name: grupo:cmd` quando existem subpastas tipo `softflow/`).
3. Skills de plugins instalados — lê `~/.claude/plugins/installed_plugins.json`
   e busca `skills/*/SKILL.md` em cada `installPath`.

Cada item vira um `SkillEntry` com name + description + source. A função
`format_for_prompt` produz uma linha curta (só nomes) pra injeção no
system prompt do agente — tokens controlados.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

# Frontmatter YAML simples: --- bloco --- no início do arquivo.
_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
# key: value — não suporta listas/dicts aninhados, mas frontmatter de
# skill/command é raso (name, description, argument-hint, allowed-tools).
_FIELD_RE = re.compile(r"^([a-zA-Z_-]+):\s*(.+?)\s*$", re.MULTILINE)

# Limite default pra string injetada no prompt. ~20 nomes de skill cabem
# folgados em 600 chars; passar disso, trunca pra não inflar tokens.
_DEFAULT_MAX_CHARS = 600


@dataclass(frozen=True)
class SkillEntry:
    name: str
    description: str
    source: str  # "user-skill" | "user-command" | "plugin:<plugin-id>"
    path: Path


def discover_skills(claude_home: Path | None = None) -> list[SkillEntry]:
    """Lê as três fontes e retorna lista ordenada por nome, sem duplicatas.

    Resolução de duplicatas: a primeira fonte vista vence (skills pessoais
    têm prioridade sobre commands; commands sobre plugins). Suficiente
    pra v1 — colisão de nome entre skill pessoal e command é rara.
    """
    home = claude_home or (Path.home() / ".claude")
    entries: list[SkillEntry] = []
    seen: set[str] = set()

    # 1) Skills pessoais.
    skills_dir = home / "skills"
    if skills_dir.is_dir():
        for skill_md in sorted(skills_dir.glob("*/SKILL.md")):
            entry = _parse_md(skill_md, source="user-skill", default_name=skill_md.parent.name)
            if entry and entry.name not in seen:
                entries.append(entry)
                seen.add(entry.name)

    # 2) Slash commands pessoais. Recursivo: ~/.claude/commands/softflow/dev.md
    #    → default name "softflow:dev" se o frontmatter não trouxer name.
    cmds_dir = home / "commands"
    if cmds_dir.is_dir():
        for cmd_md in sorted(cmds_dir.rglob("*.md")):
            rel = cmd_md.relative_to(cmds_dir).with_suffix("")
            default_name = ":".join(rel.parts)
            entry = _parse_md(cmd_md, source="user-command", default_name=default_name)
            if entry and entry.name not in seen:
                entries.append(entry)
                seen.add(entry.name)

    # 3) Skills de plugins instalados.
    for plugin_id, install_path in _load_installed_plugins(home):
        skills_root = install_path / "skills"
        if not skills_root.is_dir():
            continue
        for skill_md in sorted(skills_root.glob("*/SKILL.md")):
            entry = _parse_md(
                skill_md,
                source=f"plugin:{plugin_id}",
                default_name=skill_md.parent.name,
            )
            if entry and entry.name not in seen:
                entries.append(entry)
                seen.add(entry.name)

    entries.sort(key=lambda e: e.name)
    return entries


def format_for_prompt(
    entries: list[SkillEntry],
    disabled: set[str] | None = None,
    max_chars: int = _DEFAULT_MAX_CHARS,
) -> str:
    """Lista plana de nomes separada por vírgula, truncada se for longa.

    Skills cujo nome estiver em `disabled` são removidas da string —
    elas continuam descobertas mas o agente não fica sabendo delas.
    """
    if not entries:
        return ""
    disabled = disabled or set()
    names = [e.name for e in entries if e.name not in disabled]
    if not names:
        return ""
    joined = ", ".join(names)
    if len(joined) > max_chars:
        joined = joined[: max_chars - 3].rstrip(", ") + "..."
    return joined


def annotate(
    entries: list[SkillEntry],
    disabled: set[str] | None = None,
) -> list[dict]:
    """Serializa pra JSON (UI), anotando cada item com `enabled`."""
    disabled = disabled or set()
    out: list[dict] = []
    for e in entries:
        out.append(
            {
                "name": e.name,
                "description": e.description,
                "source": e.source,
                "enabled": e.name not in disabled,
            }
        )
    return out


# ---------- Internals ----------

def _parse_md(path: Path, source: str, default_name: str) -> SkillEntry | None:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return None
    fields: dict[str, str] = {}
    for field in _FIELD_RE.finditer(m.group(1)):
        # Strip aspas: frontmatter pode ter `name: "softflow:dev"`.
        value = field.group(2).strip().strip('"').strip("'")
        fields[field.group(1).lower()] = value
    name = fields.get("name") or default_name
    if not name:
        return None
    return SkillEntry(
        name=name,
        description=fields.get("description", ""),
        source=source,
        path=path,
    )


def _load_installed_plugins(home: Path) -> list[tuple[str, Path]]:
    """Devolve [(plugin_id, installPath)] dos plugins de fato instalados."""
    f = home / "plugins" / "installed_plugins.json"
    if not f.is_file():
        return []
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    out: list[tuple[str, Path]] = []
    for plugin_id, installs in (data.get("plugins") or {}).items():
        for inst in installs or []:
            install_path = inst.get("installPath")
            if install_path:
                out.append((plugin_id, Path(install_path)))
                break  # uma instalação por plugin basta
    return out
