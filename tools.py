"""Media MCP Server entrypoint.

Runs a standalone MCP server providing LLMs with dynamic tools
from mounted external Action Packs, backed by a background media server.
"""

from __future__ import annotations

import argparse
import logging
import sys
import os
import stat
import time
from mcp.server import MCPServer

from core.config import (
    find_available_port,
    get_media_host,
    is_media_server_running,
    resolve_pack_sources,
    setup_logging,
)
from core.loader import discover_all_packs
from core.server import start_background_server

# Ensure all logging goes to sys.stderr to avoid corrupting MCP stdio JSON-RPC
setup_logging(level=logging.INFO)
logger = logging.getLogger("media_mcp")


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Media Action Packs MCP Server Runtime",
        prog="media-mcp",
    )
    parser.add_argument(
        "--pack",
        dest="packs",
        action="append",
        default=[],
        help="Path to an individual action pack directory (can be specified multiple times)",
    )
    parser.add_argument(
        "--packs-dir",
        dest="packs_dirs",
        action="append",
        default=[],
        help="Path to a directory containing multiple action pack subdirectories",
    )
    parser.add_argument(
        "--host",
        type=str,
        default=None,
        help="Host IP/hostname used in media embed URLs (defaults to LAN IP or MEDIA_HOST)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="Port for the media streaming HTTP server (defaults to 8088 or next available)",
    )
    parser.add_argument(
        "--serve",
        action="store_true",
        help="Run standalone HTTP media & dashboard server (stays running without waiting for MCP stdio)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    # 1. Resolve host and target port
    host = args.host or get_media_host()
    target_port = args.port or int(os.environ.get("MEDIA_PORT", "8088"))

    # 2. Discover pack locations
    pack_paths, packs_dirs = resolve_pack_sources(
        cli_packs=args.packs,
        cli_packs_dirs=args.packs_dirs,
    )

    logger.info("Resolving action packs from: explicit=%s, dirs=%s", pack_paths, packs_dirs)

    # 3. Load all packs and synthesize MCP tools
    loaded_packs = discover_all_packs(
        pack_paths=pack_paths,
        packs_dirs=packs_dirs,
        host=host,
        port=target_port,
    )

    if not loaded_packs:
        logger.warning(
            "No action packs found! Mount packs via '--pack /path' or place them in ~/.media-packs or ./packs"
        )

    # 4. Start background media server (or reuse if already running on target_port)
    already_running = is_media_server_running("127.0.0.1", target_port)
    server = None

    if already_running and not args.serve:
        bound_port = target_port
        logger.info("Reusing existing media server on http://%s:%d", host, bound_port)
    else:
        port = find_available_port(start_port=target_port)
        mounts = {pack.name: pack.media_dir for pack in loaded_packs.values()}
        server, bound_port = start_background_server(
            pack_mounts=mounts,
            host="0.0.0.0",
            port=port,
            loaded_packs=loaded_packs,
        )

    # Print startup banner to sys.stderr (keeps MCP stdio JSON-RPC clean)
    banner = f"""
==============================================================
🚀 Media MCP Server Ready
   • Media Dashboard & Prompt: http://{host}:{bound_port}/
   • Raw System Prompt:       http://{host}:{bound_port}/prompt
   • llama-server WebUI:      http://{host}:8080/
==============================================================
"""
    sys.stderr.write(banner)
    sys.stderr.flush()

    # 5. Initialize MCP Server and register tools
    mcp = MCPServer("MediaActionTools")

    # Helper tool to inspect loaded packs
    @mcp.tool()
    def list_action_packs() -> str:
        """Lists all loaded Action Packs and their available action tools."""
        if not loaded_packs:
            return "No action packs are currently loaded."

        lines = [f"Loaded {len(loaded_packs)} Action Pack(s):"]
        for p in loaded_packs.values():
            tool_names = ", ".join(t.name for t in p.tools)
            lines.append(f"- **{p.name}** (v{p.manifest.version}): {p.manifest.description or 'No description'}")
            lines.append(f"  Actions: {tool_names or 'none'}")
        return "\n".join(lines)

    # Register each synthesized action tool
    total_tools = 0
    for pack in loaded_packs.values():
        for tool in pack.tools:
            mcp.add_tool(
                tool.fn,
                name=tool.name,
                description=tool.description,
            )
            total_tools += 1

    logger.info(
        "Registered %d action tool(s) from %d pack(s). Starting MCP stdio runner...",
        total_tools,
        len(loaded_packs),
    )

    # 6. Run standalone server mode or MCP stdio runner
    is_devnull = False
    try:
        mode = os.fstat(sys.stdin.fileno()).st_mode
        is_devnull = stat.S_ISCHR(mode)
    except Exception:
        pass

    if args.serve or is_devnull:
        logger.info("Running in standalone HTTP media & dashboard server mode (port %d)...", bound_port)
        try:
            while True:
                time.sleep(3600)
        except (KeyboardInterrupt, SystemExit):
            pass
    else:
        # Run MCP server over stdio
        mcp.run()


if __name__ == "__main__":
    main()