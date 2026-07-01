from __future__ import annotations

import re
from typing import List, Optional

from models import Job, LocalDecision


TITLE_HARD_SKIP = [
    r"\bintern\b", r"\binternship\b", r"\bgraduate\b", r"\bstudent\b", r"\bjunior\b",
    r"\blead\b", r"\bstaff\b", r"\bprincipal\b", r"\bhead\b", r"\bmanager\b", r"\bdirector\b",
    r"\bvp\b", r"\bchief\b", r"\bowner\b",
    r"\bgraphic designer\b", r"\bbrand designer\b", r"\bmarketing designer\b", r"\bvisual designer\b",
    r"\bweb designer\b", r"\bmotion designer\b", r"\billustrator\b", r"\bcontent designer\b",
    r"\bui artist\b", r"\bgame ui\b", r"\bcreative designer\b", r"\bsocial media designer\b",
]

TEXT_HARD_SKIP = [
    r"\bdutch required\b", r"\bfluent dutch\b", r"\bnative dutch\b", r"\bdutch speaking\b",
    r"\bgerman required\b", r"\bfluent german\b", r"\bnative german\b", r"\bgerman speaking\b",
    r"\bus only\b", r"\bunited states only\b", r"\bcanada only\b", r"\bnorth america only\b",
    r"\bmust be based in the us\b", r"\bmust be based in united states\b", r"\buk resident only\b",
    r"\brelocation required\b",
]

RELEVANT_TITLE = [
    r"\bproduct designer\b",
    r"\bsenior product designer\b",
    r"\bux designer\b",
    r"\bsenior ux designer\b",
    r"\bux/ui designer\b",
    r"\bui/ux designer\b",
    r"\binteraction designer\b",
    r"\bdigital product designer\b",
    r"\bexperience designer\b",
    r"\bproduct design(er)? ii\b",
]

DOMAIN_BOOST = [
    r"\bb2b\b", r"\bb2c\b", r"\bsaas\b", r"\bplatform\b", r"\bdashboard\b", r"\badmin\b",
    r"\binternal tools?\b", r"\benterprise\b", r"\bworkflow\b", r"\bworkflows\b",
    r"\bfintech\b", r"\bpayments?\b", r"\blogistics?\b", r"\boperations?\b",
    r"\bmarketplace\b", r"\bdesign systems?\b", r"\bautomation\b", r"\bcomplex\b",
    r"\bedtech\b", r"\bhr tech\b", r"\btravel\b", r"\bproductivity\b", r"\bhealthcare\b",
    r"\bdeveloper tools?\b", r"\be-?commerce\b", r"\bai\b", r"\bmachine learning\b",
]

LOCATION_BOOST = [
    r"\bnetherlands\b", r"\bamsterdam\b", r"\beurope\b", r"\beu\b", r"\bemea\b",
    r"\bremote worldwide\b", r"\bworldwide\b", r"\bremote\b", r"\bdubai\b", r"\buae\b",
]

NEGATIVE_LOCATION_OUTSIDE_NL = [
    r"\bhybrid\b", r"\bon[- ]?site\b", r"\boffice[- ]?based\b",
]

OUTSIDE_NL_COUNTRIES = [
    "germany", "belgium", "france", "spain", "portugal", "austria", "switzerland",
    "denmark", "sweden", "finland", "ireland", "uae", "dubai",
]


def _match_any(text: str, patterns: List[str]) -> Optional[str]:
    text = text.lower()
    for pattern in patterns:
        if re.search(pattern, text):
            return pattern
    return None


def _count(text: str, patterns: List[str]) -> int:
    text = text.lower()
    return sum(1 for pattern in patterns if re.search(pattern, text))


def local_filter(job: Job) -> LocalDecision:
    title = job.title.lower()
    text = job.text.lower()

    title_skip = _match_any(title, TITLE_HARD_SKIP)
    if title_skip:
        return LocalDecision(False, f"title hard skip: {title_skip}", 0)

    text_skip = _match_any(text, TEXT_HARD_SKIP)
    if text_skip:
        return LocalDecision(False, f"text hard skip: {text_skip}", 0)

    if not _match_any(title, RELEVANT_TITLE):
        return LocalDecision(False, "not a Product/UX Designer title", 0)

    # Outside NL, hybrid/on-site is not acceptable unless the text also explicitly says remote.
    has_nl = any(w in text for w in ["netherlands", "amsterdam", "utrecht", "rotterdam", "haarlem", "hoofddorp", "hilversum", "den haag", "the hague"])
    has_outside_country = any(country in text for country in OUTSIDE_NL_COUNTRIES)
    has_remote = "remote" in text or "work from anywhere" in text or "worldwide" in text or "emea" in text or "europe" in text
    if has_outside_country and not has_nl and _match_any(text, NEGATIVE_LOCATION_OUTSIDE_NL) and not has_remote:
        return LocalDecision(False, "hybrid/on-site outside Netherlands", 0)

    score = 10
    score += _count(text, DOMAIN_BOOST) * 2
    score += _count(text, LOCATION_BOOST) * 2

    if "senior" in title:
        score += 3
    if "product designer" in title:
        score += 4
    if "ux designer" in title:
        score += 3
    if job.source.lower() in {"greenhouse", "lever", "ashby"}:
        score += 3

    return LocalDecision(True, "passed local filter", score)
