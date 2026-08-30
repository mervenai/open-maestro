"""Standalone dashboard receiver for Open Maestro.

This package provides a self-hosted HTTP receiver that accepts published
dashboard snapshots from the Maestro CLI and serves them as HTML, JSON, or
Markdown. It has no dependency on the Merven project.
"""

from open_maestro.dashboard_server.server import serve_remote_dashboard

__all__ = ["serve_remote_dashboard"]
