"""One university + discipline -> one Result row. Shared by all three modes."""
import re

from loguru import logger

from . import config
from .confidence import score
from .contacts import best_contact, extract_html, extract_pdf, finalize, llm_contacts
from .debug import DebugTrace
from .department import find_department, get_sitemap_urls
from .faculty import find_faculty_pages
from .http import fetch, fetch_rendered
from .models import Result
from .util import registered_domain, root_url
from .website import discover_websites, resolve_website


def _looks_js(html: str) -> bool:
    visible = re.sub(r"<script.*?</script>|<style.*?</style>|<[^>]+>", " ", html, flags=re.S)
    return "<script" in html and len(visible.strip()) < 1500


def _extract(url: str, reg: str, trace: DebugTrace | None = None):
    page = fetch(url)
    if not page or not page.ok:
        if trace:
            trace.add("contact_page", "fetch_failed", url=url)
        return [], ""
    if page.is_pdf:
        contacts, text = extract_pdf(page.content, page.url, reg)
        if trace:
            trace.add("contact_page", "extracted", url=page.url, kind="pdf", contacts=len(contacts),
                      text_chars=len(text))
        return contacts, text
    contacts, text = extract_html(page.text, page.url, reg)
    if not contacts and config.USE_PLAYWRIGHT and _looks_js(page.text):
        rendered = fetch_rendered(page.url)  # None if Playwright/Chromium is missing
        if rendered:
            contacts, text = extract_html(rendered.text, rendered.url, reg)
            if trace:
                trace.add("contact_page", "rendered_extracted", url=rendered.url, kind="html",
                          contacts=len(contacts), text_chars=len(text))
            return contacts, text
    if trace:
        trace.add("contact_page", "extracted", url=page.url, kind="html", contacts=len(contacts),
                  text_chars=len(text))
    return contacts, text


def _run(res: Result, website: str, trace: DebugTrace, show_candidates: bool = False) -> Result:
    if website.strip():
        site = website.strip()
        site = root_url(site if site.startswith("http") else "https://" + site)
        trace.add("website", "provided", url=site)
    else:
        cands = discover_websites(res.university_name, res.country)
        trace.add("website", "candidates", candidates=[c.to_dict() for c in cands])
        if show_candidates and cands:
            from rich.console import Console
            from rich.table import Table
            table = Table(title=f"Website Candidates for {res.university_name}")
            table.add_column("Rank", justify="right")
            table.add_column("Score", justify="right")
            table.add_column("URL")
            table.add_column("Source")
            table.add_column("Reason")
            for i, c in enumerate(cands, 1):
                table.add_row(str(i), f"{c.score:.2f}", c.url, c.source, c.reason)
            Console().print(table)
        site = cands[0].url if cands else None

    if not site:
        res.status = "Website Not Found"
        res.error = "Stage website: no official website candidate passed verification"
        trace.add("website", "not_found")
        return res
    res.college_website = site
    trace.add("website", "selected", url=site, source="input" if website.strip() else "resolver")

    dept = find_department(site, res.discipline, geo=res.geo, show_candidates=show_candidates)
    if dept and dept.via == "ambiguous":
        res.status = "Ambiguous Department"
        res.error = "Stage department: multiple department candidates found across campuses without disambiguating geo"
        trace.add("department", "ambiguous", candidates=[{"score": s, "url": u, "title": t} for s, u, t in (dept.candidates or [])])
        return res

    if dept and dept.via == "blocked":
        res.status = "Department Page Blocked"
        res.department_url = dept.url
        res.error = f"Stage department: candidate department page ({dept.url}) was blocked or unreachable"
        trace.add("department", "blocked", url=dept.url, candidates=[{"score": s, "url": u, "title": t} for s, u, t in (dept.candidates or [])])
        return res

    if not dept or dept.confidence < config.DEPT_MIN_CONFIDENCE:
        if dept:
            logger.info(f"[dept] rejected {dept.url}: confidence {dept.confidence} below "
                        f"{config.DEPT_MIN_CONFIDENCE}")
            trace.add("department", "rejected_low_confidence", url=dept.url, confidence=dept.confidence,
                      minimum=config.DEPT_MIN_CONFIDENCE, via=dept.via)
            res.error = (f"Stage department: best candidate below confidence threshold "
                         f"({dept.confidence} < {config.DEPT_MIN_CONFIDENCE})")
        else:
            trace.add("department", "not_found", site=site, discipline=res.discipline)
            res.error = "Stage department: no page looked like the requested discipline department"
        res.status = "Field Not Found"
        return res
    res.teaching_requested_field = "YES"
    res.target_department, res.department_url = dept.name, dept.url
    trace.add("department", "selected", name=dept.name, url=dept.url, confidence=dept.confidence,
              via=dept.via)

    reg = registered_domain(site)
    pages = find_faculty_pages(dept.url, get_sitemap_urls(site))
    trace.add("faculty_pages", "selected", pages=pages)
    contacts, texts, best = [], [], None
    for url in pages:
        found, text = _extract(url, reg, trace)
        contacts = finalize(contacts + found, reg)
        if text:
            texts.append((url, text))
        best = best_contact(contacts)
        if best and best.rank <= 1 and best.email:
            break  # head/chair with a bound email: done

    if best is None:  # deterministic extraction found nothing -> Qwen (guarded + validated)
        trace.add("llm_fallback", "attempted", pages=[url for url, _ in texts[:2]])
        contacts = finalize(contacts + llm_contacts(texts, reg), reg)
        best = best_contact(contacts)

    if not best:
        res.status = "Contact Not Found"
        res.error = "Stage contact: no validated faculty/head/chair contact found"
        trace.add("contact", "not_found", contacts_seen=len(contacts), pages=[u for u, _ in texts])
        return res

    logger.info(f"[contact] {best.name} | {best.role} | {best.method} | {best.source_url}")
    trace.add("contact", "selected", name=best.name, role=best.role, email=best.email,
              method=best.method, source_url=best.source_url, profile_url=best.profile_url)
    res.contact_type, res.contact_name = best.role, best.name
    res.email, res.phone, res.office = best.email, best.phone, best.office
    res.profile_url, res.source_url, res.research_area = best.profile_url, best.source_url, best.research_area
    res.extraction_method = best.method
    for k, v in score(best, dept.url, dept.confidence).items():
        setattr(res, k, v)
    res.status = "Success"
    return res


def run_university(university: str, discipline: str, geo: str = "", country: str = "",
                   website: str = "", campus: str = "", show_candidates: bool = False) -> Result:
    effective_geo = f"{campus} {geo}".strip() if (campus and geo) else (campus or geo)
    res = Result(university_name=university, geo=effective_geo, country=country, discipline=discipline)
    trace = DebugTrace(university=university, discipline=discipline, geo=effective_geo, country=country)
    try:
        res = _run(res, website, trace, show_candidates=show_candidates)
    except Exception as e:  # one bad site must never kill a batch
        logger.exception(f"pipeline error for {university}")
        res.status, res.error = "Error", str(e)
        trace.add("pipeline", "error", error=str(e))
    try:
        res.debug_file = str(trace.write())
    except Exception as e:
        logger.warning(f"debug trace write failed for {university}: {e}")
    logger.info(f"[done] {university} | {discipline} -> {res.status} {res.contact_name}")
    return res
