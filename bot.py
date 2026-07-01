from __future__ import annotations

import asyncio
import logging
from typing import Dict, List, Tuple

from config import MAX_AI_ANALYSIS_PER_RUN, MAX_RESULTS_TO_SEND, MIN_INTERVIEW_CHANCE_TO_SEND
from filters import local_filter
from models import Job, ScoredJob
from scorer import score_job
from sources import fetch_all_jobs
from storage import Storage
from telegram_client import ingest_feedback, send_report

logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
logger = logging.getLogger(__name__)


def sort_candidates(candidates: List[Tuple[int, Job]]) -> List[Tuple[int, Job]]:
    return sorted(candidates, key=lambda item: item[0], reverse=True)


def sort_scored(scored: List[ScoredJob]) -> List[ScoredJob]:
    decision_weight = {
        "APPLY_IMMEDIATELY": 3,
        "APPLY_TODAY": 2,
        "SAVE_FOR_LATER": 1,
        "SKIP": 0,
    }
    return sorted(
        scored,
        key=lambda item: (decision_weight.get(item.decision, 0), item.interview_chance),
        reverse=True,
    )


async def main() -> None:
    storage = Storage()

    await ingest_feedback(storage)
    all_jobs = await fetch_all_jobs()

    stats: Dict[str, int] = {
        "checked": len(all_jobs),
        "already_seen": 0,
        "new_after_history": 0,
        "local_passed": 0,
        "local_skipped": 0,
        "ai_analyzed": 0,
        "ai_skipped": 0,
    }

    candidates: List[Tuple[int, Job]] = []

    for job in all_jobs:
        if storage.has_seen(job):
            stats["already_seen"] += 1
            continue

        stats["new_after_history"] += 1
        decision = local_filter(job)

        if not decision.should_analyze:
            stats["local_skipped"] += 1
            logger.info("Local skip: %s — %s", job.title[:90], decision.reason)
            continue

        stats["local_passed"] += 1
        candidates.append((decision.score, job))

    scored: List[ScoredJob] = []
    for _score, job in sort_candidates(candidates)[:MAX_AI_ANALYSIS_PER_RUN]:
        try:
            result = await score_job(job)
            stats["ai_analyzed"] += 1
            logger.info("AI: %s %s%% — %s", result.decision, result.interview_chance, job.title[:90])
            storage.mark_seen(
                job,
                "ai_scored",
                {
                    "decision": result.decision,
                    "interview_chance": result.interview_chance,
                    "summary": result.summary,
                },
            )
            if result.should_send and result.interview_chance >= MIN_INTERVIEW_CHANCE_TO_SEND:
                scored.append(result)
            else:
                stats["ai_skipped"] += 1
        except Exception as exc:
            logger.exception("AI failed for %s: %s", job.title, exc)

    scored = sort_scored(scored)[:MAX_RESULTS_TO_SEND]
    for item in scored:
        storage.mark_seen(
            item.job,
            "shown",
            {
                "decision": item.decision,
                "interview_chance": item.interview_chance,
                "summary": item.summary,
                "best_case_study": item.best_case_study,
            },
        )

    await send_report(scored, stats)
    storage.save()

    logger.info("Done: %s", stats)


if __name__ == "__main__":
    asyncio.run(main())
