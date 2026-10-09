"""Stage 2: find the department page. Sitemap + nav hubs + subdomain probes -> scoring -> Qwen -> search.
Every candidate must pass two gates: it mentions the discipline, and it looks like a department home."""
import gzip
import re
from dataclasses import dataclass, field
from urllib.parse import unquote, urlparse

from bs4 import BeautifulSoup
from loguru import logger
from pydantic import BaseModel
from rapidfuzz import fuzz

from . import config, llm
from .http import fetch, fetch_robust
from .util import clean, extract_links, host, in_site, norm, registered_domain, root_url, tokens


@dataclass
class Dept:
    name: str
    url: str
    confidence: float
    via: str
    evidence_text: str = ""
    candidates: list = field(default_factory=list)


_SKIP_EXT = (".jpg", ".jpeg", ".png", ".gif", ".svg", ".css", ".js", ".zip", ".doc", ".docx",
             ".ppt", ".pptx", ".xls", ".xlsx", ".mp4", ".mp3", ".pdf", ".ico", ".xml")
DEPT_WORDS = {"department", "departments", "dept", "school", "schools", "division"}
PROGRAM_WORDS = {
    "btech", "mtech", "bsc", "msc", "phd", "bachelor", "bachelors", "master", "masters", "mba",
    "admission", "admissions", "syllabus", "curriculum", "program", "programme", "programs",
    "programmes", "course", "courses", "degree", "undergraduate", "postgraduate", "scholarship",
    "news", "events", "event", "placement", "placements", "alumni", "lab", "labs",
    # admissions lists / counselling pages
    "ug", "pg", "ft", "pt", "cands", "candidate", "candidates", "rank", "ranklist", "counselling",
    "counseling", "allotment", "eligibility", "fee", "fees", "intake",
}
_YEAR_RE = re.compile(r"(?<!\d)(?:19|20)\d{2}(?!\d)")
HUB_RE = re.compile(r"department|school|faculties|academics?|programmes?|programs|colleges?", re.I)

_sitemaps: dict[str, list[str]] = {}


# ---------- sitemap ----------
def _read_sitemap(url: str) -> str | None:
    p = fetch(url)
    if not p or not p.ok:
        return None
    data = p.content
    if url.endswith(".gz") or data[:2] == b"\x1f\x8b":
        try:
            data = gzip.decompress(data)
        except Exception:
            return None
    return data.decode("utf-8", errors="replace")


def get_sitemap_urls(base: str) -> list[str]:
    if base in _sitemaps:
        return _sitemaps[base]
    queue = [base + "/sitemap.xml", base + "/sitemap_index.xml"]
    robots = fetch(base + "/robots.txt")
    if robots and robots.ok:
        queue = re.findall(r"(?im)^sitemap:\s*(\S+)", robots.text) + queue
    urls, seen, fetched = [], set(), 0
    queue = list(dict.fromkeys(queue))
    while queue and fetched < 15 and len(urls) < config.MAX_SITEMAP_URLS:
        sm = queue.pop(0)
        if sm in seen:
            continue
        seen.add(sm)
        fetched += 1
        text = _read_sitemap(sm)
        if not text:
            continue
        soup = BeautifulSoup(text, "xml")
        for s in soup.find_all("sitemap"):
            loc = s.find("loc")
            if loc:
                queue.append(loc.get_text(strip=True))
        for u in soup.find_all("url"):
            loc = u.find("loc")
            if loc:
                urls.append(loc.get_text(strip=True))
    _sitemaps[base] = urls[: config.MAX_SITEMAP_URLS]
    return _sitemaps[base]


# ---------- navigation hubs ----------
def nav_links(base: str) -> list[tuple[str, str]]:
    reg = registered_domain(base)
    home = fetch_robust(base)  # real-browser fallback if the site blocks scripted requests
    if not home or not home.ok or home.is_pdf:
        return []
    links = [(u, t) for u, t in extract_links(home.text, home.url) if in_site(u, reg)]
    out = list(links)
    hubs = [u for u, t in links if HUB_RE.search(f"{t} {urlparse(u).path}")]
    for h in list(dict.fromkeys(hubs))[:4]:
        p = fetch_robust(h)
        if p and p.ok and not p.is_pdf:
            out += [(u, t) for u, t in extract_links(p.text, p.url) if in_site(u, reg)]
    return out


# ---------- discipline terms, scoring and the gates ----------
def discipline_terms(discipline: str) -> set[str]:
    d = norm(discipline)
    terms = {d}
    for key, aliases in config.DISCIPLINE_ALIASES.items():
        if fuzz.ratio(d, key) >= 85 or key in d:
            terms |= aliases | {key}
    return terms


def _depth(url: str) -> int:
    return len([s for s in urlparse(url).path.split("/") if s])


def _hay(url: str, text: str = ""):
    p = urlparse(url)
    host_toks = set(tokens(p.netloc.replace(".", " ")))
    path_toks = tokens(unquote(p.path).replace("-", " ").replace("_", " ").replace("/", " "))
    text_n = norm(text)
    hay = " ".join(list(host_toks) + path_toks + ([text_n] if text_n else []))
    return host_toks, hay, set(hay.split())


def _base(terms: set[str], hay: str, hay_toks: set[str]) -> float:
    best = 0.0
    for t in terms:
        s = fuzz.token_set_ratio(t, hay) if " " in t else (100.0 if t in hay_toks else 0.0)
        best = max(best, s)
    return best


def term_match(terms: set[str], url: str, text: str = "") -> bool:
    """Gate 1: the discipline (or a strong alias) must literally appear."""
    _, hay, toks = _hay(url, text)
    return _base(terms, hay, toks) >= 90


def _host_has_term(terms: set[str], url: str) -> bool:
    return term_match(terms, root_url(url), "")


def _host_label_has_term(terms: set[str], url: str) -> bool:
    """True if a subdomain label is the discipline or an alias: cse.x.edu, homecse.x.edu, www.cs.x.ac.uk"""
    h, reg = host(url), registered_domain(url)
    sub = h[: -(len(reg) + 1)] if reg and h.endswith(reg) else h
    labels = [l for l in sub.split(".") if l]
    for t in terms:
        if " " in t:
            continue
        for lab in labels:
            if lab == t or (len(t) >= 3 and (lab.endswith(t) or lab.startswith(t))):
                return True
    return False


def looks_like_dept_home(html: str, url: str, terms: set[str]) -> bool:
    """Gate 2: reject admissions lists, program pages and dated documents. A department home
    links to its people, or lives on the discipline's own subdomain."""
    soup = BeautifulSoup(html, "lxml")
    title = clean(soup.title.get_text()) if soup.title else ""
    h1 = clean(soup.h1.get_text(" ")) if soup.h1 else ""
    if set(tokens(f"{title} {h1}")) & PROGRAM_WORDS:
        return False
    if _YEAR_RE.search(urlparse(url).path):
        return False
    link_toks: set[str] = set()
    for u, t in extract_links(html, url):
        link_toks |= set(tokens(t))
        link_toks |= set(tokens(unquote(urlparse(u).path).replace("-", " ").replace("_", " ").replace("/", " ")))
    if link_toks & {"faculty", "people", "staff", "members"}:
        return True
    return _host_label_has_term(terms, url)


def score_candidate(terms: set[str], url: str, text: str = "", from_nav: bool = False, geo: str = "") -> float:
    host_toks, hay, hay_toks = _hay(url, text)
    base = _base(terms, hay, hay_toks)
    if base == 0:
        return 0.0
    score = base
    if hay_toks & DEPT_WORDS:
        score += 8
    if hay_toks & PROGRAM_WORDS:
        score -= 25
    if _YEAR_RE.search(urlparse(url).path):
        score -= 25
    if any((t in host_toks) if " " not in t else set(t.split()) <= host_toks for t in terms):
        score += 8  # discipline in the subdomain, e.g. cse.uni.edu
    score -= 3 * max(0, _depth(url) - 2)
    if from_nav:
        score += 4
    if geo:
        g = norm(geo)
        if g and (g in norm(url) or g in norm(text)):
            score += 15
    return max(0.0, min(100.0, score))


def _same_unit(a: str, b: str) -> bool:
    pa, pb = urlparse(a), urlparse(b)
    if pa.netloc != pb.netloc:
        return False
    x, y = pa.path.rstrip("/"), pb.path.rstrip("/")
    return x.startswith(y) or y.startswith(x)


def _units(scored):
    """Best candidate per site/section (drops near-duplicates), keeping score order."""
    out, kept = [], []
    for s in scored:
        if not any(_same_unit(s[1], k) for k in kept):
            out.append(s)
            kept.append(s[1])
    return out


def _score_all(terms, cands, geo: str = ""):
    scored = sorted(
        ((score_candidate(terms, u, t, nav, geo=geo), u, t) for u, (t, nav) in cands.items()
         if term_match(terms, u, t)),
        key=lambda x: (-x[0], _depth(x[1]), len(x[1])),
    )
    return [s for s in scored if s[0] >= 50]


# ---------- subdomain probing (cse.uni.edu, cs.uni.edu ...) ----------
def probe_subdomains(base: str, terms: set[str]) -> list[tuple[str, str]]:
    reg = registered_domain(base)
    labels = sorted({t for t in terms if " " not in t and 2 <= len(t) <= 12})[:5]
    multi = [t for t in terms if " " in t]
    out = []
    for lab in labels:
        p = fetch(f"https://{lab}.{reg}", timeout=6, retries=False)
        if not p or not p.ok or p.is_pdf or not in_site(p.url, reg):
            continue
        soup = BeautifulSoup(p.text, "lxml")
        title = clean(soup.title.get_text()) if soup.title else ""
        toks = set(tokens(title))
        if toks & DEPT_WORDS or any(set(t.split()) <= toks for t in multi):
            out.append((p.url, title))
    return out


def _hop_to_dept_site(page, terms: set[str], reg: str) -> str | None:
    """If the chosen page is a sub-page of the main site that links to the department's own
    subdomain (e.g. www.uni.edu/departments/cse -> cse.uni.edu), prefer that subdomain."""
    ph = host(page.url)
    for u, _ in extract_links(page.text, page.url):
        if not in_site(u, reg) or host(u) == ph or _depth(u) > 1:
            continue
        if u.lower().split("?")[0].endswith(_SKIP_EXT) or not _host_has_term(terms, u):
            continue
        p = fetch(root_url(u), timeout=8, retries=False)
        if p and p.ok and not p.is_pdf and in_site(p.url, reg):
            return p.url
    return None


# ---------- Qwen disambiguation ----------
class _Pick(BaseModel):
    index: int


def _llm_pick(shortlist, discipline):
    if not shortlist or not llm.available():
        return None
    lines = "\n".join(f"{i}. {u} | {t}" for i, (s, u, t) in enumerate(shortlist))
    prompt = (
        f"We need the academic DEPARTMENT home page (not a degree program, course, news item, lab, "
        f"admissions list or service centre) for the discipline: {discipline}.\nCandidates:\n{lines}\n"
        f"Reply with the index of the best department page, or -1 if none is correct."
    )
    res = llm.ask(prompt, _Pick)
    if res and 0 <= res.index < len(shortlist):
        _, u, t = shortlist[res.index]
        return u, t, 0.7, "llm"
    return None


def _pick(scored, discipline, geo: str = ""):
    if not scored:
        return None
    top = scored[0]
    rivals = [s for s in scored[1:] if top[0] - s[0] < 8 and not _same_unit(top[1], s[1])]

    # Disambiguate with geo if provided
    if geo:
        g = norm(geo)
        geo_matches = [s for s in scored if s[0] >= 60 and (g in norm(s[1]) or g in norm(s[2]))]
        if geo_matches:
            top_geo = geo_matches[0]
            other_geo_rivals = [s for s in geo_matches[1:] if top_geo[0] - s[0] < 8 and not _same_unit(top_geo[1], s[1])]
            if not other_geo_rivals:
                return top_geo[1], top_geo[2], top_geo[0] / 100, "deterministic-geo"

    # If no geo provided and top candidate has strong rivals from different units:
    if not geo and rivals and top[0] >= 75 and rivals[0][0] >= 75:
        return None, None, 0.0, "ambiguous"

    if top[0] >= 85 and not rivals:
        return top[1], top[2], top[0] / 100, "deterministic"
    choice = _llm_pick(_units(scored)[:5], discipline)
    if choice:
        return choice
    if not llm.available() and top[0] >= 70:
        return top[1], top[2], top[0] / 100 * 0.85, "deterministic-weak"
    return None


def _search_fallback(reg: str, discipline: str, terms: set[str], blocked_urls: list | None = None) -> Dept | None:
    try:
        from ddgs import DDGS
        ddg = DDGS()
        queries = [
            f"site:{reg} {discipline} department",
            f"site:{reg} \"Department of {discipline}\"",
            f"site:{reg} {discipline} faculty",
            f"site:{reg} {discipline} people",
        ]
        results = []
        for q in queries:
            results.extend(ddg.text(q, max_results=8) or [])
    except Exception as e:
        logger.debug(f"dept search failed: {e}")
        return None
    best = None
    for r in results or []:
        url = r.get("href") or r.get("url") or ""
        title = r.get("title", "")
        body = r.get("body", "") or r.get("snippet", "")
        evidence = clean(f"{title}\n{body}")
        if not url or not in_site(url, reg) or url.lower().split("?")[0].endswith(_SKIP_EXT):
            continue
        if not term_match(terms, url, evidence):
            continue
        s = score_candidate(terms, url, evidence)
        if s >= 60 and (best is None or s > best[0]):
            best = (s, url, evidence)
    if best:
        dept = _finalize(best[1], best[2], best[0] / 100 * 0.85, "search", terms, blocked_urls=blocked_urls)
        if dept:
            return dept
    return None


def _finalize(url, text, conf, via, terms, blocked_urls: list | None = None) -> Dept | None:
    page = fetch_robust(url)  # tries a real browser first; if that also fails, the original response comes back
    if not page or not page.ok or page.is_pdf:
        if page is None or (page and page.status in (403, 429)):
            if blocked_urls is not None:
                blocked_urls.append(url)
        return None
    soup = BeautifulSoup(page.text, "lxml")
    h1 = clean(soup.h1.get_text(" ")) if soup.h1 else ""
    title = clean(soup.title.get_text()).split("|")[0].strip() if soup.title else ""
    if not term_match(terms, page.url, f"{title} {h1} {text}"):
        logger.debug(f"[dept] rejected {url}: page does not mention the discipline")
        return None
    final, dept_url = page, page.url
    if _depth(page.url) >= 1 and not _host_has_term(terms, page.url):
        hop = _hop_to_dept_site(page, terms, registered_domain(page.url))
        if hop:
            hp = fetch(hop)
            if hp and hp.ok and not hp.is_pdf:
                logger.info(f"[dept] following link to the department's own site: {hop}")
                final, dept_url, via = hp, hop, via + "+site"
    if not looks_like_dept_home(final.text, final.url, terms):
        logger.info(f"[dept] rejected {final.url}: does not look like a department home page")
        return None
    name = clean(text) or h1 or title
    return Dept(name[:120], dept_url, round(min(conf, 0.95), 2), via)


def find_department(base: str, discipline: str, geo: str = "", show_candidates: bool = False) -> Dept | None:
    reg = registered_domain(base)
    terms = discipline_terms(discipline)
    cands: dict[str, tuple[str, bool]] = {}
    for u in get_sitemap_urls(base):
        if in_site(u, reg) and not u.lower().split("?")[0].endswith(_SKIP_EXT):
            cands.setdefault(u, ("", False))
    for u, t in nav_links(base):
        if not u.lower().split("?")[0].endswith(_SKIP_EXT):
            old = cands.get(u)
            if old is None or (t and not old[0]):
                cands[u] = (t, True)

    scored = _score_all(terms, cands, geo=geo)
    if not scored or scored[0][0] < 85:  # no confident candidate yet: try cse./cs./... subdomains
        for u, t in probe_subdomains(base, terms):
            cands[u] = (t, True)
        scored = _score_all(terms, cands, geo=geo)

    if show_candidates and scored:
        from rich.console import Console
        from rich.table import Table
        table = Table(title=f"Department Candidates for {discipline} ({base})")
        table.add_column("Rank", justify="right")
        table.add_column("Score", justify="right")
        table.add_column("URL")
        table.add_column("Title")
        for i, (s, u, t) in enumerate(scored[:10], 1):
            table.add_row(str(i), f"{s:.1f}", u, t[:60])
        Console().print(table)

    picked = _pick(scored, discipline, geo=geo)
    if picked and picked[3] == "ambiguous":
        return Dept("Ambiguous Department", "", 0.0, "ambiguous", candidates=scored)

    blocked_urls: list[str] = []
    dept = _finalize(*picked, terms, blocked_urls=blocked_urls) if picked else None
    if dept is None:  # first choice failed a gate: try the other strong candidates
        tried = {picked[0]} if picked and picked[0] else set()
        for s, u, t in _units(scored)[:5]:
            if u in tried or s < 85:
                continue
            dept = _finalize(u, t, s / 100, "deterministic", terms, blocked_urls=blocked_urls)
            if dept:
                break
    if dept is None:
        dept = _search_fallback(reg, discipline, terms, blocked_urls=blocked_urls)

    if dept is None and blocked_urls:
        logger.info(f"[dept] candidate department page blocked: {blocked_urls[0]}")
        return Dept("Department Page Blocked", blocked_urls[0], 0.0, "blocked", candidates=scored)

    if dept:
        dept.candidates = scored
        logger.info(f"[dept] {dept.name} -> {dept.url} ({dept.via}, {dept.confidence})")
    return dept