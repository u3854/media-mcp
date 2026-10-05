"""Action pack discovery, validation, and dynamic MCP tool synthesis."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from core.models import (
    ActionDefinition,
    ActionParameter,
    LoadedPack,
    PackManifest,
    SynthesizedTool,
)

logger = logging.getLogger("loader")

# Valid tool name according to MCP specification (SEP-986)
TOOL_NAME_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,128}$")

# Mapping of JSON schema type strings to Python types for dynamic function signatures
TYPE_MAP = {
    "string": "str",
    "integer": "int",
    "number": "float",
    "boolean": "bool",
}


def validate_pack_dir(pack_dir: Path) -> tuple[Path, Path]:
    """Validate that pack_dir is a directory containing pack.json and media/ folder."""
    pack_dir = pack_dir.resolve()
    if not pack_dir.is_dir():
        raise FileNotFoundError(f"Pack directory not found: {pack_dir}")

    manifest_file = pack_dir / "pack.json"
    if not manifest_file.is_file():
        raise FileNotFoundError(f"Missing pack manifest 'pack.json' in {pack_dir}")

    media_dir = pack_dir / "media"
    if not media_dir.is_dir():
        raise FileNotFoundError(f"Missing 'media/' directory in {pack_dir}")

    return manifest_file, media_dir


def load_manifest(manifest_file: Path) -> PackManifest:
    """Parse and validate pack.json against PackManifest schema."""
    with open(manifest_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    return PackManifest.model_validate(data)


def validate_pack_contents(manifest: PackManifest, media_dir: Path) -> list[str]:
    """Validate action definitions, parameter names, and media file existence.

    Returns:
        list[str]: List of validation warning or error messages (empty if valid).
    """
    errors: list[str] = []

    if not TOOL_NAME_PATTERN.match(manifest.name):
        errors.append(
            f"Pack name '{manifest.name}' contains invalid characters. Must match {TOOL_NAME_PATTERN.pattern}"
        )

    for action in manifest.actions:
        # Check action name
        if not TOOL_NAME_PATTERN.match(action.name):
            errors.append(
                f"Action name '{action.name}' is invalid. Must match {TOOL_NAME_PATTERN.pattern}"
            )

        # Check parameter names and types
        for param_name, param in action.parameters.items():
            if not param_name.isidentifier():
                errors.append(
                    f"Parameter '{param_name}' in action '{action.name}' is not a valid Python identifier"
                )
            if param.type not in TYPE_MAP:
                errors.append(
                    f"Parameter '{param_name}' has unsupported type '{param.type}'. Supported: {list(TYPE_MAP.keys())}"
                )

        # Check referenced media files exist
        for media_file in action.get_media_files():
            if ".." in media_file or media_file.startswith("/"):
                errors.append(f"Action '{action.name}' references unsafe media path: '{media_file}'")
            else:
                target = media_dir / media_file
                if not target.is_file():
                    errors.append(
                        f"Action '{action.name}' references missing media file: 'media/{media_file}'"
                    )

    return errors


def render_action_output(
    action: ActionDefinition,
    pack_name: str,
    host: str,
    port: int,
    call_kwargs: dict[str, Any],
) -> str:
    """Render the tool output by choosing media, generating the embed, and applying templates."""
    chosen_file = action.pick_media_file()
    media_url = f"http://{host}:{port}/{pack_name}/{chosen_file}"
    alt_text = action.alt.strip() or action.name.replace("_", " ").title()
    embed = f"![{alt_text}]({media_url})"

    template = action.template.strip()
    if not template:
        return embed

    # Prepare template replacement variables
    substitutions = {str(k): str(v) for k, v in call_kwargs.items()}
    substitutions["media"] = embed

    # Safe regex replacement: replaces {var} with value if present, otherwise leaves it unchanged
    has_media_placeholder = "{media}" in template

    def replace_var(match: re.Match[str]) -> str:
        var_name = match.group(1)
        return substitutions.get(var_name, match.group(0))

    rendered = re.sub(r"\{([a-zA-Z0-9_]+)\}", replace_var, template)

    # If {media} was not part of the template, append it to the end
    if not has_media_placeholder:
        rendered = f"{rendered}\n\n{embed}"

    return rendered


def synthesize_action_tool(
    action: ActionDefinition,
    pack_name: str,
    tool_name: str,
    host: str,
    port: int,
) -> SynthesizedTool:
    """Synthesize a native, typed Python callable for FastMCP / MCPServer reflection."""
    # Partition parameters into required (no default) and optional (with default)
    required_args: list[str] = []
    optional_args: list[str] = []
    arg_names: list[str] = []

    for name, param in action.parameters.items():
        type_str = TYPE_MAP.get(param.type, "str")
        arg_names.append(name)
        if param.required and param.default is None:
            required_args.append(f"{name}: {type_str}")
        else:
            default_val = repr(param.default) if param.default is not None else "None"
            optional_args.append(f"{name}: {type_str} = {default_val}")

    signature_params = ", ".join(required_args + optional_args)
    pass_args = ", ".join(f"{name}={name}" for name in arg_names)

    def runtime_handler(**kwargs: Any) -> str:
        return render_action_output(action, pack_name, host, port, kwargs)

    # Internal Python function name must be a valid Python identifier (no hyphens)
    py_func_name = re.sub(r"[^A-Za-z0-9_]", "_", tool_name)
    if not py_func_name or py_func_name[0].isdigit():
        py_func_name = f"action_{py_func_name}"

    # Compile a native function with the exact signature so Pydantic / FastMCP inspects it accurately
    func_code = f"""
def {py_func_name}({signature_params}) -> str:
    \"\"\"{action.description}\"\"\"
    return _runtime_handler({pass_args})
"""
    namespace: dict[str, Any] = {
        "_runtime_handler": runtime_handler,
        "str": str,
        "int": int,
        "float": float,
        "bool": bool,
        "None": None,
    }

    exec(func_code, namespace)  # noqa: S102
    tool_func = namespace[py_func_name]
    tool_func.__name__ = py_func_name
    tool_func.__doc__ = action.description or f"Performs action '{action.name}' from pack '{pack_name}'"

    return SynthesizedTool(
        name=tool_name,
        description=action.description,
        fn=tool_func,
    )


def load_pack(
    pack_dir: Path,
    host: str = "127.0.0.1",
    port: int = 8088,
    namespace_prefix: bool = True,
) -> LoadedPack:
    """Load, validate, and synthesize tools for a single action pack."""
    manifest_file, media_dir = validate_pack_dir(pack_dir)
    manifest = load_manifest(manifest_file)

    errors = validate_pack_contents(manifest, media_dir)
    if errors:
        raise ValueError(f"Pack validation failed for {pack_dir}:\n  - " + "\n  - ".join(errors))

    tools: list[SynthesizedTool] = []
    for action in manifest.actions:
        # Determine tool name: avoid duplicate prefix if action matches pack or is already prefixed
        if action.name == manifest.name or action.name.startswith(f"{manifest.name}_"):
            tool_name = action.name
        elif namespace_prefix:
            tool_name = f"{manifest.name}_{action.name}"
        else:
            tool_name = action.name

        tool = synthesize_action_tool(
            action=action,
            pack_name=manifest.name,
            tool_name=tool_name,
            host=host,
            port=port,
        )
        tools.append(tool)

    system_prompt: str | None = None
    prompt_file = pack_dir / "system_prompt.txt"
    if prompt_file.is_file():
        try:
            system_prompt = prompt_file.read_text(encoding="utf-8").strip()
            logger.info("Loaded system prompt for pack '%s' (%d chars)", manifest.name, len(system_prompt))
        except Exception as e:
            logger.warning("Failed to read system_prompt.txt in %s: %s", pack_dir, e)

    logger.info("Loaded pack '%s' (%d actions) from %s", manifest.name, len(tools), pack_dir)
    return LoadedPack(
        manifest=manifest,
        root_dir=pack_dir,
        media_dir=media_dir,
        tools=tools,
        system_prompt=system_prompt,
    )


def discover_all_packs(
    pack_paths: list[Path],
    packs_dirs: list[Path],
    host: str = "127.0.0.1",
    port: int = 8088,
) -> dict[str, LoadedPack]:
    """Scan and load all packs from explicit pack paths and pack parent directories."""
    discovered: dict[str, LoadedPack] = {}
    candidate_paths: list[Path] = []

    # Add explicit pack paths
    for p in pack_paths:
        if p.is_dir() and p not in candidate_paths:
            candidate_paths.append(p)

    # Add subdirectories from packs directories
    for d in packs_dirs:
        if d.is_dir():
            for child in sorted(d.iterdir()):
                if child.is_dir() and (child / "pack.json").is_file() and child not in candidate_paths:
                    candidate_paths.append(child)

    for p in candidate_paths:
        try:
            pack = load_pack(p, host=host, port=port, namespace_prefix=True)
            if pack.name in discovered:
                logger.warning(
                    "Duplicate pack name '%s' from %s ignored (already loaded from %s)",
                    pack.name,
                    p,
                    discovered[pack.name].root_dir,
                )
            else:
                discovered[pack.name] = pack
        except Exception as e:
            logger.error("Failed to load pack at %s: %s", p, e)

    return discovered
