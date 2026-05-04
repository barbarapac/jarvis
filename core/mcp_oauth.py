"""OAuth 2.1 + PKCE pra MCPs HTTP/SSE (Atlassian, GitLab, Linear etc.).

Fluxo:
1. SDK MCP detecta `WWW-Authenticate` no primeiro request → chama o
   `OAuthClientProvider`.
2. Provider faz dynamic client registration no servidor (RFC 7591) e salva
   o `client_id` em disco.
3. Provider abre o browser pedindo autorização (redirect_handler).
4. Servidor de auth redireciona pra `http://127.0.0.1:9876/callback?code=...`.
5. Levantamos um HTTP server local one-shot que captura `code` e `state`.
6. Provider troca o code por tokens, salva em disco, e o request original
   é refeito com `Authorization: Bearer ...`.

Porta do callback é fixa (`9876`) — precisa ser estável entre execuções pra
que o redirect_uri registrado no servidor de auth continue válido. Como a
supervisor do MCPManager processa conexões em série, não há conflito.

Tokens e client info ficam em `.jarvis_state/mcp_oauth/<name>.json`.
"""

from __future__ import annotations

import asyncio
import http.server
import json
import socketserver
import threading
import urllib.parse
import webbrowser
from pathlib import Path

from mcp.client.auth import OAuthClientProvider, TokenStorage
from mcp.shared.auth import (
    OAuthClientInformationFull,
    OAuthClientMetadata,
    OAuthToken,
)

# Porta fixa do callback HTTP. Não pode mudar entre runs senão o
# `redirect_uri` registrado no servidor de auth deixa de bater.
CALLBACK_PORT = 9876
CALLBACK_PATH = "/callback"
CALLBACK_TIMEOUT_SECONDS = 300.0  # 5 min — gente real precisa de tempo no browser

CLIENT_NAME = "Jarvis (local)"
CLIENT_URI = "https://github.com/barbarapac/jarvis"


class JsonTokenStorage(TokenStorage):
    """Persiste tokens e client info em um único JSON por servidor MCP."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def _read(self) -> dict:
        if not self._path.exists():
            return {}
        try:
            return json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def _write(self, data: dict) -> None:
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self._path)

    async def get_tokens(self) -> OAuthToken | None:
        raw = self._read().get("tokens")
        if not raw:
            return None
        try:
            return OAuthToken.model_validate(raw)
        except Exception:
            return None

    async def set_tokens(self, tokens: OAuthToken) -> None:
        data = self._read()
        data["tokens"] = tokens.model_dump(mode="json", exclude_none=True)
        self._write(data)

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        raw = self._read().get("client_info")
        if not raw:
            return None
        try:
            return OAuthClientInformationFull.model_validate(raw)
        except Exception:
            return None

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        data = self._read()
        data["client_info"] = client_info.model_dump(mode="json", exclude_none=True)
        self._write(data)


class _CallbackServer:
    """HTTP server one-shot que captura o `code` do redirect OAuth.

    Usa thread daemon — é stdlib pura, sem aiohttp. A coroutine
    `wait_for_code()` espera o callback em executor, sem travar o loop.
    """

    def __init__(self, port: int = CALLBACK_PORT) -> None:
        self._port = port
        self._code: str | None = None
        self._state: str | None = None
        self._error: str | None = None
        self._done = threading.Event()
        self._server: socketserver.TCPServer | None = None

    def _build_handler(self):
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_args, **_kwargs):  # silencia stdout
                pass

            def do_GET(self):  # noqa: N802 (stdlib API)
                parsed = urllib.parse.urlparse(self.path)
                if parsed.path != CALLBACK_PATH:
                    self.send_response(404)
                    self.end_headers()
                    return
                params = urllib.parse.parse_qs(parsed.query)
                outer._code = params.get("code", [None])[0]
                outer._state = params.get("state", [None])[0]
                outer._error = params.get("error", [None])[0]
                if outer._error:
                    body = f"<h2>Falha de autorização</h2><p>{outer._error}</p>"
                else:
                    body = (
                        "<h2>Autorizado.</h2>"
                        "<p>O Jarvis já está conectando o MCP. Pode fechar essa aba.</p>"
                    )
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(body.encode("utf-8"))
                outer._done.set()

        return Handler

    def start(self) -> None:
        self._server = http.server.HTTPServer(("127.0.0.1", self._port), self._build_handler())
        threading.Thread(
            target=self._server.serve_forever,
            daemon=True,
            name="mcp-oauth-callback",
        ).start()

    def stop(self) -> None:
        if self._server is not None:
            try:
                self._server.shutdown()
                self._server.server_close()
            except Exception:
                pass
            self._server = None

    async def wait_for_code(self, timeout: float = CALLBACK_TIMEOUT_SECONDS) -> tuple[str, str | None]:
        loop = asyncio.get_running_loop()
        # `Event.wait` em executor — não trava o loop async.
        await loop.run_in_executor(None, self._done.wait, timeout)
        if self._error:
            raise RuntimeError(f"OAuth callback retornou erro: {self._error}")
        if not self._code:
            raise TimeoutError("OAuth callback não recebido dentro do timeout")
        return self._code, self._state


def make_oauth_provider(
    *,
    server_name: str,
    server_url: str,
    state_dir: Path,
    callback_port: int = CALLBACK_PORT,
) -> OAuthClientProvider:
    """Constrói um OAuthClientProvider pronto pra `streamablehttp_client(auth=...)`.

    Cada servidor MCP tem o próprio storage em `state_dir/<name>.json`. A
    primeira conexão dispara o flow inteiro (registration + browser); as
    seguintes reusam o token persistido.
    """
    storage_path = state_dir / f"{_safe_name(server_name)}.json"
    storage = JsonTokenStorage(storage_path)

    redirect_uri = f"http://127.0.0.1:{callback_port}{CALLBACK_PATH}"
    client_metadata = OAuthClientMetadata(
        redirect_uris=[redirect_uri],  # type: ignore[arg-type]
        token_endpoint_auth_method="client_secret_post",
        grant_types=["authorization_code", "refresh_token"],
        response_types=["code"],
        client_name=CLIENT_NAME,
        client_uri=CLIENT_URI,  # type: ignore[arg-type]
        scope=None,  # SDK negocia scope com o servidor automaticamente
    )

    callback_server = _CallbackServer(port=callback_port)

    async def redirect_handler(authorization_url: str) -> None:
        # Sobe o servidor de callback ANTES de abrir o browser pra não perder
        # o redirect (servidores de auth podem ser rápidos).
        callback_server.start()
        print(f"[mcp-oauth/{server_name}] abrindo browser para autorização...")
        opened = webbrowser.open(authorization_url, new=1, autoraise=True)
        if not opened:
            print(f"[mcp-oauth/{server_name}] não consegui abrir o browser. URL: {authorization_url}")

    async def callback_handler() -> tuple[str, str | None]:
        try:
            return await callback_server.wait_for_code()
        finally:
            callback_server.stop()

    return OAuthClientProvider(
        server_url=server_url,
        client_metadata=client_metadata,
        storage=storage,
        redirect_handler=redirect_handler,
        callback_handler=callback_handler,
    )


def _safe_name(name: str) -> str:
    """Sanitiza nome pra usar como filename (Windows-friendly)."""
    keep = "-_."
    return "".join(c if (c.isalnum() or c in keep) else "_" for c in name) or "server"
