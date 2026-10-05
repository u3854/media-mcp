"""Multi-threaded HTTP media server with security headers, traversal guards, and stderr logging."""

from __future__ import annotations

import functools
import html
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


def render_dashboard_html(loaded_packs: dict[str, Any]) -> str:
    """Generate modern, responsive HTML dashboard for Action Packs and System Prompts."""
    prompts = [
        p.system_prompt
        for p in loaded_packs.values()
        if getattr(p, "system_prompt", None)
    ]
    raw_prompt = "\n\n".join(prompts) if prompts else "No system prompt configured for loaded pack(s)."
    escaped_prompt = html.escape(raw_prompt)

    cards_html_list: list[str] = []
    for pack_name, pack in loaded_packs.items():
        for action in pack.manifest.actions:
            media_files = action.get_media_files()
            first_media = media_files[0] if media_files else ""
            media_url = f"/{pack_name}/{first_media}" if first_media else ""
            alt_text = action.alt or action.name

            params_html = ""
            if action.parameters:
                p_items = []
                for p_name, p_def in action.parameters.items():
                    req_label = "required" if p_def.required else "optional"
                    p_items.append(
                        f"<code>{html.escape(p_name)}</code> ({html.escape(p_def.type)}, {req_label})"
                    )
                params_html = f'<div class="card-params"><strong>Params:</strong> {", ".join(p_items)}</div>'

            card = f"""
            <div class="action-card">
              <div class="card-media">
                <img src="{html.escape(media_url)}" alt="{html.escape(alt_text)}" loading="lazy" />
              </div>
              <div class="card-body">
                <span class="card-badge">{html.escape(pack_name)}</span>
                <h3 class="card-title">{html.escape(action.name)}</h3>
                <p class="card-desc">{html.escape(action.description or 'No description provided.')}</p>
                {params_html}
              </div>
            </div>"""
            cards_html_list.append(card)

    cards_html = (
        "\n".join(cards_html_list)
        if cards_html_list
        else "<p style='color: var(--text-muted);'>No actions found in loaded packs.</p>"
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Media-MCP Action Dashboard</title>
  <style>
    :root {{
      --bg: #090d16;
      --card-bg: #131b2e;
      --border: #232f48;
      --accent: #6366f1;
      --accent-hover: #4f46e5;
      --text: #f1f5f9;
      --text-muted: #94a3b8;
      --success: #10b981;
      --code-bg: #0b1120;
    }}
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      background-color: var(--bg);
      color: var(--text);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
      line-height: 1.5;
      padding: 2rem 1.5rem;
    }}
    .container {{
      max-width: 1040px;
      margin: 0 auto;
    }}
    header {{
      display: flex;
      flex-wrap: wrap;
      justify-content: space-between;
      align-items: center;
      gap: 1rem;
      margin-bottom: 2rem;
      padding-bottom: 1.5rem;
      border-bottom: 1px solid var(--border);
    }}
    .brand {{
      display: flex;
      align-items: center;
      gap: 0.75rem;
    }}
    .brand h1 {{
      font-size: 1.5rem;
      font-weight: 700;
      letter-spacing: -0.025em;
    }}
    .badge-status {{
      display: inline-flex;
      align-items: center;
      gap: 0.4rem;
      background: rgba(16, 185, 129, 0.15);
      color: var(--success);
      padding: 0.25rem 0.6rem;
      border-radius: 9999px;
      font-size: 0.8rem;
      font-weight: 600;
      border: 1px solid rgba(16, 185, 129, 0.3);
    }}
    .badge-status::before {{
      content: "";
      width: 0.45rem;
      height: 0.45rem;
      background-color: var(--success);
      border-radius: 50%;
      display: inline-block;
      box-shadow: 0 0 8px var(--success);
    }}
    .header-actions {{
      display: flex;
      gap: 0.75rem;
    }}
    .btn {{
      display: inline-flex;
      align-items: center;
      gap: 0.5rem;
      padding: 0.5rem 1rem;
      border-radius: 0.5rem;
      font-size: 0.875rem;
      font-weight: 600;
      text-decoration: none;
      cursor: pointer;
      border: none;
      transition: all 0.15s ease;
    }}
    .btn-primary {{
      background-color: var(--accent);
      color: white;
    }}
    .btn-primary:hover {{
      background-color: var(--accent-hover);
      transform: translateY(-1px);
    }}
    .btn-secondary {{
      background-color: var(--card-bg);
      color: var(--text);
      border: 1px solid var(--border);
    }}
    .btn-secondary:hover {{
      background-color: var(--border);
    }}
    .section-card {{
      background-color: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: 0.75rem;
      padding: 1.5rem;
      margin-bottom: 2rem;
      box-shadow: 0 4px 20px rgba(0, 0, 0, 0.25);
    }}
    .section-header {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 1rem;
    }}
    .section-title {{
      font-size: 1.15rem;
      font-weight: 600;
    }}
    .prompt-box {{
      background-color: var(--code-bg);
      border: 1px solid var(--border);
      border-radius: 0.5rem;
      padding: 1rem;
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
      font-size: 0.9rem;
      white-space: pre-wrap;
      word-break: break-word;
      color: #e2e8f0;
      line-height: 1.6;
      max-height: 320px;
      overflow-y: auto;
    }}
    .prompt-hint {{
      margin-top: 0.75rem;
      font-size: 0.825rem;
      color: var(--text-muted);
    }}
    .prompt-hint code {{
      background: var(--code-bg);
      padding: 0.15rem 0.4rem;
      border-radius: 0.25rem;
      border: 1px solid var(--border);
      color: #cbd5e1;
    }}
    .cards-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
      gap: 1.25rem;
      margin-top: 1rem;
    }}
    .action-card {{
      background-color: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: 0.75rem;
      overflow: hidden;
      display: flex;
      flex-direction: column;
      transition: transform 0.15s ease, border-color 0.15s ease;
    }}
    .action-card:hover {{
      border-color: #3b82f6;
      transform: translateY(-2px);
    }}
    .card-media {{
      background-color: #070a12;
      display: flex;
      align-items: center;
      justify-content: center;
      min-height: 200px;
      max-height: 240px;
      overflow: hidden;
      border-bottom: 1px solid var(--border);
    }}
    .card-media img {{
      max-width: 100%;
      max-height: 240px;
      object-fit: contain;
    }}
    .card-body {{
      padding: 1.25rem;
      display: flex;
      flex-direction: column;
      flex-grow: 1;
    }}
    .card-badge {{
      display: inline-block;
      font-size: 0.75rem;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.05em;
      color: #818cf8;
      margin-bottom: 0.4rem;
    }}
    .card-title {{
      font-size: 1.1rem;
      font-weight: 600;
      margin-bottom: 0.5rem;
    }}
    .card-desc {{
      font-size: 0.875rem;
      color: var(--text-muted);
      margin-bottom: 1rem;
      flex-grow: 1;
    }}
    .card-params {{
      font-size: 0.8rem;
      background: var(--code-bg);
      border: 1px solid var(--border);
      padding: 0.5rem 0.75rem;
      border-radius: 0.375rem;
      color: #94a3b8;
    }}
    .endpoints-nav {{
      display: flex;
      flex-wrap: wrap;
      gap: 0.75rem;
      margin-top: 2rem;
      padding-top: 1.5rem;
      border-top: 1px solid var(--border);
      font-size: 0.85rem;
      color: var(--text-muted);
    }}
    .endpoints-nav a {{
      color: #818cf8;
      text-decoration: none;
    }}
    .endpoints-nav a:hover {{
      text-decoration: underline;
    }}
  </style>
</head>
<body>
  <div class="container">
    <header>
      <div class="brand">
        <h1>Media-MCP Action Dashboard</h1>
        <span class="badge-status">Active</span>
      </div>
      <div class="header-actions">
        <a id="webui-btn" class="btn btn-primary" href="#" target="_blank">
          Open llama-server WebUI (:8080) ↗
        </a>
      </div>
    </header>

    <section class="section-card">
      <div class="section-header">
        <h2 class="section-title">📋 System Prompt</h2>
        <button id="copy-btn" class="btn btn-secondary" onclick="copyPrompt()">📋 Copy Prompt</button>
      </div>
      <pre id="prompt-box" class="prompt-box">{escaped_prompt}</pre>
      <p class="prompt-hint">
        💡 Paste this prompt into the <strong>System Message</strong> box in the WebUI, or pipe directly via terminal:
        <code>curl -s http://<span class="host-placeholder">localhost:8088</span>/prompt | pbcopy</code>
      </p>
    </section>

    <section>
      <h2 class="section-title">🎭 Available Action Tools & Media Assets</h2>
      <div class="cards-grid">
        {cards_html}
      </div>
    </section>

    <footer class="endpoints-nav">
      <span>Endpoints:</span>
      <a href="/prompt">Raw Prompt (/prompt)</a> •
      <a href="/healthz">Health Check (/healthz)</a> •
      <a id="webui-footer-link" href="#" target="_blank">llama-server WebUI (:8080)</a>
    </footer>
  </div>

  <script>
    const host = window.location.hostname || 'localhost';
    const webuiUrl = 'http://' + host + ':8080';
    document.getElementById('webui-btn').href = webuiUrl;
    document.getElementById('webui-footer-link').href = webuiUrl;
    document.querySelectorAll('.host-placeholder').forEach(el => el.textContent = host + ':8088');

    function copyPrompt() {{
      const text = document.getElementById('prompt-box').textContent;
      navigator.clipboard.writeText(text).then(() => {{
        const btn = document.getElementById('copy-btn');
        btn.textContent = '✓ Copied!';
        btn.style.color = '#10b981';
        setTimeout(() => {{
          btn.textContent = '📋 Copy Prompt';
          btn.style.color = '';
        }}, 2000);
      }}).catch(err => {{
        alert('Failed to copy: ' + err);
      }});
    }}
  </script>
</body>
</html>"""


class MediaRequestHandler(http.server.BaseHTTPRequestHandler):
    """HTTP Request Handler serving media assets with CORP, CORS, and PNA headers."""

    # Dictionary of pack_name -> Path(media_dir)
    pack_mounts: dict[str, Path] = {}
    # Dictionary of pack_name -> LoadedPack
    loaded_packs: dict[str, Any] = {}

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

        # Health check
        if decoded_path == "healthz":
            self.send_response(200)
            self._send_cors_headers()
            self.send_header("Content-Type", "application/json")
            body = b'{"status":"ok"}\n'
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if send_body:
                self.wfile.write(body)
            return

        # System prompt plain text endpoint
        if decoded_path == "prompt":
            prompts = [
                p.system_prompt
                for p in self.loaded_packs.values()
                if getattr(p, "system_prompt", None)
            ]
            prompt_content = "\n\n".join(prompts) if prompts else ""
            if prompt_content and not prompt_content.endswith("\n"):
                prompt_content += "\n"
            body = prompt_content.encode("utf-8")
            self.send_response(200)
            self._send_cors_headers()
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if send_body:
                self.wfile.write(body)
            return

        # Action Pack Dashboard at root or index.html
        if not decoded_path or decoded_path in ("index.html", "dashboard"):
            dashboard_html = render_dashboard_html(self.loaded_packs)
            body = dashboard_html.encode("utf-8")
            self.send_response(200)
            self._send_cors_headers()
            self.send_header("Content-Type", "text/html; charset=utf-8")
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
    loaded_packs: dict[str, Any] | None = None,
) -> tuple[ThreadedTCPServer, int]:
    """Start the media server in a background daemon thread.

    Returns:
        tuple[ThreadedTCPServer, int]: The running server instance and the bound port.
    """
    handler_class = functools.partial(MediaRequestHandler)
    # Configure mounted packs and pack objects on the handler class
    MediaRequestHandler.pack_mounts = pack_mounts
    MediaRequestHandler.loaded_packs = loaded_packs or {}

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
