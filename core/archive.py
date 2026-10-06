"""Archive packaging, encryption, decryption, and temporary extraction."""

from __future__ import annotations

import atexit
import base64
import io
import logging
import os
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from core.config import get_media_passkey

logger = logging.getLogger("archive")

MAGIC_HEADER = b"MMENC\x01"  # 6 bytes magic header for encrypted Media-MCP archives
SALT_SIZE = 16  # 16 bytes PBKDF2 salt

# Registry of active temporary directories for shutdown cleanup
_ACTIVE_TEMP_DIRS: list[tempfile.TemporaryDirectory] = []


def _cleanup_all_temp_dirs() -> None:
    """Atexit handler ensuring all extracted temporary pack directories are deleted."""
    for td in list(_ACTIVE_TEMP_DIRS):
        try:
            td.cleanup()
        except Exception:
            pass
    _ACTIVE_TEMP_DIRS.clear()


atexit.register(_cleanup_all_temp_dirs)


def derive_key(passkey: str, salt: bytes) -> bytes:
    """Derive a URL-safe base64-encoded 32-byte key from a passkey and salt."""
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=100_000,
    )
    return base64.urlsafe_b64encode(kdf.derive(passkey.encode("utf-8")))


def encrypt_pack_bytes(zip_bytes: bytes, passkey: str) -> bytes:
    """Encrypt raw zip bytes with PBKDF2 + Fernet into an MMENC binary payload."""
    salt = os.urandom(SALT_SIZE)
    key = derive_key(passkey, salt)
    token = Fernet(key).encrypt(zip_bytes)
    return MAGIC_HEADER + salt + token


def decrypt_pack_bytes(data: bytes, passkey: str | None = None) -> bytes:
    """Decrypt archive data if encrypted, or return raw bytes if standard unencrypted zip."""
    if data.startswith(MAGIC_HEADER):
        if not passkey:
            raise ValueError(
                "Action Pack is encrypted. Please provide MEDIA_PASSKEY in .env or pass --key."
            )
        header_len = len(MAGIC_HEADER)
        if len(data) < header_len + SALT_SIZE:
            raise ValueError("Corrupted encrypted archive: payload too short.")
        salt = data[header_len : header_len + SALT_SIZE]
        token = data[header_len + SALT_SIZE :]
        key = derive_key(passkey, salt)
        try:
            return Fernet(key).decrypt(token)
        except Exception as e:
            raise ValueError(
                f"Failed to decrypt Action Pack. Invalid passkey or corrupted archive: {e}"
            ) from e

    if data.startswith(b"PK"):
        return data

    raise ValueError(
        "Unrecognized archive format. Expected a .mm or .zip archive (plain or encrypted)."
    )


def is_archive_file(path: Path) -> bool:
    """Determine whether a path is an archive file (.mm, .zip, or matching magic bytes)."""
    if not path.is_file():
        return False
    suffix = path.suffix.lower()
    if suffix in (".mm", ".zip"):
        return True
    try:
        with open(path, "rb") as f:
            header = f.read(6)
            return header.startswith(b"PK") or header.startswith(MAGIC_HEADER)
    except Exception:
        return False


def pack_dir_to_zip_bytes(pack_dir: Path) -> bytes:
    """Package an action pack directory into in-memory zip bytes."""
    pack_dir = pack_dir.resolve()
    if not pack_dir.is_dir():
        raise FileNotFoundError(f"Pack directory not found: {pack_dir}")

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(pack_dir):
            root_path = Path(root)
            # Skip hidden folders / pycache
            dirs[:] = [d for d in dirs if not d.startswith(".") and d != "__pycache__"]
            for file in files:
                if file.startswith("."):
                    continue
                file_path = root_path / file
                rel_path = file_path.relative_to(pack_dir)
                zf.write(file_path, arcname=str(rel_path))

    return buffer.getvalue()


def create_pack_archive(
    pack_dir: Path,
    output_file: Path,
    encrypt: bool = False,
    passkey: str | None = None,
) -> Path:
    """Create a .mm (or .zip) archive from a pack directory, optionally encrypted."""
    pack_dir = pack_dir.resolve()
    zip_bytes = pack_dir_to_zip_bytes(pack_dir)

    if encrypt:
        effective_key = passkey or get_media_passkey()
        if not effective_key:
            raise ValueError(
                "Encryption requested but no passkey provided via --key or MEDIA_PASSKEY in .env"
            )
        output_bytes = encrypt_pack_bytes(zip_bytes, effective_key)
    else:
        output_bytes = zip_bytes

    output_file = output_file.resolve()
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_bytes(output_bytes)
    return output_file


def extract_pack_archive(
    archive_file: Path,
    passkey: str | None = None,
) -> tuple[Path, tempfile.TemporaryDirectory]:
    """Extract an archive (.mm or .zip) into a managed temporary directory.

    Returns:
        tuple[Path, tempfile.TemporaryDirectory]: (unpacked_pack_dir, temp_dir_handle)
    """
    archive_file = archive_file.resolve()
    if not archive_file.is_file():
        raise FileNotFoundError(f"Archive file not found: {archive_file}")

    data = archive_file.read_bytes()
    effective_key = passkey or get_media_passkey()
    zip_bytes = decrypt_pack_bytes(data, passkey=effective_key)

    temp_dir = tempfile.TemporaryDirectory(prefix="media_mcp_")
    _ACTIVE_TEMP_DIRS.append(temp_dir)
    extract_root = Path(temp_dir.name)

    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        zf.extractall(extract_root)

    # Resolve pack root directory inside the extraction
    if (extract_root / "pack.json").is_file():
        return extract_root, temp_dir

    # Check for single nested root directory
    children = [c for c in extract_root.iterdir() if c.is_dir()]
    for child in children:
        if (child / "pack.json").is_file():
            return child, temp_dir

    # Fallback search
    candidates = list(extract_root.rglob("pack.json"))
    if candidates:
        return candidates[0].parent, temp_dir

    return extract_root, temp_dir
