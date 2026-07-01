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

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]

SEEN_FILE = "seen_jobs.json"

# Strict safety limits. Keep low while tuning.
MAX_AI_ANALYSIS_PER_RUN = int(os.environ.get("MAX_AI_ANALYSIS_PER_RUN", "6"))
MAX_TELEGRAM_MESSAGES_PER_RUN = int(os.environ.get("MAX_TELEGRAM_MESSAGES_PER_RUN", "3"))
MAX_ITEMS_PER_SOURCE = int(os.environ.get("MAX_ITEMS_PER_SOURCE", "20"))

# Only these verdicts will be sent.
SEND_VERDICTS = {"STRONG"}

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

# Hard title rejects before AI. Maximum seniority is Senior.
TITLE_SKIP_PATTERNS = [
    r"\bintern\b", r"\binternship\b", r"\bgraduate\b", r"\bstudent\b", r"\bjunior\b",
    r"\blead\b", r"\bstaff\b", r"\bprincipal\b", r"\bhead\b", r"\bmanager\b", r"\bdirector\b",
    r"\bvp\b", r"\bchief\b", r"\bowner\b",
    r"\bgraphic designer\b", r"\bbrand designer\b", r"\bmarketing designer\b", r"\bvisual designer\b",
    r"\bweb designer\b", r"\bmotion designer\b", r"\billustrator\b", r"\bcontent designer\b",
    r"\bui artist\b", r"\bgame ui\b", r"\bcreative designer\b",
]

# Hard content rejects before AI.
TEXT_SKIP_PATTERNS = [
    r"\bdutch required\b", r"\bfluent dutch\b", r"\bnative dutch\b", r"\bdutch speaking\b",
    r"\bgerman required\b", r"\bfluent german\b", r"\bnative german\b", r"\bgerman speaking\b",
    r"\bus only\b", r"\bunited states only\b", r"\bcanada only\b", r"\bnorth america only\b",
    r"\bmust be based in the us\b", r"\bmust be based in united states\b",
]

# The role itself must match one of these. Do not analyze random product/PM/design roles.
RELEVANT_TITLE_PATTERNS = [
    r"\bproduct designer\b",
    r"\bsenior product designer\b",
    r"\bux designer\b",
    r"\bsenior ux designer\b",
    r"\bux/ui designer\b",
    r"\bui/ux designer\b",
    r"\binteraction designer\b",
    r"\bdigital product designer\b",
    r"\bproduct design(er)? ii\b",
]

# Strong-fit product context. Without at least one, the role is probably too generic.
STRONG_CONTEXT_PATTERNS = [
    r"\bb2b\b", r"\bsaas\b", r"\bplatform\b", r"\bdashboard\b", r"\badmin\b",
    r"\binternal tools?\b", r"\benterprise\b", r"\bworkflow\b", r"\bworkflows\b",
    r"\bfintech\b", r"\bpayments?\b", r"\blogistics?\b", r"\boperations?\b",
    r"\bmarketplace\b", r"\bdesign systems?\b", r"\bautomation\b", r"\bcomplex\b",
]

# Helpful location/context. Not required before AI, but boosts priority.
LOCATION_CONTEXT_PATTERNS = [
    r"\bnetherlands\b", r"\bamsterdam\b", r"\beurope\b", r"\beu\b", r"\bemea\b", r"\bremote worldwide\b", r"\bworldwide\b",
]

SYSTEM_PROMPT = """You are a strict job-fit filter for Olga, a Product Designer returning to product work after a career break.

Olga's profile:
- 10+ years in product/UX design.
- Strongest fit: complex operational systems, POS, logistics, fintech/payments, B2B SaaS, enterprise tools, dashboards, internal platforms, workflows.
- Based in Amsterdam, Netherlands. EU work authorization.
- English-speaking roles only. Minimal Dutch.
- Suitable roles: Product Designer, UX Designer, UX/UI Designer, Digital Product Designer, Interaction Designer.
- Suitable seniority: mid-level, medior, Product Designer II, Senior Product Designer, Senior UX Designer.
- Maximum seniority: Senior. Anything above Senior is not suitable.

Hard SKIP:
- Junior, intern, graduate, student.
- Lead, Staff, Principal, Head of Design, Design Manager, Product Design Manager, UX Lead, Design Lead, Director, VP.
- Graphic design, brand design, marketing design, visual-only, web-only, motion, illustration, content design.
- Roles requiring Dutch or German.
- US-only, Canada-only, North-America-only, or unclear remote location when Europe/Netherlands is not allowed.
- Product roles that include heavy brand, marketing, landing pages, growth marketing, social media, ads, or visual content.
- Purely consumer/mobile/social/content apps with no complex product workflows.

Be strict. Do not send roles just because the title says Product Designer.

Return ONLY valid JSON. No markdown. No explanation.

Schema:
{
  "verdict": "STRONG" | "MEDIUM" | "WEAK" | "SKIP",
  "reason": "one short sentence",
  "flags": ["up to 4 short flags"]
}

Guidance:
- STRONG only if it is clearly a realistic fit: Product/UX role, not above Senior, English likely OK, Netherlands/Europe/remote Europe/worldwide allowed, and strong domain match: B2B/SaaS/platform/internal tools/fintech/payments/logistics/operations/complex workflows.
- MEDIUM if relevant but uncertain. MEDIUM will not be sent to Olga.
- WEAK if mostly consumer, agency, UI-focused, marketing-heavy, or little evidence of complex workflows.
- SKIP for any hard rejection.
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


def count_matches(text: str, patterns: List[str]) -> int:
    low = text.lower()
    return sum(1 for pattern in patterns if re.search(pattern, low))


def local_filter(title: str, description: str) -> Tuple[bool, str, int]:
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

    relevant_title = matches_any(title_low, RELEVANT_TITLE_PATTERNS)
    if not relevant_title:
        return False, "title is not a Product/UX Designer role", 0

    strong_context = count_matches(all_text, STRONG_CONTEXT_PATTERNS)
    location_context = count_matches(all_text, LOCATION_CONTEXT_PATTERNS)

    # Avoid wasting Claude on very generic product designer posts with no signal.
    if strong_context == 0:
        return False, "no strong domain/workflow signal", 0

    score = strong_context * 2 + location_context
    return True, "passed strict local filter", score


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
    content = f"Title: {title}\nURL: {link}\n\nDescription:\n{description[:2200]}"

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
                "max_tokens": 180,
                "system": SYSTEM_PROMPT,
                "messages": [{"role": "user", "content": content}],
            },
        )
        response.raise_for_status()
        data = response.json()
        text = data["content"][0]["text"].strip()
        return extract_json(text)


def clean_text(text: str) -> str:
    return (text or "").replace("*", "").replace("_", " ").strip()


def format_message(entry, analysis: Dict, source_name: str) -> str:
    verdict = analysis.get("verdict", "STRONG")
    emoji = {"STRONG": "🟢", "MEDIUM": "🟡", "WEAK": "🔴"}.get(verdict, "")

    title = clean_text(entry.get("title", "No title"))
    link = entry.get("link", "")
    reason = clean_text(analysis.get("reason", ""))
    flags = [clean_text(f) for f in analysis.get("flags", [])[:4]]
    flags_text = "\n".join(f"• {f}" for f in flags if f)

    return (
        f"{emoji} {verdict} — {title}\n"
        f"📍 {clean_text(source_name)}\n"
        f"{reason}\n"
        f"{flags_text}\n"
        f"Open job: {link}"
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
                should_analyze, reason, score = local_filter(title, description)

                if not should_analyze:
                    local_skip_count += 1
                    newly_seen.add(jid)
                    logger.info(f"Local SKIP: {title[:90]} — {reason}")
                    continue

                local_pass_count += 1
                candidates.append((score, entry, jid, title, description))

            candidates.sort(key=lambda item: item[0], reverse=True)

            for score, entry, jid, title, description in candidates:
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
                logger.info(f"AI verdict: {verdict} — {title[:90]}")

                if verdict not in SEND_VERDICTS:
                    continue

                msg = format_message(entry, analysis, source["name"])
                await bot.send_message(
                    chat_id=TELEGRAM_CHAT_ID,
                    text=msg,
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
        f"AI analyzed: {ai_count}. Sent: {sent_count}. Seen total this run: {len(updated_seen)}."
    )


if __name__ == "__main__":
    asyncio.run(check_feeds())
