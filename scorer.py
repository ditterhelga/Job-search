from __future__ import annotations

import json
import logging
from typing import Dict, Any, List

import httpx

from config import ANTHROPIC_API_KEY, PROMPTS_DIR
from models import Job, ScoredJob

logger = logging.getLogger(__name__)


SYSTEM_PROMPT = """You are Olga's job-search analyst.

Analyze one vacancy for Olga Pertseva.

Core profile:
- Product Designer / UX Designer, 10+ years.
- Amsterdam, full EU work authorization.
- Looking for hands-on IC roles. Senior is OK. Above Senior is not suitable.
- Now in ACTIVE_SEARCH mode: optimize for interview probability, not perfect fit.
- Accept Product Designer, UX Designer, UX/UI Designer, Digital Product Designer, Interaction Designer, Experience Designer.
- Reject Junior/Intern/Graduate and Lead/Staff/Principal/Head/Manager/Director.
- Netherlands can be remote/hybrid/on-site. Outside Netherlands only remote.
- Accept Remote EU, Remote EMEA, Remote Worldwide, and remote Dubai/UAE.
- Reject Dutch-required, German-required, US-only, Canada-only, UK resident-only.
- Domain is a weak filter. SaaS, AI, fintech, edtech, HR tech, travel, productivity, marketplaces, healthcare, ecommerce, B2B and B2C can all be fine.
- Reject roles that are mostly brand, graphic, marketing, advertising, social media, illustration, motion, visual-only, or web-only.

Relevant case studies:
- Quray: hardware/software music-tech, coded prototype, new technical domain, AI-assisted workflow.
- Billfold: cashless RFID POS/payments, festivals/stadiums, high-pressure workflows, offline mode.
- Gazelkin: logistics ecosystem, drivers/dispatchers/operators, operational complexity.
- Dreamkas: POS/self-checkout, low digital literacy, retail, training and error reduction.
- Subway: training and franchise operations platforms, onboarding and operations.

Return ONLY valid JSON, no markdown.

Schema:
{
  "decision": "APPLY_IMMEDIATELY" | "APPLY_TODAY" | "SAVE_FOR_LATER" | "SKIP",
  "interview_chance": 0-100,
  "summary": "2 short sentences explaining what the company/role is",
  "green_flags": ["2-4 short flags"],
  "red_flags": ["1-4 short flags"],
  "best_case_study": "Quray" | "Billfold" | "Gazelkin" | "Dreamkas" | "Subway" | "None",
  "case_study_reason": "one short reason",
  "why_received": ["2-4 short reasons why this job was selected"]
}

Be practical and slightly conservative. Do not recommend jobs only because the title matches.
"""


def extract_json(text: str) -> Dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        parts = text.split("```")
        if len(parts) >= 2:
            text = parts[1]
            if text.strip().lower().startswith("json"):
                text = text.strip()[4:]
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1:
        text = text[start : end + 1]
    return json.loads(text)


def _list(value) -> List[str]:
    if not value:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    return [str(value).strip()]


async def score_job(job: Job) -> ScoredJob:
    user_content = f"""
Source: {job.source}
Company: {job.company}
Title: {job.title}
Location: {job.location}
Department: {job.department}
URL: {job.url}

Description:
{job.description[:2800]}
""".strip()

    async with httpx.AsyncClient(timeout=35) as client:
        resp = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-haiku-4-5-20251001",
                "max_tokens": 450,
                "system": SYSTEM_PROMPT,
                "messages": [{"role": "user", "content": user_content}],
            },
        )
        resp.raise_for_status()
        data = resp.json()
        text = data["content"][0]["text"]
        parsed = extract_json(text)

    decision = str(parsed.get("decision", "SKIP")).strip().upper()
    if decision not in {"APPLY_IMMEDIATELY", "APPLY_TODAY", "SAVE_FOR_LATER", "SKIP"}:
        decision = "SKIP"

    try:
        chance = int(parsed.get("interview_chance", 0))
    except Exception:
        chance = 0
    chance = max(0, min(100, chance))

    return ScoredJob(
        job=job,
        decision=decision,
        interview_chance=chance,
        summary=str(parsed.get("summary", "")).strip(),
        green_flags=_list(parsed.get("green_flags"))[:4],
        red_flags=_list(parsed.get("red_flags"))[:4],
        best_case_study=str(parsed.get("best_case_study", "None")).strip(),
        case_study_reason=str(parsed.get("case_study_reason", "")).strip(),
        why_received=_list(parsed.get("why_received"))[:4],
    )
