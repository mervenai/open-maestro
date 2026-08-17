"""Lightweight HTTP server for the client-facing milestone dashboard."""

from __future__ import annotations

import logging
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from threading import Thread
from typing import Any

from open_maestro.milestones.dashboard import (
    export_dashboard_html,
    export_dashboard_json,
    export_dashboard_markdown,
)
from open_maestro.milestones.store import MilestoneStore

logger = logging.getLogger(__name__)


class _DashboardHandler(BaseHTTPRequestHandler):
    """HTTP request handler for the milestone dashboard."""

    project_path: Path = Path.cwd()

    def _load_plan(self):
        store = MilestoneStore(self.project_path)
        return store.load()

    def _respond(self, status: int, content_type: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?")[0]
        try:
            if path in ("/", "/index.html"):
                plan = self._load_plan()
                html = export_dashboard_html(plan)
                self._respond(200, "text/html; charset=utf-8", html.encode("utf-8"))
            elif path == "/api/dashboard":
                plan = self._load_plan()
                data = export_dashboard_json(plan)
                self._respond(200, "application/json; charset=utf-8", data.encode("utf-8"))
            elif path == "/dashboard.md":
                plan = self._load_plan()
                md = export_dashboard_markdown(plan)
                self._respond(200, "text/markdown; charset=utf-8", md.encode("utf-8"))
            else:
                self._respond(404, "text/plain; charset=utf-8", b"Not found")
        except Exception as exc:
            logger.exception("Dashboard request failed")
            self._respond(
                500,
                "text/plain; charset=utf-8",
                f"Server error: {exc}".encode("utf-8"),
            )

    def log_message(self, format: str, *args: Any) -> None:
        logger.debug(format, *args)


def serve_dashboard(
    project_path: str | Path,
    host: str = "127.0.0.1",
    port: int = 8080,
    blocking: bool = True,
) -> HTTPServer:
    """Start an HTTP server serving the milestone dashboard.

    Args:
        project_path: Root directory of the project to display.
        host: Interface to bind to.
        port: Port to listen on.
        blocking: If True, block the calling thread. If False, run in a
            background thread and return the server instance.

    Returns:
        The running HTTPServer instance.
    """
    project_path = Path(project_path)
    _DashboardHandler.project_path = project_path

    server = HTTPServer((host, port), _DashboardHandler)
    url = f"http://{host}:{port}"
    logger.info("Serving milestone dashboard at %s for project %s", url, project_path)

    if blocking:
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            logger.info("Shutting down dashboard server")
            server.shutdown()
    else:
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()

    return server


def stop_dashboard_server(server: HTTPServer) -> None:
    """Stop a running dashboard server."""
    server.shutdown()
    server.server_close()
