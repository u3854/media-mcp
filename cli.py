#!/usr/bin/env python3
"""Developer CLI for scaffolding and validating Action Packs."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from core.archive import create_pack_archive, extract_pack_archive, is_archive_file
from core.config import get_media_passkey
from core.loader import load_manifest, validate_pack_contents, validate_pack_dir

SAMPLE_PACK_MANIFEST = {
    "$schema": "https://media-mcp.org/schema/v1/pack.json",
    "name": "{pack_name}",
    "version": "1.0.0",
    "description": "Custom action pack for {pack_name}",
    "actions": [
        {
            "name": "attack",
            "description": "Performs an attack against a target and displays the attack animation",
            "media": "attack.webp",
            "alt": "Attack Strike",
            "parameters": {
                "target": {
                    "type": "string",
                    "description": "The enemy or entity being attacked",
                    "required": True,
                },
                "weapon": {
                    "type": "string",
                    "description": "The weapon used (e.g. sword, staff, bow)",
                    "required": False,
                    "default": "sword",
                },
            },
            "template": "Attacked **{target}** with {weapon}!\n\n{media}",
        },
        {
            "name": "defend",
            "description": "Raises guard to block incoming attacks",
            "media": "defend.webp",
            "alt": "Defensive Guard",
            "template": "Raised shield and assumed a defensive stance.\n\n{media}",
        },
        {
            "name": "spell_burst",
            "description": "Casts a magic burst with randomized animation effects",
            "media": ["attack.webp", "defend.webp"],
            "selection": "random",
            "alt": "Magic Spell",
            "template": "Unleashed an arcane burst!\n\n{media}",
        },
    ],
}


def sanitize_name(name: str) -> str:
    """Sanitize directory name to produce a valid identifier for pack name."""
    cleaned = re.sub(r"[^A-Za-z0-9_]+", "_", name).strip("_").lower()
    return cleaned or "my_action_pack"


def handle_create(args: argparse.Namespace) -> int:
    """Scaffold a new Action Pack directory with boilerplate pack.json and media folder."""
    target_dir = Path(args.path).resolve()
    pack_name = args.name or sanitize_name(target_dir.name)

    if target_dir.exists() and not args.force:
        manifest_file = target_dir / "pack.json"
        if manifest_file.exists():
            sys.stderr.write(f"Error: {manifest_file} already exists. Use --force to overwrite.\n")
            return 1

    target_dir.mkdir(parents=True, exist_ok=True)
    media_dir = target_dir / "media"
    media_dir.mkdir(exist_ok=True)

    # Generate manifest content
    manifest_data = json.loads(json.dumps(SAMPLE_PACK_MANIFEST).replace("{pack_name}", pack_name))
    manifest_path = target_dir / "pack.json"
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest_data, f, indent=2)
        f.write("\n")

    # Add a .gitkeep or guide in media/
    readme_path = media_dir / "README.txt"
    if not readme_path.exists():
        with open(readme_path, "w", encoding="utf-8") as f:
            f.write(
                "Place your WebP animation assets in this folder.\n"
                "Refer to them by filename in pack.json (e.g. 'attack.webp').\n"
            )

    # Add starter prompts/default.txt
    prompts_dir = target_dir / "prompts"
    prompts_dir.mkdir(exist_ok=True)
    prompt_path = prompts_dir / "default.txt"
    if not prompt_path.exists():
        with open(prompt_path, "w", encoding="utf-8") as f:
            f.write(
                f"You have access to interactive media tools from the '{pack_name}' action pack.\n"
                f"When appropriate, call the provided action tools to display animations and reaction clips inline.\n"
            )

    sys.stderr.write(f"✓ Created action pack '{pack_name}' at {target_dir}\n")
    sys.stderr.write(f"  - Manifest: {manifest_path}\n")
    sys.stderr.write(f"  - Media directory: {media_dir}\n")
    sys.stderr.write(f"  - Default prompt: {prompt_path}\n")
    sys.stderr.write("\nNext steps:\n")
    sys.stderr.write("  1. Add WebP images to the media/ folder (e.g. attack.webp, defend.webp)\n")
    sys.stderr.write("  2. Customize prompts in prompts/ (e.g. prompts/default.txt)\n")
    sys.stderr.write(f"  3. Validate your pack: python cli.py validate {target_dir}\n")
    return 0


def handle_export(args: argparse.Namespace) -> int:
    """Export an Action Pack directory to a .mm (or .zip) archive, optionally encrypted."""
    target_dir = Path(args.path).resolve()
    if not target_dir.is_dir():
        sys.stderr.write(f"✗ Export Failed: Pack directory not found: {target_dir}\n")
        return 1

    try:
        manifest_file, media_dir = validate_pack_dir(target_dir)
        manifest = load_manifest(manifest_file)
        errors = validate_pack_contents(manifest, media_dir)
        if errors:
            sys.stderr.write(f"✗ Validation Failed before export with {len(errors)} error(s):\n")
            for err in errors:
                sys.stderr.write(f"  - {err}\n")
            return 1
    except Exception as e:
        sys.stderr.write(f"✗ Export Failed: Invalid pack structure: {e}\n")
        return 1

    # Output file path
    if args.out:
        output_file = Path(args.out).resolve()
    else:
        output_file = target_dir.parent / f"{manifest.name}.mm"

    # Encryption configuration
    encrypt = bool(args.encrypt)
    passkey = getattr(args, "key", None) or get_media_passkey()
    if encrypt and not passkey:
        sys.stderr.write(
            "✗ Error: Encryption requested but no passkey found. "
            "Set MEDIA_PASSKEY in .env or pass --key.\n"
        )
        return 1

    sys.stderr.write(f"Packaging pack '{manifest.name}' from {target_dir}...\n")
    try:
        out_path = create_pack_archive(
            pack_dir=target_dir,
            output_file=output_file,
            encrypt=encrypt,
            passkey=passkey,
        )
    except Exception as e:
        sys.stderr.write(f"✗ Export Failed: {e}\n")
        return 1

    file_size = out_path.stat().st_size
    mode_desc = "Encrypted (.mm archive)" if encrypt else "Plain (.mm archive)"
    sys.stderr.write(f"✓ Exported pack '{manifest.name}' to {out_path}\n")
    sys.stderr.write(f"  Format: {mode_desc}\n")
    sys.stderr.write(f"  Size: {file_size} bytes\n")
    return 0


def handle_validate(args: argparse.Namespace) -> int:
    """Validate a pack manifest and verify all referenced media assets exist."""
    target_path = Path(args.path).resolve()
    passkey = getattr(args, "key", None) or get_media_passkey()

    if target_path.is_file():
        if not is_archive_file(target_path):
            sys.stderr.write(f"✗ Validation Failed: File is not a recognized pack archive: {target_path}\n")
            return 1
        sys.stderr.write(f"Extracting and validating archive at {target_path}...\n")
        try:
            target_dir, _ = extract_pack_archive(target_path, passkey=passkey)
        except Exception as e:
            sys.stderr.write(f"✗ Extraction Failed: {e}\n")
            return 1
    elif target_path.is_dir():
        target_dir = target_path
        sys.stderr.write(f"Validating action pack at {target_dir}...\n")
    else:
        sys.stderr.write(f"✗ Validation Failed: Path not found: {target_path}\n")
        return 1

    try:
        manifest_file, media_dir = validate_pack_dir(target_dir)
    except FileNotFoundError as e:
        sys.stderr.write(f"✗ Validation Failed: {e}\n")
        return 1

    try:
        manifest = load_manifest(manifest_file)
    except Exception as e:
        sys.stderr.write(f"✗ Failed to parse pack.json: {e}\n")
        return 1

    errors = validate_pack_contents(manifest, media_dir)
    if errors:
        sys.stderr.write(f"✗ Validation Failed with {len(errors)} error(s):\n")
        for err in errors:
            sys.stderr.write(f"  - {err}\n")
        return 1

    prompts_dir = target_dir / "prompts"
    prompt_files = list(prompts_dir.glob("*.txt")) if prompts_dir.is_dir() else []
    if prompt_files:
        prompt_info = f"{len(prompt_files)} prompt(s): {', '.join(p.stem for p in sorted(prompt_files))}"
    else:
        legacy_prompt = target_dir / "system_prompt.txt"
        if legacy_prompt.is_file():
            prompt_info = "Legacy system_prompt.txt (will migrate to prompts/default.txt)"
        else:
            prompt_info = "None"

    archive_tag = f" (Archive: {target_path.name})" if target_path.is_file() else ""
    sys.stderr.write(f"✓ Pack '{manifest.name}' (v{manifest.version}){archive_tag} is valid!\n")
    sys.stderr.write(f"  Description: {manifest.description or '(No description)'}\n")
    sys.stderr.write(f"  Prompts: {prompt_info}\n")
    sys.stderr.write(f"  Actions ({len(manifest.actions)}):\n")
    for act in manifest.actions:
        param_summary = ", ".join(
            f"{k}{'*' if v.required else ''}: {v.type}" for k, v in act.parameters.items()
        ) or "no parameters"
        files = act.get_media_files()
        media_summary = f"{files[0]} ({len(files)} files, selection={act.selection})" if len(files) > 1 else files[0]
        sys.stderr.write(f"    • {act.name}({param_summary}) -> media/{media_summary}\n")

    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Action Pack Developer CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # create
    create_parser = subparsers.add_parser("create", help="Scaffold a new Action Pack")
    create_parser.add_argument("path", help="Destination path for the new pack")
    create_parser.add_argument("--name", help="Identifier for the pack (defaults to folder name)")
    create_parser.add_argument("--force", action="store_true", help="Overwrite existing pack.json")

    # validate
    validate_parser = subparsers.add_parser("validate", help="Validate an Action Pack manifest and media files")
    validate_parser.add_argument("path", help="Path to the pack directory or archive (.mm / .zip) to validate")
    validate_parser.add_argument("--key", "-k", help="Passkey to decrypt archive if encrypted")

    # export
    export_parser = subparsers.add_parser("export", help="Export an Action Pack to a .mm archive")
    export_parser.add_argument("path", help="Path to the pack directory to export")
    export_parser.add_argument("--out", "-o", help="Destination path for the archive (defaults to <pack_name>.mm)")
    export_parser.add_argument("--encrypt", "-e", action="store_true", help="Encrypt the archive using MEDIA_PASSKEY or --key")
    export_parser.add_argument("--key", "-k", help="Passkey to use for encryption (overrides MEDIA_PASSKEY in .env)")

    args = parser.parse_args()

    if args.command == "create":
        sys.exit(handle_create(args))
    elif args.command == "validate":
        sys.exit(handle_validate(args))
    elif args.command == "export":
        sys.exit(handle_export(args))


if __name__ == "__main__":
    main()
