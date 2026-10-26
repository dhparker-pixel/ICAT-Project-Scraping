#!/usr/bin/env python3
"""Build ICAT supported-research JSON from verified Ensemble pages.

This program DOES NOT use AI or infer affiliations from article prose.

Install: python3 -m pip install requests beautifulsoup4
Run:     python3 icat_sead_scraper.py

Output: projects.generated.json, projects.scrape.log
If incomplete, output is projects.generated.INCOMPLETE.json instead.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import date
import hashlib
import json
import logging
from pathlib import Path
import re
import sys
import time
from urllib.parse import parse_qs, unquote, urljoin, urlsplit

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

BASE = "https://icat.vt.edu"
LIST_URL = BASE + "/projects/supported-research/sead-grants.html"
ALL_YEARS_URL = BASE + "/projects/supported-research.html"
OLD_JSON_URL = BASE + "/content/dam/icat_vt_edu/projects.json"
EXTRA_INDEX_URLS = {
    "rapid-response": BASE + "/projects/supported-research/sead-grants/rapid-response.html",
    "misc": BASE + "/projects/supported-research/sead-grants/misc.html",
}
GRANT_TAG_URLS = [
    BASE + "/content/icat_vt_edu/en/tags.html/icat_vt_edu%3AICAT-Project/major-sead-grant",
    BASE + "/content/icat_vt_edu/en/tags.html/icat_vt_edu%3AICAT-Project/mini-sead-grant",
    BASE + "/content/icat_vt_edu/en/tags.html/icat_vt_edu%3AICAT-Project/student-sead-grant",
]
# The earlier version mistakenly excluded ALL student project URLs. Student
# projects really are published under /YYYY-YYYY/student/<slug>.html.

PATH_GRANT_LABELS = {
    "major": "Major SEAD Grant",
    "mini": "Mini SEAD Grant",
    "student": "Student SEAD Grant",
}
PROJECT_PATH = re.compile(
    r"^/projects/supported-research/sead-grants/(\d{4}-\d{4})/(major|mini|student)/.+\.html$",
    re.IGNORECASE,
)
# These collections have NO academic year in the published project URL.
# "Miscellaneous" is an archive grouping, not an assertion that a grant was awarded.

SPECIAL_GRANT_LABELS = {
    "rapid-response": "Rapid Response Grant", "misc": "Miscellaneous"}
SPECIAL_PROJECT_PATH = re.compile(
    r"^/projects/supported-research/sead-grants/(rapid-response|misc)/[^/]+\.html$",
    re.IGNORECASE,
)
ALL_GRANT_KINDS = tuple(PATH_GRANT_LABELS) + tuple(SPECIAL_GRANT_LABELS)
YEAR_RE = re.compile(r"^\d{4}-\d{4}$")


def project_path_info(path: str):
    match = PROJECT_PATH.fullmatch(path)
    if match:
        return match.group(1), match.group(2).lower(), PATH_GRANT_LABELS[match.group(2).lower()]
    match = SPECIAL_PROJECT_PATH.fullmatch(path)
    if match:
        kind = match.group(1).lower()
        return "", kind, SPECIAL_GRANT_LABELS[kind]
    return None


ANY_SEAD_HTML = re.compile(
    r"^/projects/supported-research/sead-grants/[^?#]+\.html$", re.IGNORECASE
)
LAST_VERIFIED_EXPECTED_COUNT = 236  # SEAD listing on October 15, 2026
EXPECTED_RE = re.compile(r"\b(\d[\d,]*)\s+Results\b", re.IGNORECASE)
REVIEW_SECTIONS = re.compile(
    r"^(documentation|mid.term report|final report|reports?|tags|related (articles|stories)|"
    r"presentations?|supplementary materials?|project updates?)\b",
    re.IGNORECASE,
)

# Tag *namespaces*, not words guessed from a person's or a unit's name.
# Checked against published tags: People, Department-School, ICAT-Center,
# Idea-Challenge, Studios, and ICAT-Project.

PEOPLE_NS = {"people"}
UNIT_NS = {"department-school", "icat-center"}
TOPIC_NS = {"idea-challenge"}
OTHER_TOPIC_ROOTS = {"visualization", "visual-arts"}
GRANT_LABELS = set(PATH_GRANT_LABELS.values()) | {
    "Rapid Response Grant", "ICAT Rapid Response Grant"}
# Studio/location tags are NOT organizational affiliations.
SKIPPED_NS = {"studios"}


def clean(value: str) -> str:
    return " ".join((value or "").split()).strip()


def ordered_unique(values):
    result, seen = [], set()
    for value in values:
        value = clean(value)
        if value and value.casefold() not in seen:
            result.append(value)
            seen.add(value.casefold())
    return result


def canonical_project_url(href: str) -> str | None:
    if not href:
        return None
    full = urljoin(BASE + "/", href)
    p = urlsplit(full)
    if p.hostname not in {"icat.vt.edu", "www.icat.vt.edu"}:
        return None
    if not project_path_info(p.path):
        return None
    return BASE + p.path


def new_session():
    session = requests.Session()
    retry = Retry(
        total=4,
        backoff_factor=0.9,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET",),
        respect_retry_after_header=True,
    )
    session.mount("https://", HTTPAdapter(max_retries=retry))
    session.headers.update({
        "User-Agent": "ICAT-SEAD-JSON-Builder/1.0 (+https://icat.vt.edu; editorial data audit)",
        "Accept": "text/html,application/json;q=0.9,*/*;q=0.5",
    })
    return session


class Fetcher:
    def __init__(self, session, delay: float, timeout: int, logger):
        self.session, self.delay, self.timeout, self.log = session, delay, timeout, logger
        self.count = 0
        self.failures = []

    def get(self, url: str, *, required=True):
        if self.count and self.delay:
            time.sleep(self.delay)
        self.count += 1
        try:
            r = self.session.get(url, timeout=self.timeout)
            r.raise_for_status()
            if not r.content:
                raise ValueError("Empty response")
            return r
        except (requests.RequestException, ValueError) as exc:
            self.log.error("FETCH_FAILED url=%s error=%s", url, exc)
            if required:
                self.failures.append(url)
            return None


def project_links(soup: BeautifulSoup) -> set[str]:
    links = set()
    for a in soup.select("a[href]"):
        u = canonical_project_url(a.get("href"))
        if u:
            links.add(u)
    return links


def unrecognized_grant_paths(soup: BeautifulSoup) -> set[str]:
    """Identify project-looking paths omitted by our STRICT grant URL parser.

    These are logged, not silently classified or included without evidence.
    """
    unknown = set()
    for anchor in soup.select("a[href]"):
        full = urlsplit(urljoin(BASE + "/", anchor.get("href", "")))
        if full.hostname not in {"icat.vt.edu", "www.icat.vt.edu"}:
            continue
        if ANY_SEAD_HTML.fullmatch(full.path) and not project_path_info(full.path):
            tail = full.path.split("/sead-grants/", 1)[-1]
            # Index pages (e.g. /misc.html and /2024-2025.html) aren't projects.
            if "/" in tail:
                unknown.add(BASE + full.path)
    return unknown


def grant_path_counts(urls) -> dict[str, int]:
    counts = Counter()
    for url in urls:
        info = project_path_info(urlsplit(url).path)
        if info:
            counts[info[1]] += 1
    return {kind: counts[kind] for kind in ALL_GRANT_KINDS}


def listing_pages(soup, current_url, visited):
    """Follow real pagination hrefs only. Client-side pagination needs other sources."""
    found = set()
    for a in soup.select("a[href]"):
        href = a.get("href", "")
        full = urljoin(current_url, href)
        p = urlsplit(full)
        text = clean(a.get_text(" ", strip=True)).casefold()
        # Avoid arbitrary navigation, even when its URL resembles the index.
        if p.netloc not in {"icat.vt.edu", "www.icat.vt.edu"}:
            continue
        if p.path != urlsplit(LIST_URL).path:
            continue
        if (p.query or p.fragment) and (text in {"next", "previous", "prev"} or text.isdigit() or "page" in href.lower()):
            full = BASE + p.path + (("?" + p.query) if p.query else "")
            if full not in visited:
                found.add(full)
    return found


def discover(fetcher: Fetcher, log, max_listing_pages: int, year: str | None = None):
    discovered = defaultdict(set)
    unknown_paths = set()
    expected_sead = None
    visited = set()
    # Year-only runs start with the year archive, not a 30-page general listing.
    year_url = BASE + "/projects/supported-research/sead-grants/" + \
        year + ".html" if year else None
    pending = [year_url] if year_url else [LIST_URL]
    seed_failures = []

    def add_links(soup, source):
        unknown_paths.update(unrecognized_grant_paths(soup))
        matches = 0
        for project_url in project_links(soup):
            year_in_path = project_path_info(urlsplit(project_url).path)[0]
            if year is None or year_in_path == year:
                discovered[project_url].add(source)
                matches += 1
        return matches

    while pending and len(visited) < max_listing_pages:
        url = pending.pop(0)
        if url in visited:
            continue
        visited.add(url)
        response = fetcher.get(url)
        if not response:
            seed_failures.append(url)
            continue
        soup = BeautifulSoup(response.content, "html.parser")
        count = add_links(soup, "Year listing" if year else "SEAD listing")
        log.info(
            "DISCOVERY source=listing url=%s matching_project_links=%d", url, count)
        if not year:
            match = EXPECTED_RE.search(soup.get_text(" ", strip=True))
            if match:
                expected_sead = max(expected_sead or 0, int(
                    match.group(1).replace(",", "")))
            pending.extend(sorted(listing_pages(
                soup, url, visited) - set(pending)))
    if pending:
        log.warning("DISCOVERY_PAGINATION_LIMIT remaining=%d limit=%d",
                    len(pending), max_listing_pages)

    # Year pages explicitly enumerate projects. Broader indexes can expose
    # missing linked projects, including old projects excluded by page pagination.
    seeds = [("Supported Research year listing", ALL_YEARS_URL)]
    if not year:
        seeds += [("Supplementary archive", url)
                  for url in EXTRA_INDEX_URLS.values()]
    seeds += [("Grant tag index", url) for url in GRANT_TAG_URLS]
    # Collect all documented year indexes as extra entry points in all mode.
    if not year:
        response = fetcher.get(ALL_YEARS_URL, required=False)
        if response:
            overview = BeautifulSoup(response.content, "html.parser")
            candidate_years = set()
            for a in overview.select("a[href]"):
                u = urlsplit(urljoin(BASE + "/", a["href"]))
                match = re.fullmatch(
                    r"/projects/supported-research/sead-grants/(\d{4}-\d{4})\.html", u.path)
                if match and u.hostname in {"icat.vt.edu", "www.icat.vt.edu"}:
                    candidate_years.add(BASE + u.path)
            seeds.extend(("Funding year archive", u)
                         for u in sorted(candidate_years))
            log.info("YEAR_ARCHIVES discovered=%d", len(candidate_years))

    for label, url in seeds:
        response = fetcher.get(url, required=(
            label == "Supplementary archive"))
        if not response:
            if label == "Supplementary archive":
                seed_failures.append(url)
            continue
        soup = BeautifulSoup(response.content, "html.parser")
        count = add_links(soup, label)
        log.info("DISCOVERY source=%s url=%s matching_project_links=%d",
                 label, url, count)
    for url in sorted(unknown_paths):
        log.warning(
            "UNRECOGNIZED_SEAD_PATH url=%s (not included; review new path structure)", url)
    log.info("DISCOVERY_TOTAL unique_project_urls=%d sead_listing_results=%s types=%s unrecognized=%d",
             len(discovered), expected_sead, grant_path_counts(discovered), len(unknown_paths))
    return discovered, expected_sead, seed_failures


def discover_with_browser(log, max_pages: int):
    """Optional fallback for Ensemble listing pagination controlled by JavaScript.

    Install with: pip install playwright && python -m playwright install chromium
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        log.warning(
            "BROWSER_UNAVAILABLE install: pip install playwright && python -m playwright install chromium")
        return set()
    found = set()
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(LIST_URL, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(1200)
            for number in range(1, max_pages + 1):
                html = page.content()
                current = project_links(BeautifulSoup(html, "html.parser"))
                found.update(current)
                log.info("BROWSER_LISTING page=%d links=%d cumulative=%d",
                         number, len(current), len(found))
                if number == max_pages:
                    break
                next_elements = page.locator("button, a, [role='button']").filter(
                    has_text=re.compile(r"^\s*Next\s*$", re.I)
                )
                target = None
                for index in range(min(next_elements.count(), 20)):
                    item = next_elements.nth(index)
                    if item.is_visible() and item.is_enabled() and item.get_attribute("aria-disabled") != "true":
                        target = item
                        break
                if target is None:
                    log.info("BROWSER_END_OF_PAGES current=%d", number)
                    break
                try:
                    target.click(timeout=8000)
                    changed = False
                    for _ in range(15):
                        page.wait_for_timeout(400)
                        updated = project_links(BeautifulSoup(
                            page.content(), "html.parser"))
                        if updated and updated != current:
                            changed = True
                            break
                    if not changed:
                        log.warning(
                            "BROWSER_PAGINATION_STALLED page=%d", number)
                        break
                except Exception as exc:
                    log.warning(
                        "BROWSER_PAGINATION_ERROR page=%d error=%s", number, exc)
                    break
            browser.close()
    except Exception as exc:
        log.warning(
            "BROWSER_START_FAILED error=%s (see Playwright Chromium installation)", exc)
    return found


def tag_identifier(href: str) -> str | None:
    """Support Ensemble's query and path forms, without guessing from labels."""
    p = urlsplit(urljoin(BASE + "/", href))
    if p.hostname not in {"icat.vt.edu", "www.icat.vt.edu"} or "tags.html" not in p.path:
        return None
    query = parse_qs(p.query)
    val = query.get("tag", [""])[0]
    if not val and "tags.html/" in p.path:
        val = p.path.split("tags.html/", 1)[1]
    val = unquote(val).strip("/")
    if ":" not in val:
        return None
    namespace, rest = val.split(":", 1)
    if namespace.casefold() != "icat_vt_edu" or not rest:
        return None
    return rest


def extract_tags(soup: BeautifulSoup, log, url):
    people, affiliations, topics, grant_types = [], [], [], []
    unseen = []
    tag_count = 0
    for anchor in soup.select("a[href]"):
        raw = tag_identifier(anchor.get("href", ""))
        if raw is None:
            continue
        label = clean(anchor.get_text(" ", strip=True))
        label = re.sub(r"^tag\s*:\s*", "", label, flags=re.I)
        if not label:
            unseen.append((raw, "empty tag label"))
            continue
        tag_count += 1
        segments = raw.split("/")
        root = segments[0].casefold()
        if root in PEOPLE_NS:
            if len(segments) >= 2:
                people.append(label)
            else:
                unseen.append((raw, "root-level People tag"))
        elif root in UNIT_NS:
            if len(segments) >= 2:
                affiliations.append(label)
            else:
                unseen.append((raw, "root-level unit tag"))
        elif root in TOPIC_NS:
            if len(segments) >= 2:
                topics.append(label)
            else:
                unseen.append((raw, "root-level topic tag"))
        elif root == "icat-project":
            if label in GRANT_LABELS:
                grant_types.append(label)
            elif label.casefold() not in {"icat project"}:
                unseen.append((raw, "unmapped ICAT-Project classification"))
        elif root in OTHER_TOPIC_ROOTS and len(segments) == 1:
            topics.append(label)
        elif root in SKIPPED_NS:
            # Deliberately don't turn a studio/facility into a college or department.
            pass
        else:
            unseen.append((raw, "unmapped tag namespace"))

    if not tag_count:
        log.warning("NO_TAGS url=%s", url)
    for raw, reason in sorted(set(unseen)):
        log.warning("UNMAPPED_TAG url=%s tag_id=%r reason=%s",
                    url, raw, reason)
    return {
        "people": sorted(ordered_unique(people), key=str.casefold),
        "affiliations": sorted(ordered_unique(affiliations), key=str.casefold),
        "topics": sorted(ordered_unique(topics), key=str.casefold),
        "grant_types": ordered_unique(grant_types),
        "tag_count": tag_count,
        "unmapped_count": len(set(unseen)),
    }


def is_page_content(node) -> bool:
    """Reject text from common chrome, overlays and non-project templates."""
    for parent in node.parents:
        if getattr(parent, "name", "") in {"header", "nav", "footer", "aside"}:
            return False
        classes = " ".join(parent.get("class", [])) if parent.name else ""
        if re.search(r"breadcrumb|site-search|navigation|navbar|tag[-_]?list", classes, re.I):
            return False
    return True


def excerpt(paragraph: str, max_length: int = 650) -> str:
    paragraph = clean(paragraph)
    if len(paragraph) <= max_length:
        return paragraph
    prefix = paragraph[: max_length + 1]
    # Prefer a complete sentence, but avoid collapsing an 800-character paragraph
    # into an unhelpful 25-character first sentence.
    complete = re.findall(r".+?[.!?](?=\s|$)", prefix)
    if complete:
        candidate = clean(" ".join(complete))
        if len(candidate) >= 90 and len(candidate) <= max_length:
            return candidate
    return prefix[:max_length].rsplit(" ", 1)[0].strip()


def extract_description(soup: BeautifulSoup):
    headings = [h for h in soup.find_all("h1") if is_page_content(h)]
    if not headings:
        return "", ""
    h1 = headings[0]
    for node in h1.next_elements:
        if not getattr(node, "name", None):
            continue
        if node.name in {"h2", "h3", "h4"}:
            name = clean(node.get_text(" ", strip=True))
            if REVIEW_SECTIONS.search(name):
                break
        if node.name == "p" and is_page_content(node):
            content = clean(node.get_text(" ", strip=True))
            if len(content) >= 45 and not REVIEW_SECTIONS.search(content):
                return excerpt(content), "body-paragraph"
    return "", ""


def get_title(soup: BeautifulSoup):
    for h in soup.find_all("h1"):
        if is_page_content(h):
            return clean(h.get_text(" ", strip=True))
    return ""


def make_project(url: str, soup: BeautifulSoup, log, run_date):
    path = urlsplit(url).path
    info = project_path_info(path)
    if not info:
        raise ValueError("Unexpected project URL: " + url)
    year, path_type, path_grant = info
    title = get_title(soup)
    description, desc_source = extract_description(soup)
    tags = extract_tags(soup, log, url)
    tag_grants = ["Rapid Response Grant" if x == "ICAT Rapid Response Grant" else x
                  for x in tags["grant_types"]]
    if len(tag_grants) > 1 or (tag_grants and tag_grants[0] != path_grant):
        # Do not choose a side when published path and tags contradict each other.
        grant_type = ""
        log.warning("GRANT_TYPE_CONFLICT url=%s path=%r tags=%r",
                    url, path_grant, tag_grants)
    else:
        grant_type = tag_grants[0] if tag_grants else path_grant
        if not tag_grants and path_type in PATH_GRANT_LABELS:
            log.warning(
                "GRANT_TAG_MISSING url=%s using_explicit_url_path=%r", url, path_grant)

    # This is intentionally blank for /misc/ and /rapid-response/ URLs.
    # A publication date is NOT evidence of a funding cycle.
    if not year:
        log.warning("FUNDING_YEAR_UNKNOWN url=%s category=%s", url, path_type)

    # Missing metadata is reported; it is never guessed from prose.
    for field, value in [
        ("title", title), ("description", description),
        ("people", tags["people"]), ("affiliations", tags["affiliations"]),
        ("topics", tags["topics"]), ("grantType", grant_type),
    ]:
        if not value:
            log.warning("MISSING_FIELD url=%s field=%s", url, field)
    if desc_source:
        log.debug("DESCRIPTION_SOURCE url=%s source=%s", url, desc_source)

    return {
        # Assigned after discovering all URLs and retaining existing IDs.
        "id": "",
        "title": title,
        "url": path,
        "description": description,
        "fundingCycles": [{"year": year, "grantType": grant_type}],
        "affiliations": tags["affiliations"],
        "people": tags["people"],
        "topics": tags["topics"],
        "review": {"lastReviewed": run_date, "sources": [url]},
    }


def assign_ids(projects, baseline, log):
    old_ids = {}
    for p in baseline.get("projects", []):
        if not isinstance(p, dict):
            continue
        url = canonical_project_url(p.get("url", ""))
        if url and p.get("id"):
            old_ids[url] = str(p["id"])
    seen = set()
    for p in sorted(projects, key=lambda x: x["url"]):
        url = BASE + p["url"]
        slug = p["url"].rsplit("/", 1)[-1].removesuffix(".html")
        year = p["fundingCycles"][0]["year"]
        grant = p["url"].split("/")[-2]
        candidate = old_ids.get(url) or slug
        if candidate in seen:
            candidate = f"{slug}-{year}"
        if candidate in seen:
            candidate = f"{slug}-{year}-{grant}"
        if candidate in seen:
            candidate += "-" + hashlib.sha1(url.encode()).hexdigest()[:8]
        seen.add(candidate)
        p["id"] = candidate
        if url in old_ids and candidate != old_ids[url]:
            log.warning("BASELINE_ID_COLLISION url=%s old_id=%s assigned=%s",
                        url, old_ids[url], candidate)


def build_output(projects, baseline):
    years = {c.get("year") for p in projects for c in p.get(
        "fundingCycles", []) if c.get("year")}
    types = {c.get("grantType") for p in projects for c in p.get(
        "fundingCycles", []) if c.get("grantType")}
    all_units = {item for p in projects for item in p.get("affiliations", [])}
    all_topics = {item for p in projects for item in p.get("topics", [])}
    # schemaVersion and keys match the existing deployed file.
    return {
        "schemaVersion": baseline.get("schemaVersion", 1),
        "grantTypes": sorted(types),
        "fundingYears": sorted(years, reverse=True),
        "topicsVocabulary": sorted(all_topics, key=str.casefold),
        "units": sorted(all_units, key=str.casefold),
        "projects": sorted(
            sorted(projects, key=lambda x: (x["title"].casefold(), x["url"])),
            key=lambda x: x.get("fundingCycles", [{}])[0].get("year", ""), reverse=True
        ),
    }


def make_logger(path: Path, verbose: bool):
    logger = logging.getLogger("icat-sead-scraper")
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    logger.handlers.clear()
    formatter = logging.Formatter("%(asctime)s %(levelname)-7s %(message)s")
    file_handler = logging.FileHandler(path, mode="w", encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.DEBUG if verbose else logging.INFO)
    console.setFormatter(formatter)
    logger.addHandler(console)
    return logger


def cli_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path,
                        default=Path("projects.generated.json"))
    parser.add_argument("--log", type=Path,
                        default=Path("projects.scrape.log"))
    parser.add_argument("--baseline", default=OLD_JSON_URL,
                        help="URL or local path to original projects.json")
    parser.add_argument("--delay", type=float, default=0.7,
                        help="Seconds between HTTP requests (default: 0.7)")
    parser.add_argument("--timeout", type=int, default=25)
    parser.add_argument("--max-listing-pages", type=int, default=50)
    parser.add_argument("--browser", choices=("auto", "always", "never"), default="auto",
                        help="Use Playwright for client-side pagination when needed (default: auto)")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--all", action="store_true",
                      help="Scrape all categories (default)")
    mode.add_argument("--year", metavar="YYYY-YYYY",
                      help="Scrape only one funding year; excludes undated Misc and Rapid Response pages")
    parser.add_argument("--merge-baseline", action="store_true",
                        help="With --year, replace only that year in the baseline, preserving other projects")
    parser.add_argument("--expected-count", type=int, default=0,
                        help="Exact TOTAL expected for this run (including Misc/Rapid in --all mode)")
    parser.add_argument("--allow-incomplete", action="store_true",
                        help="Write the requested output even if coverage/errors indicate incompleteness")
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args()


def load_baseline(fetcher, source, log):
    if source.startswith(("http://", "https://")):
        resp = fetcher.get(source, required=False)
        if not resp:
            log.warning(
                "BASELINE_UNAVAILABLE original IDs cannot be preserved")
            return {"schemaVersion": 1, "projects": []}
        content = resp.content
    else:
        try:
            content = Path(source).read_bytes()
        except OSError as exc:
            log.warning("BASELINE_UNAVAILABLE source=%s error=%s", source, exc)
            return {"schemaVersion": 1, "projects": []}
    try:
        data = json.loads(content)
        if not isinstance(data, dict) or not isinstance(data.get("projects", []), list):
            raise ValueError("Baseline must contain a projects array")
        return data
    except (json.JSONDecodeError, ValueError) as exc:
        log.warning("BASELINE_INVALID source=%s error=%s", source, exc)
        return {"schemaVersion": 1, "projects": []}


def merge_year_into_baseline(new_projects, baseline, year: str, log):
    """Replace only year-matching records; undated archive items stay untouched."""
    old = baseline.get("projects", [])
    for record in old:
        cycles = record.get("fundingCycles", [])
        if len(cycles) > 1 and any(c.get("year") == year for c in cycles):
            raise ValueError("Cannot safely replace multi-year funding record: " +
                             str(record.get("url", record.get("id"))))
    old_remaining = [p for p in old if not any(
        cycle.get("year") == year for cycle in p.get("fundingCycles", [])
    )]
    known_old_ids = {str(p.get("id")) for p in old_remaining if p.get("id")}
    colliding_ids = sorted(known_old_ids & {p["id"] for p in new_projects})
    if colliding_ids:
        raise ValueError(
            "New IDs collide with preserved records: " + ", ".join(colliding_ids))
    log.info("YEAR_MERGE year=%s preserved=%d replaced=%d baseline=%d",
             year, len(old_remaining), len(new_projects), len(old))
    return old_remaining + new_projects


def main():
    args = cli_args()
    if args.delay < 0:
        raise SystemExit("--delay must be >= 0")
    if args.year:
        if not YEAR_RE.fullmatch(args.year):
            raise SystemExit("--year must use YYYY-YYYY, e.g. 2024-2025")
        start, end = map(int, args.year.split("-"))
        if end != start + 1:
            raise SystemExit("--year end must equal start + 1")
    if args.merge_baseline and not args.year:
        raise SystemExit("--merge-baseline requires --year")
    # Year exports use a different filename, so they cannot accidentally replace
    # the normal full-archive output when the user accepts the CLI defaults.
    if args.year and args.output == Path("projects.generated.json"):
        stem = "projects.merged" if args.merge_baseline else "projects"
        args.output = Path(f"{stem}.{args.year}.json")
    if args.year and args.log == Path("projects.scrape.log"):
        args.log = Path(f"projects.{args.year}.scrape.log")
    args.log.parent.mkdir(parents=True, exist_ok=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    log = make_logger(args.log, args.verbose)
    fetcher = Fetcher(new_session(), args.delay, args.timeout, log)
    log.info("START crawl=%s mode=%s date=%s", LIST_URL,
             args.year or "all", date.today().isoformat())
    baseline = load_baseline(fetcher, args.baseline, log)
    if args.merge_baseline and not baseline.get("projects"):
        raise SystemExit(
            "--merge-baseline requires a valid non-empty baseline JSON; no output written")

    discovered, listed_sead_count, seed_failures = discover(
        fetcher, log, args.max_listing_pages, args.year)
    expected = args.expected_count or None
    if listed_sead_count and not args.year:
        log.info(
            "SEAD_LISTING_COUNT count=%d (not treated as TOTAL: extra archives are now included)", listed_sead_count)
    standard_discovered = sum(grant_path_counts(discovered)[
                              kind] for kind in PATH_GRANT_LABELS)
    if args.browser == "always" or (
        args.browser == "auto" and not args.year and listed_sead_count and standard_discovered < listed_sead_count
    ):
        browser_links = discover_with_browser(log, args.max_listing_pages)
        for url in browser_links:
            info = project_path_info(urlsplit(url).path)
            if info and (not args.year or info[0] == args.year):
                discovered[url].add("Browser listing pagination")
        log.info("BROWSER_DISCOVERY_TOTAL found=%d unique=%d",
                 len(browser_links), len(discovered))

    projects = []
    project_errors = []
    missing_counts = Counter()
    for n, url in enumerate(sorted(discovered), start=1):
        log.info("PROJECT %d/%d %s sources=%s", n, len(discovered), url,
                 ", ".join(sorted(discovered[url])))
        resp = fetcher.get(url)
        if not resp:
            project_errors.append(url)
            continue
        if "html" not in resp.headers.get("content-type", "text/html").lower():
            project_errors.append(url)
            log.error("NOT_HTML url=%s content_type=%s",
                      url, resp.headers.get("content-type"))
            continue
        try:
            p = make_project(url, BeautifulSoup(
                resp.content, "html.parser"), log, date.today().isoformat())
            if not p["title"]:
                project_errors.append(url)
                log.error("MISSING_TITLE_SKIP url=%s", url)
                continue
            projects.append(p)
            for key in ("description", "people", "affiliations", "topics"):
                if not p[key]:
                    missing_counts[key] += 1
            if not p["fundingCycles"][0]["grantType"]:
                missing_counts["grantType"] += 1
            if not p["fundingCycles"][0]["year"]:
                missing_counts["year"] += 1
        except Exception as exc:
            project_errors.append(url)
            log.exception("PROJECT_PARSE_FAILED url=%s error=%s", url, exc)

    # Baseline ID mapping is always used, even in year-only export mode.
    assign_ids(projects, baseline, log)
    incomplete = (
        bool(fetcher.failures or project_errors or seed_failures)
        or not projects
        or (expected is not None and len(projects) != expected)
        or (not args.year and listed_sead_count is not None
            and sum(grant_path_counts(BASE + p["url"] for p in projects)[kind] for kind in PATH_GRANT_LABELS) < listed_sead_count)
    )
    if expected is not None and len(projects) != expected:
        log.error("COVERAGE_COUNT_DIFFERENCE expected=%d scraped=%d difference=%+d",
                  expected, len(projects), len(projects) - expected)
    if not args.year and listed_sead_count and standard_discovered < listed_sead_count:
        log.error("DISCOVERY_INCOMPLETE sead_listing_count=%d standard_discovered=%d",
                  listed_sead_count, standard_discovered)
    discovered_by_type = grant_path_counts(discovered)
    completed_by_type = grant_path_counts(BASE + p["url"] for p in projects)
    for grant_type in ALL_GRANT_KINDS:
        log.info("GRANT_TYPE_COUNT kind=%s discovered=%d completed=%d",
                 grant_type, discovered_by_type[grant_type], completed_by_type[grant_type])
    log.info("SUMMARY mode=%s discovered=%d completed=%d failed=%d expected_total=%s missing=%s",
             args.year or "all", len(discovered), len(projects), len(project_errors), expected, dict(missing_counts))
    for url in sorted(set(fetcher.failures + project_errors + seed_failures)):
        log.error("UNSCRAPED_URL %s", url)

    if args.merge_baseline and incomplete:
        log.error(
            "YEAR_MERGE_REFUSED incomplete year extraction; baseline must not be damaged")
    else:
        if args.merge_baseline:
            try:
                projects = merge_year_into_baseline(
                    projects, baseline, args.year, log)
            except ValueError as exc:
                raise SystemExit(str(exc))
        data = build_output(projects, baseline)
        output = args.output
        if incomplete and not args.allow_incomplete:
            output = args.output.with_name(
                args.output.stem + ".INCOMPLETE" + args.output.suffix)
            log.error("INCOMPLETE_DATASET writing=%s instead of target", output)
        output.write_text(json.dumps(data, ensure_ascii=False,
                          indent=2) + "\n", encoding="utf-8")
        log.info("WROTE_JSON file=%s projects=%d",
                 output.resolve(), len(projects))
    log.info("WROTE_LOG file=%s", args.log.resolve())
    if incomplete:
        log.error("EXIT_INCOMPLETE review collection failures or count differences")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
