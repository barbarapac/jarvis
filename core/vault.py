"""Vault de markdown — cérebro persistente do Jarvis.

Lê arquivos curados (INDEX.md + perfil/*.md + projetos/*.md) no boot e
expõe esse conteúdo formatado para ser injetado no system prompt do agente.
Faz append em interacoes/YYYY-MM-DD.md após cada turno.

Estrutura esperada do vault:
    jarvis-vault/
    ├── INDEX.md                ← curado, lido sempre
    ├── perfil/*.md             ← curado, lido sempre
    ├── projetos/*.md           ← curado, lido sempre
    ├── decisoes/*.md           ← curado, NÃO lido por padrão (referência)
    └── interacoes/YYYY-MM-DD.md ← append-only, escrito pelo Jarvis

Decisões intencionais:
- `decisoes/` não é injetado por padrão — sobrecarrega o contexto e raramente
  muda decisão de turno-a-turno. O agente pode ser orientado a buscar lá quando
  precisar (futuro: tool `vault.read`).
- `interacoes/` também não é injetado em massa — só os últimos N turnos do
  dia atual entram via memória de sessão (ver `agent.py`).
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


CURATED_DIRS: tuple[str, ...] = ("perfil", "projetos")
KNOWLEDGE_DIR = "conhecimento"
GRAPH_DIRS: tuple[str, ...] = ("perfil", "projetos", "decisoes", KNOWLEDGE_DIR)
RECENT_DAYS_DEFAULT = 7
STATS_RECENT_DAYS = 7

# Detecta wikilinks [[X]] em conteúdo markdown. Não tenta resolver alvo.
_WIKILINK_RE = re.compile(r"\[\[[^\[\]\n]+?\]\]")
# Captura o "alvo" do wikilink — antes do | (alias) ou # (heading).
_WIKILINK_TARGET_RE = re.compile(r"\[\[([^\[\]\n|#]+?)(?:[#|][^\[\]\n]*)?\]\]")


class Vault:
    def __init__(self, root: Path) -> None:
        self._root = root
        self._root.mkdir(parents=True, exist_ok=True)
        for sub in (*CURATED_DIRS, "decisoes", "interacoes", KNOWLEDGE_DIR):
            (self._root / sub).mkdir(exist_ok=True)

    @property
    def root(self) -> Path:
        return self._root

    def build_context(self) -> str:
        """Retorna conteúdo concatenado de INDEX + perfil/* + projetos/* para
        injetar no system prompt. Vazio se o vault estiver vazio."""
        sections: list[str] = []

        index = self._root / "INDEX.md"
        if index.is_file():
            sections.append(_format_section("INDEX.md", index.read_text(encoding="utf-8")))

        for sub in CURATED_DIRS:
            for md in sorted((self._root / sub).glob("*.md")):
                sections.append(
                    _format_section(f"{sub}/{md.name}", md.read_text(encoding="utf-8"))
                )

        if not sections:
            return ""
        return "\n\n".join(sections)

    def record_interaction(
        self,
        *,
        user_text: str,
        assistant_text: str,
        tools_used: list[str] | None = None,
        now: datetime | None = None,
    ) -> Path:
        """Faz append em interacoes/YYYY-MM-DD.md. Retorna o path escrito."""
        now = now or datetime.now()
        target = self._root / "interacoes" / f"{now.strftime('%Y-%m-%d')}.md"

        # Header do arquivo na primeira escrita do dia.
        new_file = not target.exists()
        with target.open("a", encoding="utf-8") as f:
            if new_file:
                f.write(f"# Interações — {now.strftime('%Y-%m-%d')}\n\n")
            f.write(f"## {now.strftime('%H:%M:%S')}\n\n")
            f.write(f"**Bárbara:** {user_text.strip()}\n\n")
            if tools_used:
                f.write(f"**Tools:** {', '.join(tools_used)}\n\n")
            f.write(f"**Jarvis:** {assistant_text.strip()}\n\n")
            f.write("---\n\n")

        return target

    def read_today_interactions(self, now: datetime | None = None) -> str:
        """Retorna o conteúdo de interacoes/HOJE.md ou string vazia."""
        now = now or datetime.now()
        target = self._root / "interacoes" / f"{now.strftime('%Y-%m-%d')}.md"
        if not target.is_file():
            return ""
        return target.read_text(encoding="utf-8")

    def read_today_turns(self, now: datetime | None = None) -> list[dict[str, Any]]:
        """Parseia o markdown do dia em turnos estruturados.

        Cada turno tem `{time, user_text, tools, assistant_text}`.
        Retorna lista vazia se o arquivo não existe.
        """
        text = self.read_today_interactions(now=now)
        return _parse_interactions_md(text) if text else []

    def read_recent_interactions(
        self,
        days: int = RECENT_DAYS_DEFAULT,
        now: datetime | None = None,
    ) -> list[dict[str, Any]]:
        """Lê turnos dos últimos `days` dias (incluindo hoje), em ordem cronológica."""
        now = now or datetime.now()
        out: list[dict[str, Any]] = []
        for offset in range(days - 1, -1, -1):
            day = now - timedelta(days=offset)
            target = self._root / "interacoes" / f"{day.strftime('%Y-%m-%d')}.md"
            if not target.is_file():
                continue
            for turn in _parse_interactions_md(target.read_text(encoding="utf-8")):
                turn["date"] = day.strftime("%Y-%m-%d")
                out.append(turn)
        return out

    def read_curated_files(self) -> dict[str, str]:
        """Retorna {caminho_relativo: conteúdo} de todos os arquivos curados.

        Usado pelo synthesizer pra mostrar à LLM o estado atual dos perfis/projetos
        antes de propor patches.
        """
        out: dict[str, str] = {}
        for sub in CURATED_DIRS:
            for md in sorted((self._root / sub).glob("*.md")):
                rel = f"{sub}/{md.name}"
                out[rel] = md.read_text(encoding="utf-8")
        return out

    def ingest_knowledge(
        self,
        title: str,
        content: str,
        *,
        tags: list[str] | None = None,
        source: str | None = None,
        category: str = KNOWLEDGE_DIR,
        now: datetime | None = None,
    ) -> Path:
        """Salva uma nota em <category>/YYYY-MM-DD-slug.md.

        Conteúdo de `conhecimento/` NÃO entra em build_context() automaticamente
        — fica como material de leitura indexado pelo Obsidian. Já `perfil/` e
        `projetos/` SÃO injetados no system prompt do agente, então notas ali
        viram parte ativa do "cérebro" do Jarvis na próxima conversa.

        Levanta ValueError para título/conteúdo vazios ou categoria inválida.
        """
        title = (title or "").strip()
        content = (content or "").strip()
        if not title:
            raise ValueError("título obrigatório")
        if not content:
            raise ValueError("conteúdo obrigatório")

        category = (category or KNOWLEDGE_DIR).strip().lower()
        valid_categories = {*CURATED_DIRS, "decisoes", KNOWLEDGE_DIR}
        if category not in valid_categories:
            raise ValueError(f"categoria inválida: {category}")

        now = now or datetime.now()
        slug = _slugify(title) or "nota"
        target = self._root / category
        target.mkdir(parents=True, exist_ok=True)

        # Resolve colisão: se YYYY-MM-DD-slug.md já existe, sufixa -2, -3, ...
        base_name = f"{now.strftime('%Y-%m-%d')}-{slug}"
        path = target / f"{base_name}.md"
        suffix = 2
        while path.exists():
            path = target / f"{base_name}-{suffix}.md"
            suffix += 1

        clean_tags = [t.strip() for t in (tags or []) if t and t.strip()]
        front: list[str] = ["---", f"title: {_yaml_escape(title)}"]
        if clean_tags:
            front.append("tags: [" + ", ".join(_yaml_escape(t) for t in clean_tags) + "]")
        if source:
            front.append(f"source: {_yaml_escape(source.strip())}")
        front.append(f"ingested_at: {now.isoformat(timespec='seconds')}")
        front.append("---")

        body = f"# {title}\n\n{content}\n"
        path.write_text("\n".join(front) + "\n\n" + body, encoding="utf-8")
        return path

    def graph(self) -> dict[str, list[dict[str, Any]]]:
        """Constrói {nodes, edges} do vault baseado em wikilinks `[[X]]`.

        - Um node por arquivo .md em `GRAPH_DIRS`. id = caminho relativo.
        - Edges entre wikilinks que apontam pra notas existentes (basename
          sem extensão). Wikilinks pra notas inexistentes ("fantasmas") são
          ignorados — escolha consciente pra não poluir o grafo do MVP.
        - `size` é proporcional ao tamanho do arquivo (kb), pra dimensionar
          o nó visualmente.
        """
        # 1) Indexa todas as notas por basename pra resolver wikilinks rápido.
        files: list[tuple[str, str, Path]] = []  # (id, dir, path)
        by_basename: dict[str, str] = {}  # basename(sem .md) → id
        for sub in GRAPH_DIRS:
            for md in sorted((self._root / sub).glob("*.md")):
                rel_id = f"{sub}/{md.name}"
                files.append((rel_id, sub, md))
                # primeiro vence em caso de colisão de nome entre pastas
                by_basename.setdefault(md.stem, rel_id)
                by_basename.setdefault(md.stem.lower(), rel_id)

        nodes: list[dict[str, Any]] = []
        edges: list[dict[str, str]] = []
        seen_edges: set[tuple[str, str]] = set()

        for node_id, sub, path in files:
            try:
                content = path.read_text(encoding="utf-8")
                size_kb = max(1, path.stat().st_size // 1024)
            except OSError:
                content = ""
                size_kb = 1
            nodes.append({
                "id": node_id,
                "label": path.stem,
                "dir": sub,
                "size": size_kb,
            })
            for match in _WIKILINK_TARGET_RE.finditer(content):
                target_name = match.group(1).strip()
                if not target_name:
                    continue
                target_id = (
                    by_basename.get(target_name)
                    or by_basename.get(target_name.lower())
                )
                if not target_id or target_id == node_id:
                    continue
                key = (node_id, target_id)
                if key in seen_edges:
                    continue
                seen_edges.add(key)
                edges.append({"source": node_id, "target": target_id})

        return {"nodes": nodes, "edges": edges}

    def stats(self, now: datetime | None = None) -> dict[str, Any]:
        """Contadores rápidos para o painel Mega-Brain.

        Conta notas por pasta, wikilinks `[[X]]` em todos os .md, turnos
        de hoje + 7 dias e ingestões recentes (notas em `conhecimento/`
        cujo mtime cai em hoje/últimos 7 dias). Inclui também a última nota
        ingerida (mais recente em mtime) pra tornar o aprendizado tangível.
        """
        now = now or datetime.now()
        per_dir: dict[str, int] = {}
        wikilinks_total = 0
        for sub in (*CURATED_DIRS, "decisoes", KNOWLEDGE_DIR):
            count = 0
            for md in (self._root / sub).glob("*.md"):
                count += 1
                try:
                    wikilinks_total += len(_WIKILINK_RE.findall(md.read_text(encoding="utf-8")))
                except OSError:
                    pass
            per_dir[sub] = count
        notas_total = sum(per_dir.values())

        interacoes_hoje = len(self.read_today_turns(now=now))
        interacoes_7dias = len(
            self.read_recent_interactions(days=STATS_RECENT_DAYS, now=now)
        )

        # Ingestões: tudo em conhecimento/ — mtime decide hoje/7d.
        knowledge_dir = self._root / KNOWLEDGE_DIR
        ingestoes_hoje = 0
        ingestoes_7dias = 0
        last_ingest: dict[str, Any] | None = None
        if knowledge_dir.is_dir():
            today_start = datetime(now.year, now.month, now.day).timestamp()
            week_start = (now - timedelta(days=STATS_RECENT_DAYS)).timestamp()
            latest_mtime = 0.0
            for md in knowledge_dir.glob("*.md"):
                try:
                    mt = md.stat().st_mtime
                except OSError:
                    continue
                if mt >= today_start:
                    ingestoes_hoje += 1
                if mt >= week_start:
                    ingestoes_7dias += 1
                if mt > latest_mtime:
                    latest_mtime = mt
                    title = self._extract_title(md)
                    last_ingest = {
                        "title": title,
                        "rel_path": f"{KNOWLEDGE_DIR}/{md.name}",
                        "ingested_at": datetime.fromtimestamp(mt).isoformat(timespec="seconds"),
                    }

        return {
            "notas_total": notas_total,
            "notas_perfil": per_dir.get("perfil", 0),
            "notas_projetos": per_dir.get("projetos", 0),
            "notas_decisoes": per_dir.get("decisoes", 0),
            "notas_conhecimento": per_dir.get(KNOWLEDGE_DIR, 0),
            "wikilinks_total": wikilinks_total,
            "interacoes_hoje": interacoes_hoje,
            "interacoes_7dias": interacoes_7dias,
            "ingestoes_hoje": ingestoes_hoje,
            "ingestoes_7dias": ingestoes_7dias,
            "last_ingest": last_ingest,
        }

    @staticmethod
    def _extract_title(md: Path) -> str:
        # Tenta `title:` no frontmatter; cai pro primeiro `# heading`; depois pro nome do arquivo.
        try:
            text = md.read_text(encoding="utf-8")
        except OSError:
            return md.stem
        if text.startswith("---"):
            end = text.find("\n---", 3)
            if end > 0:
                for line in text[3:end].splitlines():
                    if line.startswith("title:"):
                        return line.split(":", 1)[1].strip().strip('"').strip("'")
        for line in text.splitlines():
            if line.startswith("# "):
                return line[2:].strip()
        return md.stem

    def write_curated_file(
        self,
        rel_path: str,
        content: str,
        *,
        mode: str = "create",
    ) -> Path:
        """Escreve em um arquivo curado, com validação de path.

        - rel_path deve estar em CURATED_DIRS (ex: 'perfil/x.md', 'projetos/y.md',
          'decisoes/2026-z.md').
        - mode='create' ou 'replace': sobrescreve.
        - mode='append': anexa ao final do arquivo (cria se não existir).

        Recusa path traversal, paths absolutos, paths fora dos diretórios permitidos.
        """
        if mode not in ("create", "replace", "append"):
            raise ValueError(f"mode inválido: {mode}")

        # Sanitiza path
        rel = Path(rel_path)
        if rel.is_absolute() or ".." in rel.parts:
            raise ValueError(f"path inseguro: {rel_path}")
        if not rel.parts or rel.parts[0] not in (*CURATED_DIRS, "decisoes"):
            raise ValueError(
                f"path fora dos diretórios curados ({CURATED_DIRS} | decisoes): {rel_path}"
            )
        if rel.suffix != ".md":
            raise ValueError(f"só arquivos .md são permitidos: {rel_path}")

        target = (self._root / rel).resolve()
        # Trava extra: path resolvido tem que estar dentro do root.
        try:
            target.relative_to(self._root.resolve())
        except ValueError:
            raise ValueError(f"path resolvido fora do vault: {rel_path}")

        target.parent.mkdir(parents=True, exist_ok=True)
        if mode == "append":
            with target.open("a", encoding="utf-8") as f:
                if target.exists() and target.stat().st_size > 0:
                    f.write("\n")
                f.write(content.rstrip() + "\n")
        else:
            target.write_text(content.rstrip() + "\n", encoding="utf-8")
        return target


# ---------- Parser de interactions markdown ----------

# Regex pra detectar headers de turno: "## HH:MM:SS"
_TIME_HEADER_RE = re.compile(r"^##\s+(\d{2}:\d{2}:\d{2})\s*$")
# Detecta linhas estruturadas do nosso formato.
_USER_LINE_RE = re.compile(r"^\*\*Bárbara:\*\*\s*(.*)$")
_TOOLS_LINE_RE = re.compile(r"^\*\*Tools:\*\*\s*(.*)$")
_ASSISTANT_LINE_RE = re.compile(r"^\*\*Jarvis:\*\*\s*(.*)$")


def _parse_interactions_md(text: str) -> list[dict[str, Any]]:
    """Parser frouxo: sai do estado vigente quando vê o próximo marcador.

    Aceita texto livre depois das linhas marcadas (até o próximo marcador),
    pra suportar respostas multi-linha do Jarvis.
    """
    turns: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    field: str | None = None  # qual campo está absorvendo linhas extras

    def flush_field(buf: list[str]) -> str:
        return "\n".join(buf).strip()

    buf: list[str] = []
    for line in text.splitlines():
        # Novo turno?
        m = _TIME_HEADER_RE.match(line)
        if m:
            if current is not None and field is not None:
                current[field] = (current.get(field, "") + "\n" + flush_field(buf)).strip()
            if current is not None:
                turns.append(current)
            current = {"time": m.group(1), "user_text": "", "tools": [], "assistant_text": ""}
            field = None
            buf = []
            continue
        if current is None:
            continue

        # Marcadores
        m = _USER_LINE_RE.match(line)
        if m:
            if field is not None:
                current[field] = (current.get(field, "") + "\n" + flush_field(buf)).strip()
            field = "user_text"
            buf = [m.group(1)]
            continue
        m = _TOOLS_LINE_RE.match(line)
        if m:
            if field is not None:
                current[field] = (current.get(field, "") + "\n" + flush_field(buf)).strip()
            current["tools"] = [t.strip() for t in m.group(1).split(",") if t.strip()]
            field = None
            buf = []
            continue
        m = _ASSISTANT_LINE_RE.match(line)
        if m:
            if field is not None:
                current[field] = (current.get(field, "") + "\n" + flush_field(buf)).strip()
            field = "assistant_text"
            buf = [m.group(1)]
            continue

        # Separador "---" → fim do turno atual
        if line.strip() == "---":
            if field is not None:
                current[field] = (current.get(field, "") + "\n" + flush_field(buf)).strip()
            turns.append(current)
            current = None
            field = None
            buf = []
            continue

        # Linha solta — acumula no campo corrente, se houver
        if field is not None:
            buf.append(line)

    # Fecha turno em aberto (sem ---)
    if current is not None:
        if field is not None:
            current[field] = (current.get(field, "") + "\n" + flush_field(buf)).strip()
        turns.append(current)

    return turns


def _format_section(label: str, body: str) -> str:
    """Envelope para deixar claro pro agente de qual arquivo veio cada bloco."""
    return f"### Vault: {label}\n\n{body.strip()}"


# Slugify simples: ASCII fold via NFKD, mantém [a-z0-9-], colapsa hifens.
import unicodedata as _ud

_SLUG_INVALID_RE = re.compile(r"[^a-z0-9]+")


def _slugify(text: str, max_len: int = 60) -> str:
    folded = _ud.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    slug = _SLUG_INVALID_RE.sub("-", folded.lower()).strip("-")
    return slug[:max_len].rstrip("-")


def _yaml_escape(value: str) -> str:
    """Quota strings YAML quando podem confundir o parser. Conservador."""
    if not value:
        return '""'
    needs_quote = any(c in value for c in (":", "#", "[", "]", "{", "}", ",", "\n", "\"", "'"))
    if not needs_quote and not value.strip().lower() in ("yes", "no", "true", "false", "null", "~"):
        return value
    escaped = value.replace("\\", "\\\\").replace("\"", "\\\"")
    return f"\"{escaped}\""
