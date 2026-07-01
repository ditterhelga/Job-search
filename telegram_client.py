from __future__ import annotations

import logging
import re
from typing import List, Tuple

from telegram import Bot

from config import TELEGRAM_CHAT_ID, TELEGRAM_TOKEN
from models import ScoredJob
from storage import Storage

logger = logging.getLogger(__name__)


FEEDBACK_RE = re.compile(r"^(👍|👎|\+|-|relevant|not relevant|no|yes)\s*([a-f0-9]{4,16})", re.I)


def _clean(text: str) -> str:
    return (text or "").replace("<", "").replace(">", "").strip()


def format_report(scored_jobs: List[ScoredJob], stats: dict) -> str:
    lines: List[str] = []
    lines.append("Job search report")
    lines.append("")
    lines.append(f"Checked: {stats.get('checked', 0)}")
    lines.append(f"New after history: {stats.get('new_after_history', 0)}")
    lines.append(f"Passed local filter: {stats.get('local_passed', 0)}")
    lines.append(f"Analyzed with Claude: {stats.get('ai_analyzed', 0)}")
    lines.append(f"Recommended: {len(scored_jobs)}")
    lines.append("")

    if not scored_jobs:
        lines.append("No strong recommendations this run.")
        lines.append("This is not a failure. It usually means the local filter did its job.")
        return "\n".join(lines)

    for idx, item in enumerate(scored_jobs, 1):
        job = item.job
        lines.append(f"{idx}. {job.title} — {job.company}")
        lines.append(f"ID: {job.short_id}")
        lines.append(f"Source: {job.source}")
        if job.location:
            lines.append(f"Location: {_clean(job.location)}")
        lines.append(f"Action: {item.decision.replace('_', ' ').title()}")
        lines.append(f"Interview chance: {item.interview_chance}%")
        lines.append("")
        if item.summary:
            lines.append("What it is:")
            lines.append(_clean(item.summary))
            lines.append("")
        if item.green_flags:
            lines.append("Green flags:")
            for flag in item.green_flags[:4]:
                lines.append(f"+ {_clean(flag)}")
            lines.append("")
        if item.red_flags:
            lines.append("Red flags:")
            for flag in item.red_flags[:4]:
                lines.append(f"- {_clean(flag)}")
            lines.append("")
        if item.best_case_study and item.best_case_study != "None":
            lines.append(f"Best case study: {item.best_case_study}")
            if item.case_study_reason:
                lines.append(_clean(item.case_study_reason))
            lines.append("")
        if item.why_received:
            lines.append("Why you received this:")
            for reason in item.why_received[:4]:
                lines.append(f"+ {_clean(reason)}")
            lines.append("")
        lines.append(f"Open job: {job.url}")
        lines.append("")
        lines.append(f"Reply: 👍 {job.short_id} or 👎 {job.short_id}")
        lines.append("----------------")

    return "\n".join(lines).strip()


def split_message(text: str, limit: int = 3900) -> List[str]:
    if len(text) <= limit:
        return [text]
    chunks: List[str] = []
    current: List[str] = []
    current_len = 0
    for block in text.split("----------------"):
        block = block.strip()
        if not block:
            continue
        block = block + "\n----------------\n"
        if current_len + len(block) > limit and current:
            chunks.append("".join(current).strip())
            current = []
            current_len = 0
        current.append(block)
        current_len += len(block)
    if current:
        chunks.append("".join(current).strip())
    return chunks


async def send_report(scored_jobs: List[ScoredJob], stats: dict) -> None:
    bot = Bot(token=TELEGRAM_TOKEN)
    text = format_report(scored_jobs, stats)
    for chunk in split_message(text):
        await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=chunk, disable_web_page_preview=True)


async def ingest_feedback(storage: Storage) -> Tuple[int, int]:
    """Read Telegram messages since last offset and store simple feedback.

    Users can send: 👍 abc123 or 👎 abc123.
    The feedback is processed on the next manual run.
    """
    bot = Bot(token=TELEGRAM_TOKEN)
    offset = int(storage.telegram_offset.get("offset", 0) or 0)
    updates = await bot.get_updates(offset=offset, timeout=1, allowed_updates=["message"])
    processed = 0
    matched = 0
    max_update_id = offset - 1

    for update in updates:
        if update.update_id is not None:
            max_update_id = max(max_update_id, update.update_id)
        msg = update.message
        if not msg or not msg.text:
            continue
        processed += 1
        text = msg.text.strip()
        m = FEEDBACK_RE.match(text)
        if not m:
            continue
        symbol, short_id = m.group(1).lower(), m.group(2).lower()
        value = "relevant" if symbol in {"👍", "+", "relevant", "yes"} else "not_relevant"
        if storage.add_feedback(short_id, value, raw_text=text):
            matched += 1

    if updates:
        storage.telegram_offset["offset"] = max_update_id + 1
    logger.info("Telegram feedback: processed=%s matched=%s", processed, matched)
    return processed, matched
