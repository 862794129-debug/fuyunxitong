"""Deterministic mock adapters for development and tests.

Every result is marked is_mock=True and provider="mock". Mock output proves the
pipeline wiring only; it says nothing about real model quality, cost or latency.
"""
from __future__ import annotations

import hashlib
import math

from .base import (
    AnalysisResult,
    CallMeta,
    ImageGenerator,
    ImageResult,
    RemoteJobState,
    SubmissionOutcomeUnknown,
    VideoAnalyzer,
    VideoGenerator,
    VideoJobRef,
    VideoJobStatus,
)


def _meta(model: str) -> CallMeta:
    return CallMeta(provider="mock", model_id=model, is_mock=True, cost_usd=0.0, latency_ms=0)


class MockVideoAnalyzer(VideoAnalyzer):
    """Splits the video into equal shots of `shot_seconds`; marks content as unknown."""

    def __init__(self, shot_seconds: float = 5.0):
        if shot_seconds <= 0:
            raise ValueError("shot_seconds must be > 0")
        self.shot_seconds = shot_seconds

    def analyze(self, *, project_id, version_id, source_video_id, video_uri, duration_s):
        if duration_s <= 0:
            raise ValueError("duration_s must be > 0")
        n = max(1, math.ceil(duration_s / self.shot_seconds))
        shots = []
        for i in range(n):
            start = round(i * self.shot_seconds, 3)
            dur = round(min(self.shot_seconds, duration_s - start), 3)
            shots.append({
                "project_id": project_id,
                "version_id": version_id,
                "shot_id": f"s{i + 1:03d}",
                "shot_order": i + 1,
                "start_time": start,
                "duration": dur,
                "story_description": "[mock] not analysed",
                "source_evidence": [{"source_video_id": source_video_id, "start_time": start,
                                     "end_time": round(start + dur, 3), "note": "mock equal split"}],
                "confidence_or_unknown": [{"field": "story_description", "reason": "mock adapter, no real analysis"}],
            })
        sb = {"project_id": project_id, "version_id": version_id, "created_by": "mock-analyzer", "shots": shots}
        return AnalysisResult(meta=_meta("mock-analyzer"), storyboard=sb)


class MockImageGenerator(ImageGenerator):
    def generate(self, *, prompt, reference_uris, width, height):
        digest = hashlib.sha256(f"{prompt}|{'|'.join(reference_uris)}|{width}x{height}".encode()).hexdigest()[:16]
        return ImageResult(meta=_meta("mock-image"), uri=f"mock://image/{digest}.png", width=width, height=height)


class MockVideoGenerator(VideoGenerator):
    """In-memory async video provider.

    `fail_submit_ambiguously` simulates a timeout AFTER the provider accepted the job:
    the job exists remotely but the caller never got the id. This is exactly the case
    where blind resubmission would pay twice.
    """

    def __init__(self, polls_until_done: int = 1, fail_submit_ambiguously: int = 0, fail_jobs: bool = False):
        self.polls_until_done = polls_until_done
        self._ambiguous_left = fail_submit_ambiguously
        self.fail_jobs = fail_jobs
        self.jobs: dict[str, dict] = {}  # idempotency_key -> job
        self.submit_calls = 0
        self.accepted_jobs = 0

    def submit(self, *, idempotency_key, prompt, first_frame_uri, duration_s):
        self.submit_calls += 1
        if idempotency_key in self.jobs:  # provider-side idempotency
            job = self.jobs[idempotency_key]
            return VideoJobRef(meta=_meta("mock-video"), remote_job_id=job["id"], idempotency_key=idempotency_key)
        job_id = "job-" + hashlib.sha256(idempotency_key.encode()).hexdigest()[:12]
        self.jobs[idempotency_key] = {"id": job_id, "polls": 0}
        self.accepted_jobs += 1
        if self._ambiguous_left > 0:
            self._ambiguous_left -= 1
            raise SubmissionOutcomeUnknown("mock: response lost after provider accepted the job")
        return VideoJobRef(meta=_meta("mock-video"), remote_job_id=job_id, idempotency_key=idempotency_key)

    def status(self, *, idempotency_key, remote_job_id=None):
        job = self.jobs.get(idempotency_key)
        if job is None:
            return VideoJobStatus(meta=_meta("mock-video"), state=RemoteJobState.NOT_FOUND)
        job["polls"] += 1
        if job["polls"] < self.polls_until_done:
            return VideoJobStatus(meta=_meta("mock-video"), state=RemoteJobState.RUNNING, remote_job_id=job["id"])
        if self.fail_jobs:
            return VideoJobStatus(meta=_meta("mock-video"), state=RemoteJobState.FAILED,
                                  remote_job_id=job["id"], error="mock failure")
        return VideoJobStatus(meta=_meta("mock-video"), state=RemoteJobState.SUCCEEDED,
                              remote_job_id=job["id"], output_uri=f"mock://video/{job['id']}.mp4")
