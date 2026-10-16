"""Provider-neutral adapter interfaces.

Every model the system uses sits behind one of these interfaces so a provider can
be swapped (or a Claude-skill manual import can stand in) without touching the
pipeline. Every result carries `is_mock`, `provider`, `model_id`, `cost_usd` and
`latency_ms` so that cost per shot is always recorded and a mock result can never
be reported as a real generation.

Real adapters (Gemini, OpenAI, Nano Banana 2, Seedance via Volcano Ark) are NOT
implemented yet. Their exact model IDs, parameters and pricing must be taken from
the official docs at implementation time, not guessed.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class AdapterError(RuntimeError):
    """Provider call failed in a known way (safe to report and retry later)."""


class SubmissionOutcomeUnknown(AdapterError):
    """The submit request may or may not have reached the provider (timeout, dropped
    connection). The caller MUST query job status with the same idempotency key
    before resubmitting, otherwise a paid job may run twice."""


class RemoteJobState(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    NOT_FOUND = "not_found"


@dataclass(frozen=True)
class CallMeta:
    provider: str
    model_id: str
    is_mock: bool
    cost_usd: float = 0.0
    latency_ms: int = 0


@dataclass(frozen=True)
class AnalysisResult:
    meta: CallMeta
    storyboard: dict[str, Any]          # must validate as shots.schema.Storyboard
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ImageResult:
    meta: CallMeta
    uri: str
    width: int
    height: int


@dataclass(frozen=True)
class VideoJobRef:
    meta: CallMeta
    remote_job_id: str
    idempotency_key: str


@dataclass(frozen=True)
class VideoJobStatus:
    meta: CallMeta
    state: RemoteJobState
    remote_job_id: str | None = None
    output_uri: str | None = None
    error: str | None = None


class VideoAnalyzer(ABC):
    """Visual/audio understanding of a reference video (spec: Gemini)."""

    @abstractmethod
    def analyze(self, *, project_id: str, version_id: str, source_video_id: str,
                video_uri: str, duration_s: float) -> AnalysisResult: ...


class ImageGenerator(ABC):
    """Character/scene/product references and shot first frames (spec: Nano Banana 2)."""

    @abstractmethod
    def generate(self, *, prompt: str, reference_uris: list[str], width: int, height: int) -> ImageResult: ...


class VideoGenerator(ABC):
    """Asynchronous shot video generation (spec: Seedance 2.0 / 2.5 / 2.0 Fast)."""

    @abstractmethod
    def submit(self, *, idempotency_key: str, prompt: str, first_frame_uri: str | None,
               duration_s: float) -> VideoJobRef: ...

    @abstractmethod
    def status(self, *, idempotency_key: str, remote_job_id: str | None = None) -> VideoJobStatus:
        """Look a job up by idempotency key (and remote id if known)."""
