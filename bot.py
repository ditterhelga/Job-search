import os
import json
import re
import hashlib
import asyncio
import logging
from typing import Dict, List, Optional, Set, Tuple

import feedparser
import httpx
from telegram import Bot
from telegram.constants import ParseMode

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "").strip()

SEEN_FILE = "seen_jobs.json"

# Hard safety limits. Change carefully.
MAX_AI_ANALYSIS_PER_RUN = int(os.environ.get("MAX_AI_ANALYSIS_PER_RUN", "10"))
MAX_TELEGRAM_MESSAGES_PER_RUN = int(os.environ.get("MAX_TELEGRAM_MESSAGES_PER_RUN", "5"))
MAX_ITEMS_PER_SOURCE = int(os.environ.get("MAX_ITEMS_PER_SOURCE", "25"))

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
]

# Things Olga definitely does NOT want.
TITLE_SKIP_PATTERNS = [
    r"\bintern\b", r"\binternship\b", r"\bgraduate\b", r"\bstudent\b", r"\bjunior\b",
    r"\blead\b", r"\bstaff\b", r"\bprincipal\b", r"\bhead of\b", r"\bmanager\b",
    r"\bdesign manager\b", r"\bproduct design manager\b", r"\bux lead\b", r"\bdesign lead\b",
    r"\bgraphic designer\b", r"\bbrand designer\b", r"\bmarketing designer\b", r"\bvisual designer\b",
    r"\bweb designer\b", r"\bmotion designer\b", r"\billustrator\b", r"\bcontent designer\b",
    r"\bui artist\b", r"\bgame ui\b",
]

TEXT_SKIP_PATTERNS = [
    r"\bdutch required\b", r"\bfluent dutch\b", r"\bnative dutch\b", r"\bdutch speaking\b",
    r"\bgerman required\b", r"\bfluent german\b", r"\bnative german\b",
    r"\bus only\b", r"\bunited states only\b", r"\bcanada only\b",
]

# Must have at least one of these to be worth AI analysis.
RELEVANT_PATTERNS = [
    r"\bproduct designer\b",
    r"\bux designer\b",
    r"\bux/ui designer\b",
    r"\bui/ux designer\b",
    r"\binteraction designer\b",
    r"\bdigital product designer\b",
    r"\bproduct design\b",
    r"\buser experience\b",
]

# Helpful context words, used only for local scoring/logging.
BOOST_PATTERNS = [
    r"\bb2b\b", r"\bsaas\b", r"\bplatform\b", r"\bdashboard\b", r"\badmin\b",
    r"\binternal tools?\b", r"\benterprise\b", r"\bworkflow\b", r"\bfintech\b",
    r"\bpayments?\b", r"\blogistics?\b", r"\boperations?\b", r"\bmarketplace\b",
    r"\bdesign systems?\b", r"\bnetherlands\b", r"\bamsterdam\b", r"\beurope\b", r"\beu\b",
]

SYSTEM_PROMPT = """You are a careful job-fit filter for Olga, a Product Designer returning to product work after a career break.

Olga's profile:
- 10+ years in product/UX design.
- Strongest fit: complex operational systems, POS, logistics, fintech/payments, B2B SaaS, enterprise tools, dashboards, internal platforms, workflows.
- Based in Amsterdam, Netherlands. EU work authorization.
- English-speaking roles only. Minimal Dutch.
- She is open to Product Designer, UX Designer, UX/UI Designer, Digital Product Designer, Interaction Designer.
- Suitable seniority: mid-level, medior, Product Designer II, Senior Product Designer, Senior UX Designer.
- Maximum seniority: Senior.

Reject / SKIP:
- Junior, intern, graduate, student roles.
- Lead, Staff, Principal, Head of Design, Design Manager, Product Design Manager, UX Lead, Design Lead.
- Graphic design, brand design, marketing design, visual-only, web-only, motion, illustration, content design.
- Roles requiring Dutch or German.
- US-only or Canada-only roles.
- Purely consumer/mobile/social/content apps with no product complexity.

Return ONLY valid JSON. No markdown. No explanation.

Schema:
{
  "verdict": "STRONG" | "MEDIUM" | "WEAK" | "SKIP",
  "reason": "one short sentence",
  "flags": ["up to 4 short flags"]
}

Guidance:
- STRONG: product/UX role, mid-to-senior but not lead, English likely ok, Netherlands/Europe/remote Europe, B2B/SaaS/platform/internal tools/fintech/payments/logistics/operations/complex workflows.
- MEDIUM: relevant product/UX role, location and seniority acceptable, but domain fit is uncertain or only partly aligned.
- WEAK: product/UX role but mostly consumer, agency, too UI-focused, or little evidence of complex workflows.
- SKIP: any hard rejection above.
"""


def normalize_text(value: str) -> str:
    value = re.sub(r"<[^>]+>", " ", value or "")
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def matches_any(text: str, patterns: List[str]) -> Optional[str]:
    low = text.lower()
    for pattern in patterns:
        if re.search(pattern, low):
            return pattern
    return None


def local_filter(title: str, description: str) -> Tuple[bool, str, int]:
    """Return (should_send_to_ai, reason, boost_score)."""
    title_clean = normalize_text(title)
    desc_clean = normalize_text(description)
    title_low = title_clean.lower()
    all_text = f"{title_clean} {desc_clean}".lower()

    title_skip = matches_any(title_low, TITLE_SKIP_PATTERNS)
    if title_skip:
        return False, f"title skip: {title_skip}", 0

    text_skip = matches_any(all_text, TEXT_SKIP_PATTERNS)
    if text_skip:
        return False, f"text skip: {text_skip}", 0

    relevant = matches_any(all_text, RELEVANT_PATTERNS)
    if not relevant:
        return False, "no relevant product/UX keywords", 0

    boost_score = sum(1 for pattern in BOOST_PATTERNS if re.search(pattern, all_text))
    return True, "passed local filter", boost_score


def load_seen() -> Set[str]:
    if os.path.exists(SEEN_FILE):
        try:
            with open(SEEN_FILE, "r", encoding="utf-8") as f:
                return set(json.load(f))
        except Exception as e:
            logger.warning(f"Could not read {SEEN_FILE}: {e}")
    return set()


def save_seen(seen: Set[str]) -> None:
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(seen), f, ensure_ascii=False, indent=2)


def job_id(entry) -> str:
    raw = (entry.get("link", "") + "|" + entry.get("title", "")).encode("utf-8")
    return hashlib.md5(raw).hexdigest()


def extract_json(text: str) -> Dict:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.strip().lower().startswith("json"):
            text = text.strip()[4:]
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1:
        text = text[start : end + 1]
    return json.loads(text)


async def analyze_job(title: str, description: str, link: str) -> Dict:
    if not ANTHROPIC_API_KEY:
        return {
            "verdict": "MEDIUM",
            "reason": "Passed local filter; AI analysis is disabled.",
            "flags": ["Local filter", "Needs review"],
        }

    content = f"Title: {title}\nURL: {link}\n\nDescription:\n{description[:2500]}"

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
                "max_tokens": 220,
                "system": SYSTEM_PROMPT,
                "messages": [{"role": "user", "content": content}],
            },
        )
        response.raise_for_status()
        data = response.json()
        text = data["content"][0]["text"].strip()
        return extract_json(text)


def safe_markdown(text: str) -> str:
    # Telegram Markdown is fragile. Keep it simple by removing characters that commonly break formatting.
    return (text or "").replace("*", "").replace("_", " ").strip()


def format_message(entry, analysis: Dict, source_name: str) -> str:
    verdict = analysis.get("verdict", "MEDIUM")
    emoji = {"STRONG": "🟢", "MEDIUM": "🟡", "WEAK": "🔴"}.get(verdict, "")

    title = safe_markdown(entry.get("title", "No title"))
    link = entry.get("link", "")
    reason = safe_markdown(analysis.get("reason", ""))
    flags = [safe_markdown(f) for f in analysis.get("flags", [])[:4]]
    flags_text = "\n".join(f"• {f}" for f in flags if f)

    return (
        f"{emoji} *{verdict}* — {title}\n"
        f"📍 {safe_markdown(source_name)}\n"
        f"_{reason}_\n"
        f"{flags_text}\n"
        f"[Open job]({link})"
    )


async def check_feeds() -> None:
    bot = Bot(token=TELEGRAM_TOKEN)
    seen = load_seen()
    newly_seen: Set[str] = set()
    sent_count = 0
    ai_count = 0
    local_pass_count = 0
    local_skip_count = 0

    for source in RSS_SOURCES:
        if sent_count >= MAX_TELEGRAM_MESSAGES_PER_RUN:
            break

        try:
            feed = feedparser.parse(source["url"])
            logger.info(f"Checking {source['name']}: {len(feed.entries)} entries")

            candidates = []
            for entry in feed.entries[:MAX_ITEMS_PER_SOURCE]:
                jid = job_id(entry)
                if jid in seen or jid in newly_seen:
                    continue

                title = normalize_text(entry.get("title", ""))
                description = normalize_text(entry.get("summary", "") or entry.get("description", ""))
                should_analyze, reason, boost_score = local_filter(title, description)

                if not should_analyze:
                    local_skip_count += 1
                    newly_seen.add(jid)
                    logger.info(f"Local SKIP: {title[:80]} — {reason}")
                    continue

                local_pass_count += 1
                candidates.append((boost_score, entry, jid, title, description))

            # Analyze better-looking candidates first.
            candidates.sort(key=lambda item: item[0], reverse=True)

            for boost_score, entry, jid, title, description in candidates:
                if sent_count >= MAX_TELEGRAM_MESSAGES_PER_RUN:
                    break
                if ai_count >= MAX_AI_ANALYSIS_PER_RUN:
                    logger.info("AI analysis limit reached")
                    break

                link = entry.get("link", "")
                try:
                    analysis = await analyze_job(title, description, link)
                    ai_count += 1
                    newly_seen.add(jid)
                except Exception as e:
                    logger.error(f"Analysis failed for {title}: {e}")
                    continue

                verdict = analysis.get("verdict", "SKIP")
                logger.info(f"AI verdict: {verdict} — {title[:80]}")

                if verdict in {"SKIP", "WEAK"}:
                    continue

                msg = format_message(entry, analysis, source["name"])
                await bot.send_message(
                    chat_id=TELEGRAM_CHAT_ID,
                    text=msg,
                    parse_mode=ParseMode.MARKDOWN,
                    disable_web_page_preview=True,
                )
                sent_count += 1
                await asyncio.sleep(1)

        except Exception as e:
            logger.error(f"Error processing {source['name']}: {e}")

    updated_seen = seen.union(newly_seen)
    if len(updated_seen) > 3000:
        updated_seen = set(list(updated_seen)[-2000:])
    save_seen(updated_seen)

    logger.info(
        f"Done. Local passed: {local_pass_count}. Local skipped: {local_skip_count}. "
        f"AI analyzed: {ai_count}. Sent: {sent_count}. Seen total: {len(updated_seen)}."
    )


if __name__ == "__main__":
    asyncio.run(check_feeds())
