"""Multi-threaded HTTP media server with security headers, traversal guards, and stderr logging."""

from __future__ import annotations

import functools
import http.server
import logging
import mimetypes
import socketserver
import sys
import threading
import urllib.parse
from pathlib import Path
from typing import Any

logger = logging.getLogger("media_server")

# MIME type mapping for WebP and common image assets
MIME_MAP = {
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".svg": "image/svg+xml",
}


def is_safe_path(base_dir: Path, requested_rel_path: str) -> bool:
    """Ensure that the requested relative path stays strictly within the base directory."""
    try:
        base = base_dir.resolve()
        target = (base / requested_rel_path).resolve()
        # In Python 3.9+, Path.is_relative_to checks boundary containment
        return target.is_relative_to(base)
    except Exception:
        return False


class MediaRequestHandler(http.server.BaseHTTPRequestHandler):
    """HTTP Request Handler serving media assets with CORP, CORS, and PNA headers."""

    # Dictionary of pack_name -> Path(media_dir)
    pack_mounts: dict[str, Path] = {}

    def log_message(self, format: str, *args: Any) -> None:
        """Route HTTP logs exclusively to the Python logger on sys.stderr to protect MCP stdio."""
        logger.debug("%s - - [%s] %s", self.address_string(), self.log_date_time_string(), format % args)

    def _send_cors_headers(self) -> None:
        """Inject required headers for llama-server WebUI cross-origin isolation and private network access."""
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS, HEAD")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Access-Control-Allow-Private-Network", "true")
        self.send_header("Cross-Origin-Resource-Policy", "cross-origin")
        self.send_header("Cross-Origin-Embedder-Policy", "require-corp")
        self.send_header("Cache-Control", "no-cache")

    def do_OPTIONS(self) -> None:
        """Handle pre-flight requests."""
        self.send_response(200)
        self._send_cors_headers()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_HEAD(self) -> None:
        """Handle HEAD requests."""
        self._serve_file(send_body=False)

    def do_GET(self) -> None:
        """Handle GET requests."""
        self._serve_file(send_body=True)

    def _serve_file(self, send_body: bool = True) -> None:
        parsed_url = urllib.parse.urlparse(self.path)
        decoded_path = urllib.parse.unquote(parsed_url.path).strip("/")

        # Health / root check
        if not decoded_path or decoded_path == "healthz":
            self.send_response(200)
            self._send_cors_headers()
            self.send_header("Content-Type", "application/json")
            body = b'{"status":"ok"}\n'
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if send_body:
                self.wfile.write(body)
            return

        parts = decoded_path.split("/", 1)
        if len(parts) < 2:
            self._send_error_response(404, "Pack name or filename missing")
            return

        pack_name, rel_path = parts[0], parts[1]

        media_dir = self.pack_mounts.get(pack_name)
        if not media_dir:
            self._send_error_response(404, f"Pack '{pack_name}' not mounted")
            return

        # Security check: Prevent path traversal
        if not is_safe_path(media_dir, rel_path):
            logger.warning("Blocked directory traversal attempt: pack=%s, path=%s", pack_name, rel_path)
            self._send_error_response(403, "Forbidden path")
            return

        file_path = (media_dir / rel_path).resolve()
        if not file_path.is_file():
            self._send_error_response(404, f"File not found: {rel_path}")
            return

        # Determine MIME type
        ext = file_path.suffix.lower()
        mime_type = MIME_MAP.get(ext) or mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"

        try:
            file_size = file_path.stat().st_size
            self.send_response(200)
            self._send_cors_headers()
            self.send_header("Content-Type", mime_type)
            self.send_header("Content-Length", str(file_size))
            self.end_headers()

            if send_body:
                with open(file_path, "rb") as f:
                    while chunk := f.read(64 * 1024):
                        self.wfile.write(chunk)
        except (BrokenPipeError, ConnectionResetError):
            # Client browser dropped connection or closed tab while downloading
            pass
        except Exception as e:
            logger.error("Error serving %s: %s", file_path, e)

    def _send_error_response(self, code: int, message: str) -> None:
        try:
            self.send_response(code)
            self._send_cors_headers()
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            body = message.encode("utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass


class ThreadedTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    """Threaded TCP server configured with socket reuse and daemon threads."""

    allow_reuse_address = True
    daemon_threads = True


def start_background_server(
    pack_mounts: dict[str, Path],
    host: str = "0.0.0.0",
    port: int = 8088,
) -> tuple[ThreadedTCPServer, int]:
    """Start the media server in a background daemon thread.

    Returns:
        tuple[ThreadedTCPServer, int]: The running server instance and the bound port.
    """
    handler_class = functools.partial(MediaRequestHandler)
    # Configure mounted packs on the handler class
    MediaRequestHandler.pack_mounts = pack_mounts

    server = ThreadedTCPServer((host, port), handler_class)
    bound_port = server.server_address[1]

    thread = threading.Thread(
        target=server.serve_forever,
        name="MediaHTTPServerThread",
        daemon=True,
    )
    thread.start()

    logger.info("Media server running on http://%s:%d with %d mounted packs", host, bound_port, len(pack_mounts))
    return server, bound_port
