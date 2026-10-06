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
        temp_dir: Any = None,
    ) -> None:
        self.manifest = manifest
        self.root_dir = root_dir
        self.media_dir = media_dir
        self.tools: list[SynthesizedTool] = tools or []
        self.system_prompt = system_prompt
        self._temp_dir = temp_dir

    @property
    def name(self) -> str:
        return self.manifest.name

    def __repr__(self) -> str:
        return f"<LoadedPack name={self.name!r} actions={len(self.manifest.actions)} path={self.root_dir}>"
