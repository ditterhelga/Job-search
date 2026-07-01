from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any

from config import SEEN_JOBS_FILE, FEEDBACK_FILE, TELEGRAM_OFFSET_FILE
from models import Job


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_json(path: Path, default):
    if not path.exists():
        return default
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")


class Storage:
    def __init__(self):
        self.seen: Dict[str, Any] = load_json(SEEN_JOBS_FILE, {})
        self.feedback: Dict[str, Any] = load_json(FEEDBACK_FILE, {})
        self.telegram_offset: Dict[str, Any] = load_json(TELEGRAM_OFFSET_FILE, {"offset": 0})

    def has_seen(self, job: Job) -> bool:
        return job.id in self.seen

    def mark_seen(self, job: Job, status: str, details: Dict[str, Any] | None = None) -> None:
        now = utc_now()
        old = self.seen.get(job.id, {})
        self.seen[job.id] = {
            "id": job.id,
            "short_id": job.short_id,
            "company": job.company,
            "title": job.title,
            "url": job.url,
            "source": job.source,
            "location": job.location,
            "first_seen": old.get("first_seen", now),
            "last_seen": now,
            "status": status,
            "details": details or old.get("details", {}),
        }

    def find_job_id_by_short_id(self, short_id: str) -> str | None:
        short_id = short_id.strip().lower()
        for job_id, record in self.seen.items():
            if str(record.get("short_id", "")).lower() == short_id or job_id.startswith(short_id):
                return job_id
        return None

    def add_feedback(self, short_id: str, value: str, raw_text: str = "") -> bool:
        job_id = self.find_job_id_by_short_id(short_id)
        if not job_id:
            return False
        self.feedback[job_id] = {
            "value": value,
            "updated_at": utc_now(),
            "raw_text": raw_text,
            "job": self.seen.get(job_id, {}),
        }
        return True

    def save(self) -> None:
        save_json(SEEN_JOBS_FILE, self.seen)
        save_json(FEEDBACK_FILE, self.feedback)
        save_json(TELEGRAM_OFFSET_FILE, self.telegram_offset)
