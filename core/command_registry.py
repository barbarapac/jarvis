"""Catálogo de tools/actions que podem ser ligadas a comandos por voz.

Cada `Action` tem:
- `name`: identificador (ex. `play_playlist`)
- `description`: rótulo human-readable pra UI
- `params`: schema de parâmetros que a usuária precisa preencher
- `handler`: função `(tool_instance, params, ctx)` que executa e retorna a fala

Cada `Tool` agrupa actions sob um nome (ex. `spotify`). O VoiceCommander
recebe um dict {tool_name: Tool} montado em runtime, contendo só as tools
que estão ativas (Spotify autenticado, etc.).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional


@dataclass(frozen=True)
class ParamSchema:
    name: str
    type: str = "string"  # "string" | "number" | "boolean"
    required: bool = True
    placeholder: str = ""
    label: str = ""


@dataclass(frozen=True)
class Action:
    name: str
    label: str
    params: tuple[ParamSchema, ...]
    handler: Callable[[Any, dict, dict], Optional[str]]


@dataclass
class Tool:
    name: str
    label: str
    actions: dict[str, Action] = field(default_factory=dict)

    def get(self, action_name: str) -> Optional[Action]:
        return self.actions.get(action_name)


def build_spotify_tool(spotify) -> Tool:
    """Cria a Tool 'spotify' bindada à instância passada."""

    def _pause(_params, _ctx):
        spotify.pause()
        return "Pausado."

    def _resume(_params, _ctx):
        spotify.resume()
        return "Retomando."

    def _next(_params, _ctx):
        spotify.next_track()
        return "Próxima."

    def _previous(_params, _ctx):
        spotify.previous_track()
        return "Anterior."

    def _current(_params, ctx):
        track = spotify.current_track()
        honorific = ctx.get("honorific", "")
        if track:
            return f"{track}, {honorific}." if honorific else f"{track}."
        return f"Nada tocando, {honorific}." if honorific else "Nada tocando."

    def _play_playlist(params, _ctx):
        playlist = (params.get("playlist") or "").strip()
        if not playlist:
            raise ValueError("parâmetro 'playlist' é obrigatório")
        found = spotify.play_playlist(playlist)
        return f"Tocando {found}."

    def _play_track(params, _ctx):
        ref = (params.get("track") or "").strip()
        if not ref:
            raise ValueError("parâmetro 'track' é obrigatório")
        found = spotify.play_track(ref)
        return f"Tocando {found}."

    def _play_album(params, _ctx):
        ref = (params.get("album") or "").strip()
        if not ref:
            raise ValueError("parâmetro 'album' é obrigatório")
        found = spotify.play_album(ref)
        return f"Tocando {found}."

    actions = {
        "pause": Action(
            name="pause",
            label="Pausar música",
            params=(),
            handler=lambda tool, p, ctx: _pause(p, ctx),
        ),
        "resume": Action(
            name="resume",
            label="Retomar música",
            params=(),
            handler=lambda tool, p, ctx: _resume(p, ctx),
        ),
        "next": Action(
            name="next",
            label="Próxima música",
            params=(),
            handler=lambda tool, p, ctx: _next(p, ctx),
        ),
        "previous": Action(
            name="previous",
            label="Música anterior",
            params=(),
            handler=lambda tool, p, ctx: _previous(p, ctx),
        ),
        "current": Action(
            name="current",
            label="O que está tocando",
            params=(),
            handler=lambda tool, p, ctx: _current(p, ctx),
        ),
        "play_playlist": Action(
            name="play_playlist",
            label="Tocar playlist",
            params=(
                ParamSchema(
                    name="playlist",
                    type="string",
                    required=True,
                    placeholder="ex: Deep Focus",
                    label="Nome da playlist",
                ),
            ),
            handler=lambda tool, p, ctx: _play_playlist(p, ctx),
        ),
        "play_track": Action(
            name="play_track",
            label="Tocar música (URI)",
            params=(
                ParamSchema(
                    name="track",
                    type="string",
                    required=True,
                    placeholder="spotify:track:... ou link open.spotify.com",
                    label="URI/URL da música",
                ),
            ),
            handler=lambda tool, p, ctx: _play_track(p, ctx),
        ),
        "play_album": Action(
            name="play_album",
            label="Tocar álbum (URI)",
            params=(
                ParamSchema(
                    name="album",
                    type="string",
                    required=True,
                    placeholder="spotify:album:... ou link open.spotify.com",
                    label="URI/URL do álbum",
                ),
            ),
            handler=lambda tool, p, ctx: _play_album(p, ctx),
        ),
    }
    return Tool(name="spotify", label="Spotify", actions=actions)


def serialize_catalog(tools: dict[str, Tool]) -> list[dict]:
    """Forma JSON-friendly do catálogo, pra UI consumir via /api/commands/catalog."""
    return [
        {
            "name": tool.name,
            "label": tool.label,
            "actions": [
                {
                    "name": action.name,
                    "label": action.label,
                    "params": [
                        {
                            "name": p.name,
                            "type": p.type,
                            "required": p.required,
                            "placeholder": p.placeholder,
                            "label": p.label or p.name,
                        }
                        for p in action.params
                    ],
                }
                for action in tool.actions.values()
            ],
        }
        for tool in tools.values()
    ]
