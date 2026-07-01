import os
import json
import hashlib
import asyncio
import logging
from datetime import datetime
import feedparser
import httpx
from telegram import Bot
from telegram.constants import ParseMode
from apscheduler.schedulers.asyncio import AsyncIOScheduler

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]

SEEN_FILE = "seen_jobs.json"

# RSS sources that work reliably
RSS_SOURCES = [
    {
        "name": "Wellfound (AngelList)",
        "url": "https://wellfound.com/role/r/product-designer/netherlands.rss",
    },
    {
        "name": "RemoteOK",
        "url": "https://remoteok.com/remote-product-designer-jobs.rss",
    },
    {
        "name": "We Work Remotely",
        "url": "https://weworkremotely.com/categories/remote-design-jobs.rss",
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


def load_seen():
    if os.path.exists(SEEN_FILE):
        with open(SEEN_FILE) as f:
            return set(json.load(f))
    return set()


def save_seen(seen):
    with open(SEEN_FILE, "w") as f:
        json.dump(list(seen), f)


def job_id(entry):
    return hashlib.md5((entry.get("link", "") + entry.get("title", "")).encode()).hexdigest()


async def analyze_job(title: str, description: str, link: str) -> dict:
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
        data = response.json()
        text = data["content"][0]["text"].strip()
        # Strip markdown code blocks if present
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        return json.loads(text)


def format_message(entry, analysis: dict, source_name: str) -> str:
    verdict = analysis["verdict"]
    emoji = {"STRONG": "🟢", "MEDIUM": "🟡", "WEAK": "🔴"}.get(verdict, "")
    
    title = entry.get("title", "No title")
    link = entry.get("link", "")
    flags = analysis.get("flags", [])
    reason = analysis.get("reason", "")

    flags_text = "\n".join(f"• {f}" for f in flags) if flags else ""

    return (
        f"{emoji} *{verdict}* — {title}\n"
        f"📍 {source_name}\n"
        f"_{reason}_\n"
        f"{flags_text}\n"
        f"[Open job]({link})"
    )


async def check_feeds():
    bot = Bot(token=TELEGRAM_TOKEN)
    seen = load_seen()
    new_seen = set()
    found_any = False

    for source in RSS_SOURCES:
        try:
            feed = feedparser.parse(source["url"])
            logger.info(f"Checking {source['name']}: {len(feed.entries)} entries")
            
            for entry in feed.entries[:20]:  # check last 20 per source
                jid = job_id(entry)
                new_seen.add(jid)
                
                if jid in seen:
                    continue

                title = entry.get("title", "")
                description = entry.get("summary", "") or entry.get("description", "")
                link = entry.get("link", "")

                try:
                    analysis = await analyze_job(title, description, link)
                except Exception as e:
                    logger.error(f"Analysis failed for {title}: {e}")
                    continue

                if analysis["verdict"] == "SKIP":
                    continue

                msg = format_message(entry, analysis, source["name"])
                await bot.send_message(
                    chat_id=TELEGRAM_CHAT_ID,
                    text=msg,
                    parse_mode=ParseMode.MARKDOWN,
                    disable_web_page_preview=True,
                )
                found_any = True
                await asyncio.sleep(1)  # avoid rate limiting

        except Exception as e:
            logger.error(f"Error processing {source['name']}: {e}")

    # Update seen: keep old + new (avoid growing forever, keep last 2000)
    updated_seen = seen.union(new_seen)
    if len(updated_seen) > 2000:
        updated_seen = new_seen  # reset to current batch
    save_seen(updated_seen)

    if not found_any:
        logger.info("No new relevant jobs found this cycle")


async def main():
    logger.info("Bot starting...")
    
    # Run once immediately on startup
    await check_feeds()

    # Then schedule every 4 hours
    scheduler = AsyncIOScheduler()
    scheduler.add_job(check_feeds, "interval", hours=4)
    scheduler.start()

    logger.info("Scheduler running, checking every 4 hours")
    
    # Keep running
    while True:
        await asyncio.sleep(3600)


if __name__ == "__main__":
    asyncio.run(main())
