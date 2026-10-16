"""Shot-level video generation job manager.

Responsibilities (V1.1 spec section 9):
* one job per shot attempt, keyed by a deterministic idempotency key;
* if a submit outcome is unknown, query status BEFORE any resubmission;
* a failure on one shot never touches other shots;
* every attempt records provider, model, is_mock, cost and the final state;
* a budget cap stops new submissions instead of silently overspending.

State is in memory here; the database-backed version comes with G0 persistence.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..adapters.base import RemoteJobState, SubmissionOutcomeUnknown, VideoGenerator
from ..shots.schema import GenerationStatus, Shot


class BudgetExceeded(RuntimeError):
    pass


@dataclass
class Attempt:
    shot_id: str
    attempt_no: int
    idempotency_key: str
    status: GenerationStatus = GenerationStatus.QUEUED
    remote_job_id: str | None = None
    output_uri: str | None = None
    error: str | None = None
    provider: str | None = None
    model_id: str | None = None
    is_mock: bool | None = None
    cost_usd: float = 0.0
    events: list[str] = field(default_factory=list)


def idempotency_key(project_id: str, version_id: str, shot_id: str, attempt_no: int) -> str:
    return f"{project_id}:{version_id}:{shot_id}:a{attempt_no}"


class VideoJobManager:
    def __init__(self, generator: VideoGenerator, budget_usd: float | None = None):
        self.generator = generator
        self.budget_usd = budget_usd
        self.attempts: dict[str, list[Attempt]] = {}

    # -- accounting -------------------------------------------------------
    def spent_usd(self) -> float:
        return round(sum(a.cost_usd for lst in self.attempts.values() for a in lst), 6)

    def _check_budget(self, estimate_usd: float) -> None:
        if self.budget_usd is not None and self.spent_usd() + estimate_usd > self.budget_usd:
            raise BudgetExceeded(f"budget {self.budget_usd} USD would be exceeded")

    # -- lifecycle --------------------------------------------------------
    def submit(self, shot: Shot, estimate_usd: float = 0.0) -> Attempt:
        self._check_budget(estimate_usd)
        history = self.attempts.setdefault(shot.shot_id, [])
        if history and history[-1].status in (GenerationStatus.SUBMITTED, GenerationStatus.RUNNING,
                                              GenerationStatus.UNKNOWN):
            raise RuntimeError(f"shot {shot.shot_id} already has an active attempt; poll it first")
        attempt_no = len(history) + 1
        att = Attempt(shot.shot_id, attempt_no, idempotency_key(shot.project_id, shot.version_id, shot.shot_id, attempt_no))
        history.append(att)
        self._do_submit(att, shot)
        return att

    def _do_submit(self, att: Attempt, shot: Shot) -> None:
        first_frame = shot.reference_asset_ids[0] if shot.reference_asset_ids else None
        try:
            ref = self.generator.submit(idempotency_key=att.idempotency_key, prompt=shot.video_prompt,
                                        first_frame_uri=first_frame, duration_s=shot.duration)
        except SubmissionOutcomeUnknown as exc:
            att.status = GenerationStatus.UNKNOWN
            att.events.append(f"submit outcome unknown: {exc}")
            return
        att.remote_job_id = ref.remote_job_id
        att.provider, att.model_id, att.is_mock = ref.meta.provider, ref.meta.model_id, ref.meta.is_mock
        att.cost_usd += ref.meta.cost_usd
        att.status = GenerationStatus.SUBMITTED
        att.events.append("submitted")

    def poll(self, shot: Shot) -> Attempt:
        att = self.attempts[shot.shot_id][-1]
        if att.status in (GenerationStatus.SUCCEEDED, GenerationStatus.FAILED):
            return att
        st = self.generator.status(idempotency_key=att.idempotency_key, remote_job_id=att.remote_job_id)
        att.provider, att.model_id, att.is_mock = st.meta.provider, st.meta.model_id, st.meta.is_mock
        if st.state == RemoteJobState.NOT_FOUND:
            if att.status == GenerationStatus.UNKNOWN:
                # Confirmed the provider never received it: safe to submit the SAME key again.
                att.events.append("status: not found after unknown submit -> resubmitting same key")
                self._do_submit(att, shot)
            else:
                att.status = GenerationStatus.FAILED
                att.error = "provider lost the job"
            return att
        att.remote_job_id = st.remote_job_id or att.remote_job_id
        if st.state in (RemoteJobState.QUEUED, RemoteJobState.RUNNING):
            att.status = GenerationStatus.RUNNING
        elif st.state == RemoteJobState.SUCCEEDED:
            att.status, att.output_uri = GenerationStatus.SUCCEEDED, st.output_uri
        else:
            att.status, att.error = GenerationStatus.FAILED, st.error
        att.events.append(f"status: {st.state.value}")
        return att

    def latest(self, shot_id: str) -> Attempt | None:
        lst = self.attempts.get(shot_id)
        return lst[-1] if lst else None
