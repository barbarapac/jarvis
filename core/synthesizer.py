"""Synthesizer — promove fatos das interações recentes pra arquivos curados.

Lê os últimos N dias de `interacoes/*.md`, lê o estado atual de `perfil/` e
`projetos/`, e pede ao Claude pra propor *patches* aos arquivos curados.

A síntese é **conservadora por padrão**:
- Só propõe atualização se houver fato claro, útil, com vida útil maior que
  a sessão (preferência persistente, decisão, fato novo sobre projeto/pessoa).
- Detalhes triviais (debugs, comandos de uma vez) NÃO viram memória.
- Máximo 5 propostas por chamada.

Cada proposta volta como dict:
    {
        "file": "perfil/barbara.md",     # path relativo ao vault root
        "action": "append" | "replace" | "create",
        "content": "...",                # markdown a escrever
        "rationale": "..."               # por que vale promover
    }

Aplicar/rejeitar fica a cargo do usuário via UI — esta classe NÃO escreve nada.
"""

from __future__ import annotations

import json
import re
from typing import Any

from core.llm_client import LLMClient, LLMError
from core.vault import Vault

MAX_TOKENS = 2048
TEMPERATURE = 0.2
DEFAULT_DAYS = 7
MAX_PROPOSALS = 5

_SYSTEM_PROMPT = """Você é o curador da memória persistente do Jarvis, um assistente \
pessoal local. Sua missão é ler interações recentes entre o Jarvis e a Senhora \
Bárbara, e propor atualizações **pequenas, específicas e conservadoras** aos \
arquivos curados do vault (perfil/, projetos/, decisoes/).

REGRAS DE OURO:
1. SEJA CONSERVADOR. Só proponha atualização se houver fato CLARO e ÚTIL pra \
ser lembrado em conversas futuras (preferência persistente, decisão tomada, \
contexto novo de projeto, pessoa relevante, restrição de uso).
2. Detalhes triviais NÃO viram memória: passos de debug, comandos pontuais, \
mensagens de erro, perguntas isoladas, ações que aconteceram uma vez sem \
recorrência.
3. Cada proposta tem rationale CURTO (1 frase) explicando por que vale promover.
4. Máximo 5 propostas. Em dúvida, prefira NÃO propor.
5. Use `append` para fato novo discreto em arquivo existente.
   Use `replace` para sobrescrever um arquivo inteiro (quando a estrutura mudou).
   Use `create` para arquivo novo (ex: perfil de pessoa nova, decisão importante).
6. Caminhos permitidos: `perfil/*.md`, `projetos/*.md`, `decisoes/YYYY-MM-DD-*.md`.
7. Conteúdo de `append` deve começar com cabeçalho markdown (## ou ###) e \
respeitar o tom existente do arquivo.

FORMATO DE SAÍDA — APENAS JSON, sem nenhum texto antes/depois:
{
  "proposals": [
    {
      "file": "perfil/barbara.md",
      "action": "append",
      "content": "## Preferência\\n\\nPrefere X porque Y.\\n",
      "rationale": "Mencionou X duas vezes nas interações de hoje."
    }
  ]
}

Se nada da memória recente merece promoção, responda exatamente:
{"proposals": []}
"""


class SynthesizerError(Exception):
    pass


class Synthesizer:
    def __init__(
        self,
        vault: Vault,
        llm_client: LLMClient,
        days: int = DEFAULT_DAYS,
        max_proposals: int = MAX_PROPOSALS,
    ) -> None:
        self._vault = vault
        self._llm = llm_client
        self._days = days
        self._max_proposals = max_proposals

    def synthesize(self) -> list[dict[str, Any]]:
        """Roda uma sintetização e retorna a lista de propostas validadas."""
        recent = self._vault.read_recent_interactions(days=self._days)
        if not recent:
            return []

        curated = self._vault.read_curated_files()

        user_msg = self._build_user_message(recent, curated)

        try:
            response = self._llm.complete(
                system=_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_msg}],
                max_tokens=MAX_TOKENS,
                temperature=TEMPERATURE,
            )
        except LLMError as e:
            raise SynthesizerError(f"falha na síntese: {e}") from e

        proposals = _parse_and_validate(response.text, max_proposals=self._max_proposals)
        return proposals

    # ---------- Internals ----------

    def _build_user_message(
        self,
        recent: list[dict[str, Any]],
        curated: dict[str, str],
    ) -> str:
        parts: list[str] = []

        parts.append("=== ARQUIVOS CURADOS ATUAIS ===\n")
        if curated:
            for rel, body in curated.items():
                parts.append(f"--- {rel} ---\n{body.strip()}\n")
        else:
            parts.append("(nenhum arquivo curado ainda)\n")

        parts.append(f"\n=== INTERAÇÕES DOS ÚLTIMOS {self._days} DIAS ===\n")
        if recent:
            for turn in recent:
                date = turn.get("date", "?")
                time = turn.get("time", "?")
                user = turn.get("user_text", "").strip()
                tools = turn.get("tools") or []
                assistant = turn.get("assistant_text", "").strip()
                parts.append(f"\n[{date} {time}]")
                parts.append(f"Bárbara: {user}")
                if tools:
                    parts.append(f"Tools: {', '.join(tools)}")
                parts.append(f"Jarvis: {assistant}")
        else:
            parts.append("(nenhuma interação recente)\n")

        parts.append(
            "\n=== TAREFA ===\n"
            "Analise as interações acima e proponha atualizações conservadoras "
            "aos arquivos curados, conforme as regras do system prompt. "
            "Responda APENAS com o JSON especificado."
        )

        return "\n".join(parts)


# ---------- Helpers ----------


_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def _parse_and_validate(text: str, *, max_proposals: int) -> list[dict[str, Any]]:
    """Extrai e valida o JSON da resposta. Lenient com fenced code blocks."""
    if not text.strip():
        return []

    # Tenta extrair de fenced code block primeiro; se falhar, parseia direto.
    candidate = text
    m = _JSON_FENCE_RE.search(text)
    if m:
        candidate = m.group(1)

    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError as e:
        raise SynthesizerError(f"resposta não é JSON válido: {e!r}\n--\n{text}")

    if not isinstance(parsed, dict) or "proposals" not in parsed:
        raise SynthesizerError(f"JSON sem chave 'proposals': {parsed!r}")

    raw_list = parsed["proposals"]
    if not isinstance(raw_list, list):
        raise SynthesizerError(f"'proposals' não é lista: {raw_list!r}")

    out: list[dict[str, Any]] = []
    for i, item in enumerate(raw_list[:max_proposals]):
        if not isinstance(item, dict):
            continue
        file = (item.get("file") or "").strip()
        action = (item.get("action") or "").strip().lower()
        content = item.get("content") or ""
        rationale = (item.get("rationale") or "").strip()

        if not file or action not in ("append", "replace", "create"):
            continue
        if not isinstance(content, str) or not content.strip():
            continue

        out.append(
            {
                "file": file,
                "action": action,
                "content": content,
                "rationale": rationale,
            }
        )

    return out
