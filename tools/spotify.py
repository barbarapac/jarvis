"""Tool de controle do Spotify via Web API (spotipy).

Auth: OAuth Authorization Code com client_id/secret. Token cacheado em
`.jarvis_state/spotify_cache`. Primeiro uso: spawna browser pra autorizar.

Operações: pause, resume, next_track, previous_track, play_playlist(query),
current_track. Todas precisam de um device ATIVO (Spotify aberto em algum
lugar — desktop, web, mobile).
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import spotipy
from spotipy.oauth2 import SpotifyOAuth

# Scopes necessários pras operações de playback + leitura de playlists.
SCOPE = " ".join([
    "user-modify-playback-state",
    "user-read-playback-state",
    "user-read-currently-playing",
    "playlist-read-private",
    "playlist-read-collaborative",
])


class SpotifyError(RuntimeError):
    pass


class SpotifyTool:
    name = "spotify"

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
        cache_path: Path,
        open_browser: bool = True,
    ) -> None:
        if not (client_id and client_secret):
            raise ValueError("SPOTIFY_CLIENT_ID e SPOTIFY_CLIENT_SECRET são obrigatórios")
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        self._auth = SpotifyOAuth(
            client_id=client_id,
            client_secret=client_secret,
            redirect_uri=redirect_uri,
            scope=SCOPE,
            cache_path=str(cache_path),
            open_browser=open_browser,
        )
        self._client = spotipy.Spotify(auth_manager=self._auth)

    def ensure_auth(self) -> None:
        """Força resolução do token. Spawna browser se necessário (primeiro run)."""
        self._client.current_user()

    def pause(self) -> None:
        device = self._active_device_id()
        self._client.pause_playback(device_id=device)

    def resume(self) -> None:
        device = self._active_device_id()
        self._client.start_playback(device_id=device)

    def next_track(self) -> None:
        device = self._active_device_id()
        self._client.next_track(device_id=device)

    def previous_track(self) -> None:
        device = self._active_device_id()
        self._client.previous_track(device_id=device)

    def play_playlist(self, query: str) -> str:
        """Procura playlist por nome e toca. Retorna o nome da playlist achada."""
        playlist_uri, name = self._find_playlist(query)
        device = self._active_device_id()
        self._client.start_playback(device_id=device, context_uri=playlist_uri)
        return name

    def current_track(self) -> Optional[str]:
        """Retorna 'Música - Artista' ou None se nada tocando."""
        playing = self._client.current_playback()
        if not playing or not playing.get("item"):
            return None
        item = playing["item"]
        artists = ", ".join(a["name"] for a in item.get("artists", []))
        return f"{item['name']} de {artists}" if artists else item["name"]

    def _active_device_id(self) -> Optional[str]:
        """Pega o device ativo. Se nada ativo, pega o primeiro disponível."""
        devices = self._client.devices().get("devices", [])
        if not devices:
            raise SpotifyError("nenhum device Spotify disponível — abra o Spotify primeiro")
        active = next((d for d in devices if d.get("is_active")), None)
        return (active or devices[0])["id"]

    def _find_playlist(self, query: str) -> tuple[str, str]:
        """Busca nas playlists do usuário primeiro; depois no catálogo público."""
        query_lower = query.lower().strip()

        # 1) playlists do usuário
        offset = 0
        while True:
            page = self._client.current_user_playlists(limit=50, offset=offset)
            for pl in page.get("items", []):
                if query_lower in (pl.get("name") or "").lower():
                    return pl["uri"], pl["name"]
            if not page.get("next"):
                break
            offset += 50

        # 2) busca pública
        results = self._client.search(q=query, type="playlist", limit=1)
        items = (results.get("playlists") or {}).get("items") or []
        if items:
            pl = items[0]
            return pl["uri"], pl["name"]

        raise SpotifyError(f"nenhuma playlist encontrada para {query!r}")
