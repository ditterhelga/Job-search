from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional
import hashlib
import re


def normalize_text(value: str | None) -> str:
    value = value or ""
    value = re.sub(r"<[^>]+>", " ", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()


@dataclass
class Job:
    source: str
    title: str
    company: str
    url: str
    description: str = ""
    location: str = ""
    department: str = ""
    raw: Dict = field(default_factory=dict)

    @property
    def id(self) -> str:
        raw = f"{self.source}|{self.company}|{self.title}|{self.url}".lower().encode("utf-8")
        return hashlib.sha256(raw).hexdigest()[:16]

    @property
    def short_id(self) -> str:
        return self.id[:6]

    @property
    def text(self) -> str:
        return normalize_text(" ".join([self.title, self.company, self.location, self.department, self.description]))


@dataclass
class LocalDecision:
    should_analyze: bool
    reason: str
    score: int = 0


@dataclass
class ScoredJob:
    job: Job
    decision: str
    interview_chance: int
    summary: str
    green_flags: List[str]
    red_flags: List[str]
    best_case_study: str
    case_study_reason: str
    why_received: List[str]

    @property
    def should_send(self) -> bool:
        return self.decision in {"APPLY_IMMEDIATELY", "APPLY_TODAY", "SAVE_FOR_LATER"}
