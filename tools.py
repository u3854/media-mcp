"""Media MCP Server entrypoint.

Runs a standalone MCP server providing LLMs with dynamic tools
from mounted external Action Packs, backed by a background media server.
"""

from __future__ import annotations

import argparse
import logging
import sys
from mcp.server import MCPServer

from core.config import (
    find_available_port,
    get_media_host,
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
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    # 1. Resolve host and port
    host = args.host or get_media_host()
    port = find_available_port(start_port=args.port)

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
        port=port,
    )

    if not loaded_packs:
        logger.warning(
            "No action packs found! Mount packs via '--pack /path' or place them in ~/.media-packs or ./packs"
        )

    # 4. Start background media server
    mounts = {pack.name: pack.media_dir for pack in loaded_packs.values()}
    server, bound_port = start_background_server(
        pack_mounts=mounts,
        host="0.0.0.0",
        port=port,
    )

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

    # 6. Run MCP server over stdio
    mcp.run()


if __name__ == "__main__":
    main()