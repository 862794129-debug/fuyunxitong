"""Rule-based compliance pre-check for ad scripts and shot lists.

IMPORTANT design limits (keep these visible to every user of this module):
* This is a pre-check aid, not a compliance approval. The verdict never says
  "compliant"; it says what was found. A named human compliance owner must
  approve content before it is produced or submitted.
* The shipped rule list is a STARTER list that has not been calibrated on the
  team's real rejection data and is not legal advice.
* It detects risky wording. It does not try to help content slip past
  platform review; suggestions always push toward removing the claim.
* Text only. Visual issues (sensitive body imagery, etc.) need a separate
  post-generation visual check that does not exist yet.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable

DEFAULT_RULES_PATH = Path(__file__).parent / "rules" / "supplements_v0.json"

# Shot-JSON fields that carry viewer-facing or generation-driving text.
SCANNED_FIELDS = (
    "story_description",
    "dialogue",
    "voice_over",
    "on_screen_text",
    "image_prompt",
    "video_prompt",
    "audio_prompt",
)

SEVERITIES = ("red", "yellow")

VERDICT_BLOCK = "block_recommended"
VERDICT_REVIEW = "needs_review"
VERDICT_NONE = "no_issues_found"

VERDICT_NOTE = (
    "Not a compliance approval. 'no_issues_found' only means this starter rule list "
    "matched nothing in the text. A human compliance owner must approve; visual "
    "content is not checked."
)


class RulesError(ValueError):
    """Raised when a rules file is malformed."""


@dataclass(frozen=True)
class Finding:
    rule_id: str
    category: str
    severity: str
    shot_id: str
    field: str
    matched_text: str
    start: int
    end: int
    message: str
    suggestion: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class CheckResult:
    content_id: str
    rules_version: str
    checked_at: str
    verdict: str
    findings: list[Finding] = field(default_factory=list)
    note: str = VERDICT_NOTE
    human_approval_required: bool = True

    def to_dict(self) -> dict:
        d = asdict(self)
        d["findings"] = [f.to_dict() for f in self.findings]
        return d

    def categories(self) -> list[str]:
        return sorted({f.category for f in self.findings})


@dataclass(frozen=True)
class _Rule:
    id: str
    category: str
    severity: str
    message: str
    suggestion: str
    patterns: tuple[re.Pattern, ...]


@dataclass(frozen=True)
class _ContextRule:
    id: str
    category: str
    severity: str
    message: str
    suggestion: str
    trigger: tuple[re.Pattern, ...]
    absent: tuple[re.Pattern, ...]


class RuleSet:
    def __init__(self, data: dict):
        self.version = str(data["rules_version"])
        self.categories = tuple(data["categories"])
        self.rules: list[_Rule] = []
        self.context_rules: list[_ContextRule] = []
        seen: set[str] = set()
        flags = re.IGNORECASE | re.UNICODE
        for r in data.get("rules", []):
            self._validate(r, seen)
            try:
                pats = tuple(re.compile(p, flags) for p in r["patterns"])
            except re.error as exc:
                raise RulesError(f"rule {r['id']}: bad regex: {exc}") from exc
            self.rules.append(
                _Rule(r["id"], r["category"], r["severity"], r["message"], r["suggestion"], pats)
            )
        for r in data.get("context_rules", []):
            self._validate(r, seen)
            try:
                trig = tuple(re.compile(p, flags) for p in r["trigger_patterns"])
                absent = tuple(re.compile(p, flags) for p in r["absent_patterns"])
            except re.error as exc:
                raise RulesError(f"rule {r['id']}: bad regex: {exc}") from exc
            self.context_rules.append(
                _ContextRule(r["id"], r["category"], r["severity"], r["message"], r["suggestion"], trig, absent)
            )

    def _validate(self, r: dict, seen: set[str]) -> None:
        for key in ("id", "category", "severity", "message", "suggestion"):
            if key not in r:
                raise RulesError(f"rule missing '{key}': {r}")
        if r["id"] in seen:
            raise RulesError(f"duplicate rule id {r['id']}")
        seen.add(r["id"])
        if r["severity"] not in SEVERITIES:
            raise RulesError(f"rule {r['id']}: severity must be one of {SEVERITIES}")
        if r["category"] not in self.categories:
            raise RulesError(f"rule {r['id']}: unknown category {r['category']!r}")

    @classmethod
    def load(cls, path: Path | str = DEFAULT_RULES_PATH) -> "RuleSet":
        with open(path, encoding="utf-8") as fh:
            return cls(json.load(fh))


def _shots_from_input(payload) -> list[dict]:
    """Accept a plain string, a single shot dict, a list of shots, or {'shots': [...]}."""
    if isinstance(payload, str):
        return [{"shot_id": "text", "story_description": payload}]
    if isinstance(payload, dict):
        if "shots" in payload:
            return _shots_from_input(payload["shots"])
        return [payload]
    if isinstance(payload, list):
        shots = []
        for i, item in enumerate(payload, 1):
            if not isinstance(item, dict):
                raise ValueError(f"shot #{i} must be an object, got {type(item).__name__}")
            shots.append(item)
        return shots
    raise ValueError(f"unsupported input type: {type(payload).__name__}")


def _iter_texts(shot: dict) -> Iterable[tuple[str, str]]:
    for name in SCANNED_FIELDS:
        value = shot.get(name)
        if value is None:
            continue
        if isinstance(value, str):
            if value.strip():
                yield name, value
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, str) and item.strip():
                    yield name, item
        else:
            raise ValueError(f"field {name!r} must be text or a list of text, got {type(value).__name__}")


class ComplianceChecker:
    def __init__(self, rules: RuleSet | None = None, clock: Callable[[], datetime] | None = None):
        self.rules = rules or RuleSet.load()
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def check(self, payload, content_id: str = "unnamed") -> CheckResult:
        shots = _shots_from_input(payload)
        findings: list[Finding] = []
        all_text_parts: list[str] = []

        for idx, shot in enumerate(shots, 1):
            shot_id = str(shot.get("shot_id", f"shot-{idx}"))
            for fname, text in _iter_texts(shot):
                all_text_parts.append(text)
                for rule in self.rules.rules:
                    for pat in rule.patterns:
                        for m in pat.finditer(text):
                            findings.append(
                                Finding(
                                    rule.id, rule.category, rule.severity, shot_id, fname,
                                    m.group(0), m.start(), m.end(), rule.message, rule.suggestion,
                                )
                            )

        findings = _dedupe(findings)

        # Context rules look at the whole script (e.g. benefit wording with no responsible wording).
        full_text = "\n".join(all_text_parts)
        for cr in self.rules.context_rules:
            if full_text and any(p.search(full_text) for p in cr.trigger) and not any(
                p.search(full_text) for p in cr.absent
            ):
                trig = next(m for p in cr.trigger if (m := p.search(full_text)))
                findings.append(
                    Finding(cr.id, cr.category, cr.severity, "*script*", "*all*",
                            trig.group(0), trig.start(), trig.end(), cr.message, cr.suggestion)
                )

        if any(f.severity == "red" for f in findings):
            verdict = VERDICT_BLOCK
        elif findings:
            verdict = VERDICT_REVIEW
        else:
            verdict = VERDICT_NONE

        return CheckResult(
            content_id=content_id,
            rules_version=self.rules.version,
            checked_at=self._clock().isoformat(),
            verdict=verdict,
            findings=findings,
        )


def _dedupe(findings: list[Finding]) -> list[Finding]:
    """Same rule, same location, same span counts once (patterns inside a rule may overlap)."""
    seen: set[tuple] = set()
    out: list[Finding] = []
    for f in findings:
        key = (f.rule_id, f.shot_id, f.field, f.start, f.end)
        if key not in seen:
            seen.add(key)
            out.append(f)
    return out
