"""Data models for Action Packs and manifests."""

from __future__ import annotations

import random
from pathlib import Path
from typing import Any, Callable, Literal
from pydantic import BaseModel, Field, field_validator


class ActionParameter(BaseModel):
    """Schema definition for an action tool parameter."""

    type: Literal["string", "number", "integer", "boolean"] = "string"
    description: str = ""
    required: bool = False
    default: Any | None = None


class ActionDefinition(BaseModel):
    """Definition of an individual action inside an Action Pack."""

    name: str = Field(..., description="Action tool identifier (e.g. 'attack', 'dance')")
    description: str = Field("", description="Description provided to the LLM for tool selection")
    media: str | list[str] = Field(..., description="Referenced media filename or list of filenames")
    selection: Literal["first", "random"] = Field("first", description="Strategy when multiple media files are provided")
    alt: str = Field("", description="Alt text for the image markdown embed")
    parameters: dict[str, ActionParameter] = Field(default_factory=dict, description="Input parameters for the action")
    template: str = Field("", description="Optional message template. Can include {media} and {parameter_name}")

    @field_validator("media")
    @classmethod
    def validate_media_not_empty(cls, v: str | list[str]) -> str | list[str]:
        if isinstance(v, list) and not v:
            raise ValueError("Media list cannot be empty")
        if isinstance(v, str) and not v.strip():
            raise ValueError("Media filename cannot be empty")
        return v

    def get_media_files(self) -> list[str]:
        """Return all media filenames referenced by this action."""
        if isinstance(self.media, list):
            return self.media
        return [self.media]

    def pick_media_file(self) -> str:
        """Select a media filename based on the selection strategy."""
        files = self.get_media_files()
        if self.selection == "random" and len(files) > 1:
            return random.choice(files)
        return files[0]


class PackManifest(BaseModel):
    """Manifest schema for pack.json."""

    name: str = Field(..., description="Unique name/identifier for the pack")
    version: str = Field("1.0.0", description="Semantic version of the pack")
    description: str = Field("", description="Human-readable description of the pack")
    actions: list[ActionDefinition] = Field(default_factory=list, description="List of actions provided by this pack")


class SynthesizedTool(BaseModel):
    """Represents a dynamically synthesized MCP tool callable and its metadata."""

    name: str
    description: str
    fn: Any = Field(exclude=True)


class LoadedPack:
    """Runtime representation of a loaded action pack."""

    def __init__(
        self,
        manifest: PackManifest,
        root_dir: Path,
        media_dir: Path,
        tools: list[SynthesizedTool] | None = None,
        system_prompt: str | None = None,
        prompts: dict[str, str] | None = None,
        active_prompt: str = "default",
        source_path: Path | None = None,
        is_encrypted: bool = False,
        passkey: str | None = None,
        temp_dir: Any = None,
    ) -> None:
        self.manifest = manifest
        self.root_dir = root_dir
        self.media_dir = media_dir
        self.tools: list[SynthesizedTool] = tools or []
        self._temp_dir = temp_dir
        self.source_path = source_path
        self.is_encrypted = is_encrypted
        self.passkey = passkey

        self.prompts: dict[str, str] = dict(prompts) if prompts else {}
        if system_prompt and "default" not in self.prompts:
            self.prompts["default"] = system_prompt

        if self.prompts:
            self.active_prompt = active_prompt if active_prompt in self.prompts else next(iter(self.prompts))
        else:
            self.prompts["default"] = ""
            self.active_prompt = "default"

    @property
    def system_prompt(self) -> str | None:
        return self.prompts.get(self.active_prompt) or (next(iter(self.prompts.values()), None) if self.prompts else None)

    @system_prompt.setter
    def system_prompt(self, val: str | None) -> None:
        if val is not None:
            self.prompts[self.active_prompt] = val
        elif self.active_prompt in self.prompts:
            del self.prompts[self.active_prompt]

    def save_prompt(self, name: str, content: str, set_active: bool = True) -> None:
        """Save or create a prompt file under prompts/ and persist to archive if needed."""
        from core.archive import create_pack_archive, is_archive_file

        name = name.strip()
        if not name:
            raise ValueError("Prompt name cannot be empty")

        self.prompts[name] = content
        if set_active:
            self.active_prompt = name

        prompts_dir = self.root_dir / "prompts"
        prompts_dir.mkdir(parents=True, exist_ok=True)
        (prompts_dir / f"{name}.txt").write_text(content, encoding="utf-8")

        if self.source_path and is_archive_file(self.source_path):
            create_pack_archive(
                pack_dir=self.root_dir,
                output_file=self.source_path,
                encrypt=self.is_encrypted,
                passkey=self.passkey,
            )

    def delete_prompt(self, name: str) -> bool:
        """Delete a prompt file from prompts/ and persist to archive if needed."""
        from core.archive import create_pack_archive, is_archive_file

        if name not in self.prompts:
            return False
        if len(self.prompts) <= 1:
            raise ValueError("Cannot delete the only remaining prompt")

        del self.prompts[name]
        if self.active_prompt == name:
            self.active_prompt = "default" if "default" in self.prompts else next(iter(self.prompts), "")

        prompt_file = self.root_dir / "prompts" / f"{name}.txt"
        if prompt_file.is_file():
            prompt_file.unlink()

        if self.source_path and is_archive_file(self.source_path):
            create_pack_archive(
                pack_dir=self.root_dir,
                output_file=self.source_path,
                encrypt=self.is_encrypted,
                passkey=self.passkey,
            )
        return True

    def set_active_prompt(self, name: str) -> bool:
        """Set the active prompt name."""
        if name in self.prompts:
            self.active_prompt = name
            return True
        return False

    @property
    def name(self) -> str:
        return self.manifest.name

    def __repr__(self) -> str:
        return f"<LoadedPack name={self.name!r} actions={len(self.manifest.actions)} prompts={list(self.prompts.keys())} path={self.root_dir}>"
