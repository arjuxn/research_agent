"""Stage 1: official website discovery. Wikidata first, search fallback, always verified."""
from dataclasses import dataclass
import re

import requests
from bs4 import BeautifulSoup
from loguru import logger
from rapidfuzz import fuzz

from . import config
from .http import fetch
from .util import _ext, clean, norm, registered_domain, root_url, tokens

WD_API = "https://www.wikidata.org/w/api.php"
WD_HEADERS = {"User-Agent": "UniContactPOC/0.1 (local research tool)"}
UNI_WORDS = ("university", "college", "institute", "school", "academy", "polytechnic")
GENERIC_WORDS = {"university", "college", "institute", "school", "deemed", "be", "academy", "polytechnic"}
# Words that mark a sub-unit's homepage (distance-ed centre, hospital, library...) rather than the main site
QUALIFIERS = {
    "distance", "online", "centre", "center", "open", "extension", "alumni", "admission", "admissions",
    "hospital", "medical", "dental", "law", "pharmacy", "hostel", "library", "press", "school",
    "faculty", "department", "research", "foundation",
    # abbreviated forms of the above seen in the wild on Indian institution sub-portals:
    # CDOE/SDE/IDE/IDOL = Centre/School/Institute of Distance (and Online) Education, DDE = Directorate
    # of Distance Education -- these never spell the words out, so the plain-word check above misses them
    "cdoe", "sde", "ide", "idol", "dde", "sdoe",
}

# Wikidata "instance of" (P31) values that mean "not an educational institution" -- used to EXCLUDE a
# name-matched candidate (e.g. a person or place that happens to share the institution's name), rather
# than to REQUIRE a specific institution type. An allowlist of valid institution-type QIDs is always
# incomplete (many real universities/deemed-university entities carry sparse or generic descriptions
# with no literal "university"/"college"/etc. wording -- an allowlist text-match on description/label
# rejects those real entities outright); an exclusion list of "definitely not this" is far safer.
NOT_INSTITUTION_QIDS = {
    "Q5",        # human
    "Q486972",   # human settlement
    "Q515",      # city
    "Q6256",     # country
    "Q532",      # village
    "Q4830453",  # business
    "Q15617994", # neighborhood
}


def is_blocked(reg: str) -> bool:
    return any(reg == b or reg.endswith("." + b) for b in config.BLOCKED_DOMAINS)


def query_name(name: str) -> str:
    """Drop '(Deemed to be University)' style noise and repeated spaces."""
    n = re.sub(r"\([^)]*\)", " ", name)
    n = re.sub(r"(?i)\bdeemed(\s+to\s+be)?\b", " ", n)
    return clean(n)


def core(name: str) -> str:
    return " ".join(t for t in tokens(name) if t not in GENERIC_WORDS)


def name_matches(name: str, text: str) -> bool:
    """True if every token of `name` is found (fuzzily, or as an acronym) in `text`."""
    nt, tt = tokens(name), tokens(text)
    if not nt or not tt:
        return False
    acronym = "".join(w[0] for w in tt)

    def present(tok: str) -> bool:
        if any(fuzz.ratio(tok, x) >= 85 for x in tt):
            return True
        return len(tok) >= 3 and tok in acronym

    return all(present(t) for t in nt)


def _penalty(name: str, text: str) -> int:
    """Number of sub-unit words in the page text that the user's name does not contain."""
    return len((set(tokens(text)) & QUALIFIERS) - set(tokens(name)))


def _site_text(root: str) -> str | None:
    page = fetch(root)
    if not page or not page.ok or page.is_pdf:
        return None
    soup = BeautifulSoup(page.text, "lxml")
    title = clean(soup.title.get_text()) if soup.title else ""
    meta = soup.find("meta", attrs={"property": "og:site_name"})
    site_name = clean(meta.get("content", "")) if meta else ""
    e = _ext(page.url)
    return f"{title} {site_name} {e.subdomain} {e.domain}"


def verify(root: str, name: str) -> bool:
    text = _site_text(root)
    return bool(text) and name_matches(name, text)


def _wd(params: dict) -> dict:
    r = requests.get(WD_API, params={**params, "format": "json"}, headers=WD_HEADERS, timeout=15)
    r.raise_for_status()
    return r.json()


def _p31_qids(claims: dict) -> set[str]:
    out = set()
    for c in claims.get("P31", []):
        try:
            out.add(c["mainsnak"]["datavalue"]["value"]["id"])
        except (KeyError, TypeError):
            continue
    return out


def _wd_search_candidates(q: str) -> list[str]:
    """One wbsearchentities call + name-similarity gate. Returns matching entity IDs."""
    try:
        hits = _wd({"action": "wbsearchentities", "search": q, "language": "en",
                    "type": "item", "limit": 8}).get("search", [])
    except Exception as e:
        logger.debug(f"wikidata search failed: {e}")
        return []
    cands = []
    for h in hits:
        label = h.get("label", "")
        matched = (h.get("match") or {}).get("text", label)  # label or alias that matched
        sim_full = max(fuzz.ratio(norm(q), norm(matched)), fuzz.ratio(norm(q), norm(label)))
        cq, cm = core(q), core(matched)
        sim_core = fuzz.ratio(cq, cm) if cq and cm else 0
        # Name similarity alone is the gate here. A description/label keyword check used to be
        # required too, but real institutions often have sparse or generic Wikidata descriptions
        # (no literal "university"/"college"/etc. -- e.g. "a group of higher educational
        # institutions" contains neither "institute" nor "institution" as the exact word checked),
        # so that check was silently rejecting correct, high-similarity matches. The actual job of
        # filtering out a same-named non-institution (a person, a town) is done more precisely
        # below, from the entity's real P31 claims, not from guessing at free-text wording.
        if sim_full >= 88 or sim_core >= 92:
            cands.append(h["id"])
    return cands


def wikidata_site(name: str) -> str | None:
    q = query_name(name)
    cands = _wd_search_candidates(q)
    if not cands:
        # wbsearchentities is a label-matching search, not a semantic one. For an institution
        # whose parent/umbrella entity's own Wikidata label omits generic words like "University"
        # (common: many are just labeled e.g. "Bharati Vidyapeeth", not "...University"), searching
        # WITH "University" appended can return nothing but its own constituent sub-colleges and
        # campuses instead -- every one of THEIR labels literally contains "University", so they
        # out-rank the parent entity in the search index, pushing it off the result page entirely.
        # Retrying with generic institution words stripped (the same `core()` already used for
        # similarity scoring, now also used as a second search query) finds the parent entity
        # directly. Only tried as a fallback, so it changes nothing for names that already work.
        stripped = core(q)
        if stripped and stripped != norm(q):
            logger.debug(f"wikidata search for '{q}' returned nothing usable; retrying as '{stripped}'")
            cands = _wd_search_candidates(stripped)
    logger.debug(f"wikidata candidates for '{q}': {cands}")
    if not cands:
        return None
    try:
        ents = _wd({"action": "wbgetentities", "ids": "|".join(cands), "props": "claims"})["entities"]
    except Exception:
        return None
    unreachable = None
    for qid in cands:
        claims = ents.get(qid, {}).get("claims", {})
        if _p31_qids(claims) & NOT_INSTITUTION_QIDS:
            logger.debug(f"wikidata candidate {qid} excluded: P31 indicates a non-institution")
            continue
        p856 = claims.get("P856", [])
        for c in sorted(p856, key=lambda c: c.get("rank") != "preferred"):
            try:
                url = c["mainsnak"]["datavalue"]["value"]
            except KeyError:
                continue
            root = root_url(url)
            if is_blocked(registered_domain(root)):
                continue
            page = fetch(root)
            if page and page.ok:
                return root
            unreachable = unreachable or root
    if unreachable:  # Wikidata is authoritative; the site may just block scripted requests
        logger.warning(f"wikidata site {unreachable} did not return a normal page; using it anyway")
        return unreachable
    return None


@dataclass
class WebsiteCandidate:
    url: str
    score: float
    source: str  # "wikidata" | "search"
    reason: str = ""

    def to_dict(self) -> dict:
        return {
            "url": self.url,
            "score": self.score,
            "source": self.source,
            "reason": self.reason,
        }


def search_candidates(name: str, country: str = "") -> list[WebsiteCandidate]:
    """Search fallback: among matching results, rank and return candidates with penalties and scores."""
    try:
        from ddgs import DDGS
        results = DDGS().text(f"{name} {country} official website".strip(), max_results=10)
    except Exception as e:
        logger.debug(f"search failed: {e}")
        return []
    seen, cands, order = set(), [], 0
    for r in results or []:
        url = r.get("href") or r.get("url")
        if not url:
            continue
        reg = registered_domain(url)
        if not reg or reg in seen or is_blocked(reg):
            continue
        seen.add(reg)
        root = root_url(url)
        text = _site_text(root)
        extra = 0.0
        if text is None:
            e = _ext(url)
            text = f"{r.get('title', '')} {e.subdomain} {e.domain}"
            extra = 0.5
        if name_matches(name, text):
            pen = _penalty(name, text) + extra
            score = round(max(0.1, 1.0 - (pen * 0.15) - (order * 0.02)), 2)
            reason = f"penalty={pen:.1f}" if pen > 0 else "main site match"
            cand = WebsiteCandidate(url=root, score=score, source="search", reason=reason)
            cands.append((pen, order, cand))
            order += 1
            if pen == 0:
                break
        if len(seen) >= 8:
            break
    cands.sort(key=lambda x: (x[0], x[1]))
    return [c[2] for c in cands]


def search_site(name: str, country: str = "") -> str | None:
    """Search fallback returning top website URL."""
    cands = search_candidates(name, country)
    return cands[0].url if cands else None


def discover_websites(name: str, country: str = "") -> list[WebsiteCandidate]:
    """Discover multiple official website candidates from Wikidata and search with scores."""
    cands: list[WebsiteCandidate] = []
    seen_urls: set[str] = set()

    site = wikidata_site(name)
    if site:
        logger.info(f"[site] {name} -> {site} (wikidata)")
        cand = WebsiteCandidate(
            url=site,
            score=1.0,
            source="wikidata",
            reason="verified Wikidata official website (P856)",
        )
        cands.append(cand)
        seen_urls.add(site)

    scands = search_candidates(query_name(name), country)
    for c in scands:
        if c.url not in seen_urls:
            cands.append(c)
            seen_urls.add(c.url)

    cands.sort(key=lambda c: -c.score)
    return cands


def resolve_website(name: str, country: str = "") -> str | None:
    cands = discover_websites(name, country)
    if cands:
        best = cands[0]
        logger.info(f"[site] {name} -> {best.url} ({best.source})")
        return best.url
    return None
