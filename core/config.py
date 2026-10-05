"""Configuration, networking, and pack path discovery."""

from __future__ import annotations

import logging
import os
import socket
import sys
from pathlib import Path


def setup_logging(level: int = logging.INFO) -> None:
    """Configure logging to write strictly to sys.stderr to avoid polluting MCP stdio."""
    logging.basicConfig(
        stream=sys.stderr,
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        force=True,
    )


def get_lan_ip() -> str:
    """Determine the LAN IP address of this machine, falling back to 127.0.0.1."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # Does not actually transmit packets, just determines the route
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
    except Exception:
        ip = "127.0.0.1"
    finally:
        s.close()
    return ip


def get_media_host() -> str:
    """Return the media server hostname/IP for embed URLs.
    
    Can be overridden via the MEDIA_HOST environment variable.
    """
    env_host = os.environ.get("MEDIA_HOST", "").strip()
    if env_host:
        return env_host
    return get_lan_ip()


def is_port_available(host: str, port: int) -> bool:
    """Check if a TCP port can be bound."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False


def find_available_port(start_port: int | None = None, max_attempts: int = 20) -> int:
    """Find an available port starting from start_port (or MEDIA_PORT env var, default 8088)."""
    if start_port is None:
        start_port = int(os.environ.get("MEDIA_PORT", "8088"))

    for offset in range(max_attempts):
        port = start_port + offset
        if is_port_available("0.0.0.0", port):
            return port

    raise RuntimeError(
        f"Unable to find an available port in range {start_port}..{start_port + max_attempts - 1}"
    )


def resolve_pack_sources(
    cli_packs: list[str] | None = None,
    cli_packs_dirs: list[str] | None = None,
) -> tuple[list[Path], list[Path]]:
    """Resolve individual pack paths and packs directories from CLI flags, env vars, and defaults.

    Returns:
        tuple[list[Path], list[Path]]: (individual_pack_paths, packs_directory_paths)
    """
    pack_paths: list[Path] = []
    packs_dirs: list[Path] = []

    # 1. CLI explicit packs
    if cli_packs:
        for p in cli_packs:
            resolved = Path(p).expanduser().resolve()
            if resolved not in pack_paths:
                pack_paths.append(resolved)

    # 2. CLI packs directories
    if cli_packs_dirs:
        for d in cli_packs_dirs:
            resolved = Path(d).expanduser().resolve()
            if resolved not in packs_dirs:
                packs_dirs.append(resolved)

    # 3. Environment variable MEDIA_PACKS (e.g. /path/a:/path/b or comma-separated)
    env_packs = os.environ.get("MEDIA_PACKS", "").strip()
    if env_packs:
        separator = ":" if ":" in env_packs else ","
        for p in env_packs.split(separator):
            p = p.strip()
            if p:
                resolved = Path(p).expanduser().resolve()
                if resolved not in pack_paths:
                    pack_paths.append(resolved)

    # 4. Environment variable MEDIA_PACKS_DIR
    env_packs_dir = os.environ.get("MEDIA_PACKS_DIR", "").strip()
    if env_packs_dir:
        resolved = Path(env_packs_dir).expanduser().resolve()
        if resolved not in packs_dirs:
            packs_dirs.append(resolved)

    # 5. Default search directories if nothing explicitly provided
    default_user_dir = Path("~/.media-packs").expanduser().resolve()
    if default_user_dir.is_dir() and default_user_dir not in packs_dirs:
        packs_dirs.append(default_user_dir)

    default_local_dir = Path("./packs").resolve()
    if default_local_dir.is_dir() and default_local_dir not in packs_dirs:
        packs_dirs.append(default_local_dir)

    return pack_paths, packs_dirs
