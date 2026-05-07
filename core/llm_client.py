"""Abstração de cliente LLM — Ollama (local) ou Anthropic (cloud).

Interface única que esconde diferenças entre os dois providers, usando
formato OpenAI-like internamente (mensagens com role + content + opcional
tool_calls). O agente fala "OpenAI", o cliente concreto traduz pra cá ou
lá conforme o config.

Por que essa abstração existe:
- Llama 3.2 3B via Ollama é grátis e privado, mas tool calling é limitado;
  precisamos degradar gracioso quando o modelo não respeita o schema.
- Anthropic ainda é a opção mais robusta pra tool use; não queremos
  amputar a possibilidade de voltar pra ela só porque migramos.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

DEFAULT_TIMEOUT_SECONDS = 120
OLLAMA_DEFAULT_BASE = "http://localhost:11434/v1"


@dataclass
class ToolCall:
    """Solicitação do modelo pra rodar uma tool."""

    id: str
    name: str
    input: dict[str, Any]


@dataclass
class LLMResponse:
    """Resposta normalizada — independente do provider."""

    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    # "end_turn" → modelo terminou. "tool_use" → quer rodar tools.
    stop_reason: str = "end_turn"
    raw: Any = None  # último response cru, útil pra debug


class LLMClient(Protocol):
    """Cliente síncrono. Async fica no caller (asyncio.to_thread)."""

    def complete(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.2,
    ) -> LLMResponse: ...


# ---------------------------------------------------------------------------
# Ollama (OpenAI-compatible em /v1/chat/completions)
# ---------------------------------------------------------------------------


class OllamaClient:
    def __init__(
        self,
        *,
        model: str,
        base_url: str = OLLAMA_DEFAULT_BASE,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._client = httpx.Client(timeout=timeout)

    def complete(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.2,
    ) -> LLMResponse:
        body: dict[str, Any] = {
            "model": self._model,
            "messages": _prepend_system(system, messages),
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": False,
        }
        if tools:
            body["tools"] = tools

        try:
            r = self._client.post(f"{self._base_url}/chat/completions", json=body)
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise LLMError(f"Ollama HTTP falhou: {e}") from e

        data = r.json()
        choice = (data.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        text = (msg.get("content") or "").strip()
        finish = choice.get("finish_reason") or "stop"

        tool_calls: list[ToolCall] = []
        for tc in msg.get("tool_calls") or []:
            fn = tc.get("function") or {}
            args_raw = fn.get("arguments") or "{}"
            try:
                args = json.loads(args_raw) if isinstance(args_raw, str) else dict(args_raw)
            except json.JSONDecodeError:
                args = {}
            tool_calls.append(ToolCall(id=tc.get("id") or "", name=fn.get("name") or "", input=args))

        stop = "tool_use" if tool_calls else "end_turn"
        return LLMResponse(text=text, tool_calls=tool_calls, stop_reason=stop, raw=data)


# ---------------------------------------------------------------------------
# Anthropic (SDK oficial, content blocks)
# ---------------------------------------------------------------------------


class AnthropicClient:
    """Cliente Anthropic com prompt caching automático.

    System prompt e tools são marcados com cache_control=ephemeral. Isso
    cacheia esses blocos por ~5min no servidor da Anthropic; turnos
    seguintes pagam input cacheado (90% mais barato). Faz diferença
    grande pro Jarvis porque o vault context (perfil/projetos) entra no
    system todo turno.
    """

    def __init__(
        self,
        *,
        model: str,
        api_key: str | None = None,
        enable_cache: bool = True,
    ) -> None:
        from anthropic import Anthropic  # late import — só requer pacote se usar

        key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        if not key:
            raise ValueError("ANTHROPIC_API_KEY ausente — provider anthropic indisponível.")
        self._client = Anthropic(api_key=key)
        self._model = model
        self._enable_cache = enable_cache

    def complete(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.2,
    ) -> LLMResponse:
        anthropic_messages = _to_anthropic_messages(messages)

        # System em formato de blocks com cache_control no último bloco. A
        # Anthropic cacheia a partir do ponto marcado, então passar uma única
        # entrada cobre o prompt todo.
        system_blocks: list[dict[str, Any]] = [
            {"type": "text", "text": system or ""}
        ]
        if self._enable_cache and system:
            system_blocks[-1]["cache_control"] = {"type": "ephemeral"}

        kwargs: dict[str, Any] = {
            "model": self._model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "system": system_blocks,
            "messages": anthropic_messages,
        }
        if tools:
            anthropic_tools = [_to_anthropic_tool(t) for t in tools]
            # Cache na última tool cobre toda a lista (regra da Anthropic).
            if self._enable_cache and anthropic_tools:
                anthropic_tools[-1] = {
                    **anthropic_tools[-1],
                    "cache_control": {"type": "ephemeral"},
                }
            kwargs["tools"] = anthropic_tools

        try:
            response = self._client.messages.create(**kwargs)
        except Exception as e:
            raise LLMError(f"Anthropic falhou: {e}") from e

        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        for b in response.content or []:
            kind = getattr(b, "type", None)
            if kind == "text":
                text_parts.append(getattr(b, "text", ""))
            elif kind == "tool_use":
                tool_calls.append(
                    ToolCall(
                        id=getattr(b, "id", ""),
                        name=getattr(b, "name", ""),
                        input=getattr(b, "input", {}) or {},
                    )
                )

        stop = "tool_use" if response.stop_reason == "tool_use" else "end_turn"
        return LLMResponse(
            text="\n".join(text_parts).strip(),
            tool_calls=tool_calls,
            stop_reason=stop,
            raw=response,
        )


class LLMError(Exception):
    pass


# ---------------------------------------------------------------------------
# Factory + helpers
# ---------------------------------------------------------------------------


def make_client(
    *,
    provider: str,
    model: str,
    api_key: str | None = None,
    base_url: str | None = None,
) -> LLMClient:
    """Resolve config → cliente. Falha cedo se inválido."""
    p = (provider or "ollama").strip().lower()
    if p == "ollama":
        return OllamaClient(model=model, base_url=base_url or OLLAMA_DEFAULT_BASE)
    if p == "anthropic":
        return AnthropicClient(model=model, api_key=api_key)
    raise ValueError(f"provider desconhecido: {provider}")


def _prepend_system(system: str, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """OpenAI-like espera system como primeira mensagem, não como param."""
    out: list[dict[str, Any]] = []
    if system:
        out.append({"role": "system", "content": system})
    for m in messages:
        out.append(m)
    return out


def _to_anthropic_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Converte lista de mensagens OpenAI-like → Anthropic.

    Diferenças importantes do formato Anthropic:
    - role="tool" (com tool_call_id) vira role="user" com content=[tool_result].
    - Tool calls do assistant ficam embutidas como blocks no content em vez
      de array separado.
    - Múltiplos tool_results de um único turno de tool_use DEVEM ser
      empacotados num único user message com múltiplos content blocks. Se
      forem enviados em messages user separados, a API rejeita com
      "tool_use ids were found without tool_result blocks immediately after".
      Por isso colapsamos role="tool" consecutivos.
    """
    out: list[dict[str, Any]] = []
    pending_tool_results: list[dict[str, Any]] = []

    def flush_tool_results() -> None:
        if pending_tool_results:
            out.append({"role": "user", "content": list(pending_tool_results)})
            pending_tool_results.clear()

    for m in messages:
        role = m.get("role")
        if role == "tool":
            pending_tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": m.get("tool_call_id") or "",
                    "content": m.get("content") or "",
                }
            )
            continue
        flush_tool_results()
        if role == "assistant":
            blocks: list[dict[str, Any]] = []
            text = m.get("content") or ""
            if text:
                blocks.append({"type": "text", "text": text})
            for tc in m.get("tool_calls") or []:
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": tc.get("id"),
                        "name": tc.get("name"),
                        "input": tc.get("input") or {},
                    }
                )
            out.append({"role": "assistant", "content": blocks or text})
        else:
            out.append({"role": role or "user", "content": m.get("content") or ""})
    flush_tool_results()
    return out


def _to_anthropic_tool(t: dict[str, Any]) -> dict[str, Any]:
    """OpenAI: {type: function, function: {name, description, parameters}}
    Anthropic: {name, description, input_schema}."""
    fn = t.get("function") or t
    return {
        "name": fn.get("name") or "",
        "description": fn.get("description") or "",
        "input_schema": fn.get("parameters") or fn.get("input_schema") or {"type": "object", "properties": {}},
    }
