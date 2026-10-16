"""Unified storyboard (shot list) schema - the data contract between every model.

Gemini, OpenAI, image and video adapters all read and write this structure, so
information is never passed between models as free text. Field names follow the
V1.1 spec section 4.

Rules enforced here:
* shot_id unique and shot_order unique inside one storyboard version;
* duration > 0, start_time >= 0;
* anything a model could not confirm is listed in `confidence_or_unknown`
  instead of being guessed;
* a storyboard version is immutable once saved - edits create a new version
  (see `Storyboard.new_version`).
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SCHEMA_VERSION = "shot-schema-v1"


class GenerationMethod(str, Enum):
    TEXT_TO_VIDEO = "text_to_video"
    IMAGE_TO_VIDEO = "image_to_video"
    FIRST_LAST_FRAME = "first_last_frame"
    STILL_MOTION = "still_motion"
    REUSE_LICENSED = "reuse_licensed_asset"


class GenerationStatus(str, Enum):
    DRAFT = "draft"
    QUEUED = "queued"
    SUBMITTED = "submitted"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    UNKNOWN = "unknown"  # provider response lost/ambiguous: must poll before any resubmit


class SourceEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_video_id: str
    start_time: float = Field(ge=0)
    end_time: float = Field(gt=0)
    note: str = ""

    @model_validator(mode="after")
    def _range(self):
        if self.end_time <= self.start_time:
            raise ValueError("source_evidence.end_time must be after start_time")
        return self


class UnknownMark(BaseModel):
    model_config = ConfigDict(extra="forbid")
    field: str
    reason: str
    confidence: float | None = Field(default=None, ge=0, le=1)


class Shot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: str
    version_id: str
    shot_id: str = Field(min_length=1)
    shot_order: int = Field(ge=1)
    start_time: float = Field(ge=0)
    duration: float = Field(gt=0, le=600)
    story_description: str = ""
    character_ids: list[str] = Field(default_factory=list)
    scene_id: str | None = None
    action: str = ""
    camera: str = ""
    dialogue: str = ""
    voice_over: str = ""
    on_screen_text: list[str] = Field(default_factory=list)
    audio_cues: list[str] = Field(default_factory=list)
    image_prompt: str = ""
    video_prompt: str = ""
    audio_prompt: str = ""
    generation_method: GenerationMethod = GenerationMethod.IMAGE_TO_VIDEO
    video_model: str | None = None
    reference_asset_ids: list[str] = Field(default_factory=list)
    output_asset_ids: list[str] = Field(default_factory=list)
    generation_status: GenerationStatus = GenerationStatus.DRAFT
    source_evidence: list[SourceEvidence] = Field(default_factory=list)
    confidence_or_unknown: list[UnknownMark] = Field(default_factory=list)

    @field_validator("character_ids", "reference_asset_ids", "output_asset_ids")
    @classmethod
    def _no_duplicate_ids(cls, v: list[str]) -> list[str]:
        if len(v) != len(set(v)):
            raise ValueError("id lists must not contain duplicates")
        return v

    @model_validator(mode="after")
    def _method_needs_inputs(self):
        if self.generation_method == GenerationMethod.REUSE_LICENSED and not self.reference_asset_ids:
            raise ValueError("reuse_licensed_asset requires at least one reference_asset_id")
        return self


class Storyboard(BaseModel):
    """One immutable version of a project's storyboard."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = SCHEMA_VERSION
    project_id: str
    version_id: str
    parent_version_id: str | None = None
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    created_by: str = "system"
    shots: list[Shot] = Field(min_length=1)

    @model_validator(mode="after")
    def _consistency(self):
        ids = [s.shot_id for s in self.shots]
        if len(ids) != len(set(ids)):
            raise ValueError("shot_id must be unique within a storyboard version")
        orders = [s.shot_order for s in self.shots]
        if len(orders) != len(set(orders)):
            raise ValueError("shot_order must be unique within a storyboard version")
        for s in self.shots:
            if s.project_id != self.project_id or s.version_id != self.version_id:
                raise ValueError(f"shot {s.shot_id} belongs to a different project/version")
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError(f"unsupported schema_version {self.schema_version!r}")
        return self

    def ordered(self) -> list[Shot]:
        return sorted(self.shots, key=lambda s: s.shot_order)

    def total_duration(self) -> float:
        return round(sum(s.duration for s in self.shots), 3)

    def new_version(self, version_id: str, created_by: str, shots: list[dict[str, Any]] | None = None) -> "Storyboard":
        """Return a new version; the current one is never mutated (history is not overwritten)."""
        if version_id == self.version_id:
            raise ValueError("new version must have a different version_id")
        base = [s.model_dump() for s in self.shots] if shots is None else shots
        new_shots = [{**s, "project_id": self.project_id, "version_id": version_id} for s in base]
        return Storyboard(
            project_id=self.project_id,
            version_id=version_id,
            parent_version_id=self.version_id,
            created_by=created_by,
            shots=new_shots,
        )


def storyboard_json_schema() -> dict:
    """JSON Schema for model structured output and API validation."""
    return Storyboard.model_json_schema()
