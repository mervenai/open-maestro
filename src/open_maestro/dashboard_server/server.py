"""Standalone HTTP receiver and viewer for published dashboards."""

from __future__ import annotations

import json
import logging
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from threading import Thread
from typing import Any
from urllib.parse import unquote

from open_maestro.dashboard_server.renderer import (
    render_html,
    render_json,
    render_markdown,
)
from open_maestro.dashboard_server.store import DashboardStore

logger = logging.getLogger(__name__)

DEFAULT_PUBLISH_PATH = "/maestro/dashboard"


class _RemoteDashboardHandler(BaseHTTPRequestHandler):
    """HTTP handler for the standalone dashboard receiver."""

    store: DashboardStore = DashboardStore()
    api_key: str | None = None
    publish_path: str = DEFAULT_PUBLISH_PATH

    def _send(self, status: int, content_type: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length", "0"))
        return self.rfile.read(length) if length > 0 else b""

    def _authorized(self) -> bool:
        if not self.api_key:
            return True
        header = self.headers.get("Authorization", "")
        if header.startswith("Bearer ") and header[7:] == self.api_key:
            return True
        return header == self.api_key

    def _token_from_path(self) -> str | None:
        """Extract project token from GET paths like /maestro/dashboard/<token>/html."""
        path = unquote(self.path.split("?")[0])
        prefix = self.publish_path.rstrip("/") + "/"
        if not path.startswith(prefix):
            return None
        remainder = path[len(prefix) :]
        return remainder.split("/")[0] or None

    def do_GET(self) -> None:  # noqa: N802
        path = unquote(self.path.split("?")[0])

        if path in ("/", "/health"):
            body = json.dumps({"status": "ok"}).encode("utf-8")
            self._send(200, "application/json; charset=utf-8", body)
            return

        project_token = self._token_from_path()
        if not project_token:
            self._send(404, "text/plain; charset=utf-8", b"Not found")
            return

        snapshot = self.store.load(project_token)
        if snapshot is None:
            self._send(404, "text/plain; charset=utf-8", b"Dashboard not found")
            return

        try:
            if path.endswith("/html"):
                body = render_html(snapshot).encode("utf-8")
                self._send(200, "text/html; charset=utf-8", body)
            elif path.endswith("/md") or path.endswith("/markdown"):
                body = render_markdown(snapshot).encode("utf-8")
                self._send(200, "text/markdown; charset=utf-8", body)
            else:
                body = render_json(snapshot).encode("utf-8")
                self._send(200, "application/json; charset=utf-8", body)
        except Exception as exc:
            logger.exception("Failed to render dashboard for %s", project_token)
            self._send(
                500,
                "text/plain; charset=utf-8",
                f"Render error: {exc}".encode("utf-8"),
            )

    def do_POST(self) -> None:  # noqa: N802
        path = unquote(self.path.split("?")[0])
        if path != self.publish_path:
            self._send(404, "text/plain; charset=utf-8", b"Not found")
            return

        if not self._authorized():
            self._send(401, "text/plain; charset=utf-8", b"Unauthorized")
            return

        body = self._read_body()
        try:
            payload = json.loads(body)
        except json.JSONDecodeError as exc:
            self._send(
                400,
                "text/plain; charset=utf-8",
                f"Invalid JSON: {exc}".encode("utf-8"),
            )
            return

        project_token = self.headers.get("X-Maestro-Project-Token")
        if not project_token:
            project_token = payload.get("project_token") or (
                payload.get("dashboard", {}).get("project_id") if isinstance(payload.get("dashboard"), dict) else None
            )
        if not project_token:
            self._send(
                400,
                "text/plain; charset=utf-8",
                b"Missing X-Maestro-Project-Token header or project_token/project_id in body",
            )
            return

        dashboard = payload.get("dashboard") or payload
        metadata = payload.get("metadata") or {}
        try:
            self.store.save(project_token, dashboard, metadata)
        except Exception as exc:
            logger.exception("Failed to save dashboard for %s", project_token)
            self._send(
                500,
                "text/plain; charset=utf-8",
                f"Storage error: {exc}".encode("utf-8"),
            )
            return

        response = {
            "status": "ok",
            "project_token": project_token,
            "url": f"{self.publish_path}/{project_token}/html",
        }
        body = json.dumps(response).encode("utf-8")
        self._send(201, "application/json; charset=utf-8", body)

    def log_message(self, format: str, *args: Any) -> None:
        logger.debug(format, *args)


def serve_remote_dashboard(
    data_dir: str | Path | None = None,
    host: str = "127.0.0.1",
    port: int = 8080,
    api_key: str | None = None,
    publish_path: str = DEFAULT_PUBLISH_PATH,
    blocking: bool = True,
) -> HTTPServer:
    """Start the standalone dashboard receiver.

    Args:
        data_dir: Directory where dashboard snapshots are stored.
        host: Interface to bind to.
        port: Port to listen on.
        api_key: Optional Bearer token required for POST /publish.
        publish_path: URL path that accepts published dashboards.
        blocking: If True, block the calling thread.

    Returns:
        The running HTTPServer instance.
    """
    _RemoteDashboardHandler.store = DashboardStore(data_dir)
    _RemoteDashboardHandler.api_key = api_key
    _RemoteDashboardHandler.publish_path = publish_path.rstrip("/")

    server = HTTPServer((host, port), _RemoteDashboardHandler)
    url = f"http://{host}:{port}"
    logger.info(
        "Standalone dashboard receiver listening at %s (POST %s, data_dir=%s)",
        url,
        publish_path,
        _RemoteDashboardHandler.store.data_dir,
    )

    if blocking:
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            logger.info("Shutting down dashboard receiver")
            server.shutdown()
    else:
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()

    return server


def stop_remote_dashboard_server(server: HTTPServer) -> None:
    """Stop a running standalone dashboard server."""
    server.shutdown()
    server.server_close()
