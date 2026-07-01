from __future__ import annotations

import json
import logging
from typing import List, Dict, Any

import feedparser
import httpx

from config import (
    COMPANY_BOARDS_FILE,
    MAX_ITEMS_PER_ATS_BOARD,
    MAX_ITEMS_PER_RSS_SOURCE,
    USE_ASHBY,
    USE_GREENHOUSE,
    USE_LEVER,
    USE_RSS,
)
from models import Job, normalize_text

logger = logging.getLogger(__name__)

RSS_SOURCES = [
    {
        "name": "We Work Remotely — Design",
        "url": "https://weworkremotely.com/categories/remote-design-jobs.rss",
    },
    {
        "name": "We Work Remotely — Product",
        "url": "https://weworkremotely.com/categories/remote-product-jobs.rss",
    },
    {
        "name": "RemoteOK — Product Designer",
        "url": "https://remoteok.com/remote-product-designer-jobs.rss",
    },
    {
        "name": "Remotive — Design",
        "url": "https://remotive.com/remote-jobs/rss-feed?category=design",
    },
    {
        "name": "Remotive — Product",
        "url": "https://remotive.com/remote-jobs/rss-feed?category=product",
    },
]


def _company_from_title(title: str) -> str:
    # RSS feeds use inconsistent title formats. Keep this conservative.
    for sep in [" at ", " @ ", " – ", " - "]:
        if sep in title:
            parts = title.split(sep)
            if len(parts) >= 2:
                return normalize_text(parts[-1])[:80]
    return "Unknown"


async def fetch_rss_jobs() -> List[Job]:
    if not USE_RSS:
        return []

    jobs: List[Job] = []
    for source in RSS_SOURCES:
        try:
            feed = feedparser.parse(source["url"])
            logger.info("RSS %s: %s entries", source["name"], len(feed.entries))
            for entry in feed.entries[:MAX_ITEMS_PER_RSS_SOURCE]:
                title = normalize_text(entry.get("title", ""))
                if not title:
                    continue
                jobs.append(
                    Job(
                        source=source["name"],
                        title=title,
                        company=_company_from_title(title),
                        url=entry.get("link", ""),
                        description=normalize_text(entry.get("summary", "") or entry.get("description", "")),
                        location=normalize_text(entry.get("location", "") or entry.get("tags", "")),
                        raw=dict(entry),
                    )
                )
        except Exception as exc:
            logger.warning("RSS source failed %s: %s", source["name"], exc)
    return jobs


def load_company_boards() -> Dict[str, List[Dict[str, str]]]:
    if not COMPANY_BOARDS_FILE.exists():
        return {"greenhouse": [], "lever": [], "ashby": []}
    with COMPANY_BOARDS_FILE.open("r", encoding="utf-8") as f:
        return json.load(f)


async def fetch_greenhouse_board(client: httpx.AsyncClient, company: str, slug: str) -> List[Job]:
    urls = [
        f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true",
        f"https://api.greenhouse.io/v1/boards/{slug}/jobs?content=true",
    ]
    last_error = None
    for url in urls:
        try:
            resp = await client.get(url)
            if resp.status_code == 404:
                continue
            resp.raise_for_status()
            data = resp.json()
            jobs = []
            for item in data.get("jobs", [])[:MAX_ITEMS_PER_ATS_BOARD]:
                offices = item.get("offices", []) or []
                location = ", ".join(o.get("name", "") for o in offices if o.get("name"))
                departments = item.get("departments", []) or []
                department = ", ".join(d.get("name", "") for d in departments if d.get("name"))
                jobs.append(
                    Job(
                        source="Greenhouse",
                        company=company,
                        title=normalize_text(item.get("title", "")),
                        url=item.get("absolute_url", url),
                        description=normalize_text(item.get("content", "")),
                        location=normalize_text(location),
                        department=normalize_text(department),
                        raw=item,
                    )
                )
            logger.info("Greenhouse %s: %s jobs", company, len(jobs))
            return jobs
        except Exception as exc:
            last_error = exc
    logger.info("Greenhouse %s skipped: %s", company, last_error or "not found")
    return []


async def fetch_lever_board(client: httpx.AsyncClient, company: str, slug: str) -> List[Job]:
    url = f"https://api.lever.co/v0/postings/{slug}?mode=json"
    try:
        resp = await client.get(url)
        if resp.status_code == 404:
            logger.info("Lever %s skipped: 404", company)
            return []
        resp.raise_for_status()
        data = resp.json()
        jobs = []
        for item in data[:MAX_ITEMS_PER_ATS_BOARD]:
            categories = item.get("categories", {}) or {}
            location = normalize_text(categories.get("location", ""))
            commitment = normalize_text(categories.get("commitment", ""))
            team = normalize_text(categories.get("team", ""))
            desc = "\n".join([
                item.get("descriptionPlain", "") or item.get("description", ""),
                item.get("additionalPlain", "") or item.get("additional", ""),
            ])
            jobs.append(
                Job(
                    source="Lever",
                    company=company,
                    title=normalize_text(item.get("text", "")),
                    url=item.get("hostedUrl", item.get("applyUrl", url)),
                    description=normalize_text(desc),
                    location=location or commitment,
                    department=team,
                    raw=item,
                )
            )
        logger.info("Lever %s: %s jobs", company, len(jobs))
        return jobs
    except Exception as exc:
        logger.info("Lever %s skipped: %s", company, exc)
        return []


async def fetch_ashby_board(client: httpx.AsyncClient, company: str, slug: str) -> List[Job]:
    url = f"https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true"
    try:
        resp = await client.get(url)
        if resp.status_code == 404:
            logger.info("Ashby %s skipped: 404", company)
            return []
        resp.raise_for_status()
        data = resp.json()
        postings = data.get("jobs", []) or data.get("jobPostings", []) or []
        jobs = []
        for item in postings[:MAX_ITEMS_PER_ATS_BOARD]:
            title = item.get("title", "") or item.get("jobTitle", "")
            location = item.get("locationName", "") or item.get("location", "")
            department = item.get("departmentName", "") or item.get("department", "")
            description = item.get("descriptionHtml", "") or item.get("descriptionPlain", "") or item.get("description", "")
            job_url = item.get("jobUrl", "") or item.get("url", "") or f"https://jobs.ashbyhq.com/{slug}"
            jobs.append(
                Job(
                    source="Ashby",
                    company=company,
                    title=normalize_text(title),
                    url=job_url,
                    description=normalize_text(description),
                    location=normalize_text(str(location)),
                    department=normalize_text(str(department)),
                    raw=item,
                )
            )
        logger.info("Ashby %s: %s jobs", company, len(jobs))
        return jobs
    except Exception as exc:
        logger.info("Ashby %s skipped: %s", company, exc)
        return []


async def fetch_ats_jobs() -> List[Job]:
    boards = load_company_boards()
    jobs: List[Job] = []
    headers = {"User-Agent": "OlgaJobSearchBot/1.0"}
    async with httpx.AsyncClient(timeout=20, headers=headers, follow_redirects=True) as client:
        if USE_GREENHOUSE:
            for board in boards.get("greenhouse", []):
                jobs.extend(await fetch_greenhouse_board(client, board["company"], board["slug"]))
        if USE_LEVER:
            for board in boards.get("lever", []):
                jobs.extend(await fetch_lever_board(client, board["company"], board["slug"]))
        if USE_ASHBY:
            for board in boards.get("ashby", []):
                jobs.extend(await fetch_ashby_board(client, board["company"], board["slug"]))
    return jobs


async def fetch_all_jobs() -> List[Job]:
    rss_jobs = await fetch_rss_jobs()
    ats_jobs = await fetch_ats_jobs()
    all_jobs = rss_jobs + ats_jobs

    # Deduplicate by URL first. Same job can come from multiple feeds.
    unique: Dict[str, Job] = {}
    for job in all_jobs:
        key = (job.url or f"{job.company}|{job.title}").lower()
        if key and key not in unique:
            unique[key] = job
    logger.info("Fetched %s jobs, %s unique", len(all_jobs), len(unique))
    return list(unique.values())
