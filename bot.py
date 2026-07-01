import os
import json
import hashlib
import asyncio
import logging
from html import escape

import feedparser
import httpx
from telegram import Bot
from telegram.constants import ParseMode

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")  # optional

SEEN_FILE = "seen_jobs.json"

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
        "name": "Remotive — Design",
        "url": "https://remotive.com/remote-jobs/design.rss",
    },
    {
        "name": "Remotive — Product",
        "url": "https://remotive.com/remote-jobs/product.rss",
    },
    {
        "name": "RemoteOK — Product Designer",
        "url": "https://remoteok.com/remote-product-designer-jobs.rss",
    },
]

SYSTEM_PROMPT = """You are a job filter assistant for Olga, a Senior Product Designer with 10+ years experience.

Olga's profile:
- Specializes in complex operational systems: POS, logistics, enterprise tools, internal platforms, B2B SaaS
- Based in Amsterdam, Netherlands, EU work authorization
- Looking for Senior Product Designer roles
- Cannot work in Dutch-language environments (speaks minimal Dutch)
- Does NOT want: junior roles, graphic design, marketing design, UI-only roles, roles requiring Dutch language

Analyze the job posting and return ONLY valid JSON (no markdown, no explanation):

{
  "verdict": "STRONG" | "MEDIUM" | "WEAK" | "SKIP",
  "reason": "one sentence max",
  "dutch_required": true | false,
  "flags": ["up to 3 key points, each max 6 words"]
}

SKIP means: dutch required, junior role, graphic/marketing design, or completely irrelevant.
STRONG means: senior product designer at B2B/SaaS/enterprise/logistics/payments/internal tools company, English environment.
MEDIUM means: relevant but missing some criteria or uncertain.
WEAK means: product design but not a great fit (consumer app, wrong seniority, etc)."""


def load_seen() -> set[str]:
    if os.path.exists(SEEN_FILE):
        with open(SEEN_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    return set()


def save_seen(seen: set[str]) -> None:
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(seen), f, ensure_ascii=False, indent=2)


def job_id(entry) -> str:
    raw = (entry.get("link", "") + entry.get("title", "")).encode("utf-8")
    return hashlib.md5(raw).hexdigest()


def keyword_analysis(title: str, description: str, link: str) -> dict:
    """Free fallback: no paid AI call, only a simple but usable heuristic."""
    text = f"{title}\n{description}".lower()

    skip_terms = [
        "junior", "intern", "internship", "student", "graduate",
        "graphic designer", "visual designer", "brand designer", "marketing designer",
        "motion designer", "3d designer", "web designer", "wordpress",
        "dutch required", "fluent dutch", "native dutch", "nederlands", "vloeiend nederlands",
    ]
    if any(term in text for term in skip_terms):
        return {
            "verdict": "SKIP",
            "reason": "Likely wrong seniority, design type, or Dutch requirement.",
            "dutch_required": any(term in text for term in ["dutch", "nederlands"]),
            "flags": ["filtered by keywords"],
        }

    product_terms = ["product designer", "ux designer", "senior designer", "design lead"]
    if not any(term in text for term in product_terms):
        return {
            "verdict": "SKIP",
            "reason": "Not clearly a product design role.",
            "dutch_required": False,
            "flags": ["not product design"],
        }

    senior = any(term in text for term in ["senior", "lead", "principal", "staff"])
    strong_domain = any(term in text for term in [
        "b2b", "saas", "enterprise", "internal tools", "platform", "fintech",
        "payments", "logistics", "operations", "workflow", "dashboard", "admin",
    ])
    english_or_remote = any(term in text for term in ["remote", "europe", "emea", "english", "netherlands", "amsterdam"])

    if senior and strong_domain:
        return {
            "verdict": "STRONG",
            "reason": "Looks close to Olga's complex product systems background.",
            "dutch_required": False,
            "flags": ["senior", "complex product", "check details"],
        }

    if senior or strong_domain or english_or_remote:
        return {
            "verdict": "MEDIUM",
            "reason": "Relevant product design role, but fit needs manual checking.",
            "dutch_required": False,
            "flags": ["product design", "manual review", "possible fit"],
        }

    return {
        "verdict": "WEAK",
        "reason": "Product design role, but not clearly aligned with target profile.",
        "dutch_required": False,
        "flags": ["generic role", "check seniority"],
    }


async def analyze_with_claude(title: str, description: str, link: str) -> dict:
    content = f"Title: {title}\nURL: {link}\n\nDescription:\n{description[:3000]}"

    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-haiku-4-5-20251001",
                "max_tokens": 300,
                "system": SYSTEM_PROMPT,
                "messages": [{"role": "user", "content": content}],
            },
        )
        response.raise_for_status()
        data = response.json()
        text = data["content"][0]["text"].strip()
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        return json.loads(text)


async def analyze_job(title: str, description: str, link: str) -> dict:
    if not ANTHROPIC_API_KEY:
        return keyword_analysis(title, description, link)

    try:
        return await analyze_with_claude(title, description, link)
    except Exception as e:
        logger.error("Claude analysis failed, using keyword fallback for %s: %s", title, e)
        return keyword_analysis(title, description, link)


def format_message(entry, analysis: dict, source_name: str) -> str:
    verdict = analysis.get("verdict", "MEDIUM")
    emoji = {"STRONG": "🟢", "MEDIUM": "🟡", "WEAK": "🔴"}.get(verdict, "")

    title = escape(entry.get("title", "No title"))
    link = escape(entry.get("link", ""), quote=True)
    flags = analysis.get("flags", [])[:3]
    reason = escape(analysis.get("reason", ""))
    flags_text = "\n".join(f"• {escape(str(f))}" for f in flags) if flags else ""

    return (
        f"{emoji} <b>{escape(verdict)}</b> — {title}\n"
        f"📍 {escape(source_name)}\n"
        f"<i>{reason}</i>\n"
        f"{flags_text}\n"
        f'<a href="{link}">Open job</a>'
    )


async def check_feeds() -> None:
    bot = Bot(token=TELEGRAM_TOKEN)
    seen = load_seen()
    current_seen = set()
    sent_count = 0

    for source in RSS_SOURCES:
        try:
            feed = feedparser.parse(source["url"])
            logger.info("Checking %s: %s entries", source["name"], len(feed.entries))

            for entry in feed.entries[:30]:
                jid = job_id(entry)
                current_seen.add(jid)

                if jid in seen:
                    continue

                title = entry.get("title", "")
                description = entry.get("summary", "") or entry.get("description", "")
                link = entry.get("link", "")
                analysis = await analyze_job(title, description, link)

                if analysis.get("verdict") == "SKIP":
                    continue

                await bot.send_message(
                    chat_id=TELEGRAM_CHAT_ID,
                    text=format_message(entry, analysis, source["name"]),
                    parse_mode=ParseMode.HTML,
                    disable_web_page_preview=True,
                )
                sent_count += 1
                await asyncio.sleep(1)

        except Exception as e:
            logger.error("Error processing %s: %s", source["name"], e)

    updated_seen = seen.union(current_seen)
    if len(updated_seen) > 3000:
        updated_seen = current_seen
    save_seen(updated_seen)
    logger.info("Done. Sent %s messages.", sent_count)


if __name__ == "__main__":
    asyncio.run(check_feeds())
