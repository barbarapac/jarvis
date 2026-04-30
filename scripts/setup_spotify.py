"""First-run OAuth do Spotify.

Spawna o browser pra você autorizar o app, captura o callback no
http://127.0.0.1:8888/callback, e salva o token em
`.jarvis_state/spotify_cache`. Subsequent runs do main.py reusam o cache.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools.spotify import SpotifyTool


def main() -> int:
    load_dotenv(ROOT / ".env")
    client_id = os.environ.get("SPOTIFY_CLIENT_ID", "")
    client_secret = os.environ.get("SPOTIFY_CLIENT_SECRET", "")
    redirect_uri = os.environ.get("SPOTIFY_REDIRECT_URI", "http://127.0.0.1:8888/callback")

    if not (client_id and client_secret):
        print("SPOTIFY_CLIENT_ID e SPOTIFY_CLIENT_SECRET ausentes no .env", file=sys.stderr)
        return 1

    cache_path = ROOT / ".jarvis_state" / "spotify_cache"

    print("[setup_spotify] iniciando OAuth...")
    print("[setup_spotify] o browser deve abrir — autorize o app.")
    tool = SpotifyTool(
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=redirect_uri,
        cache_path=cache_path,
        open_browser=True,
    )
    tool.ensure_auth()
    me = tool._client.current_user()
    print(f"[setup_spotify] autorizado como {me.get('display_name')} ({me.get('id')})")
    print(f"[setup_spotify] token cacheado em {cache_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
