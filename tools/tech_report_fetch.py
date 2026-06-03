#!/usr/bin/env python3
"""Fetch lab/vendor technical-report blog feeds (Tier 1 + Tier 2 sources).

Reads ``tools/source_registry.json``, iterates over enabled tiers, and
dispatches each source by ``type`` (``rss`` / ``atom`` / ``html_scrape``).
Emits one normalized JSON object per line to ``--output``.

Stdlib only by default. ``feedparser`` and ``beautifulsoup4`` are imported
opportunistically; missing deps fall back to ``xml.etree.ElementTree`` /
regex paths.

Example
-------
::

    python3 tools/tech_report_fetch.py \\
        --output runs/run-x/1_search/tech_reports.jsonl \\
        --tier 1,2 --year-start 2021 --year-end 2026 \\
        --max-per-source 50 --verbose
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from pathlib import Path
from typing import Iterable

# ---------------------------------------------------------------------------
# Optional dependencies (best-effort)
# ---------------------------------------------------------------------------

try:  # pragma: no cover - exercised only when the lib happens to be present
    import feedparser  # type: ignore
    _HAS_FEEDPARSER = True
except ImportError:  # pragma: no cover
    feedparser = None  # type: ignore
    _HAS_FEEDPARSER = False

try:  # pragma: no cover
    from bs4 import BeautifulSoup  # type: ignore
    _HAS_BS4 = True
except ImportError:  # pragma: no cover
    BeautifulSoup = None  # type: ignore
    _HAS_BS4 = False


_USER_AGENT = (
    "AutoSurvey-tech-report-fetch/1.0 "
    "(https://github.com/wanshuiyin/AutoSurvey)"
)
_ATOM_NS = "{http://www.w3.org/2005/Atom}"

# A few permissive date patterns we look for in plain HTML.
_HTML_DATE_PATTERNS = [
    # ISO 8601: 2024-10-26 / 2024-10-26T12:00:00Z
    re.compile(r"(\d{4})-(\d{2})-(\d{2})"),
    # "October 26, 2024"
    re.compile(
        r"(January|February|March|April|May|June|July|August|"
        r"September|October|November|December)\s+(\d{1,2}),?\s+(\d{4})",
        re.IGNORECASE,
    ),
    # "26 Oct 2024"
    re.compile(
        r"(\d{1,2})\s+"
        r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+(\d{4})",
        re.IGNORECASE,
    ),
]

_MONTHS_FULL = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5,
    "june": 6, "july": 7, "august": 8, "september": 9, "october": 10,
    "november": 11, "december": 12,
}
_MONTHS_ABBR = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def slugify(text: str, max_len: int = 50) -> str:
    """Lowercase a string and collapse non-alphanumeric runs to single hyphens.

    Trailing/leading hyphens are stripped. Result is truncated to ``max_len``.
    """
    if not text:
        return ""
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = text.strip("-")
    if len(text) > max_len:
        text = text[:max_len].rstrip("-")
    return text


def make_cite_key(source_name: str, year: int, title: str) -> str:
    """Build a deterministic cite key: ``<source>-<year>-<title>``."""
    src = slugify(source_name, max_len=30)
    ttl = slugify(title, max_len=50)
    return f"{src}-{year}-{ttl}"


def load_registry(path: str) -> dict:
    """Load the source-registry JSON document."""
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _parse_date(value: str) -> datetime | None:
    """Best-effort date parser for RSS/Atom/HTML strings.

    Returns a timezone-aware datetime in UTC, or ``None`` if unparseable.
    """
    if not value:
        return None
    raw = value.strip()
    # Try RFC 822 (RSS pubDate)
    try:
        dt = parsedate_to_datetime(raw)
        if dt is not None:
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
    except (TypeError, ValueError):
        pass
    # Try ISO 8601 (Atom <published>/<updated>)
    iso = raw.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        pass
    # Try date-only YYYY-MM-DD
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", raw)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)),
                            tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


def _strip_html(value: str) -> str:
    """Remove HTML tags, unescape entities, collapse whitespace."""
    if not value:
        return ""
    no_tags = re.sub(r"<[^>]+>", " ", value)
    cleaned = unescape(no_tags)
    return re.sub(r"\s+", " ", cleaned).strip()


def _truncate_summary(text: str, max_chars: int = 500) -> str:
    text = text.strip()
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "…"


# ---------------------------------------------------------------------------
# Feed parsing
# ---------------------------------------------------------------------------


def parse_rss(xml_bytes: bytes, source_name: str) -> list[dict]:
    """Parse RSS 2.0 or Atom 1.0 bytes into a list of raw entry dicts.

    Each entry contains: ``title``, ``url``, ``published`` (datetime|None),
    ``summary``, ``authors`` (list[str]).
    """
    if not xml_bytes:
        return []
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return []

    # Detect Atom by namespace or root tag.
    tag_lower = root.tag.lower()
    is_atom = "atom" in tag_lower or root.tag == f"{_ATOM_NS}feed"

    entries: list[dict] = []
    if is_atom:
        for entry in root.findall(f"{_ATOM_NS}entry"):
            entries.append(_parse_atom_entry(entry))
    else:
        # RSS 2.0: <rss><channel><item>...</item></channel></rss>
        channel = root.find("channel")
        if channel is None:
            # Some feeds omit the channel wrapper; iterate items directly.
            channel = root
        for item in channel.findall("item"):
            entries.append(_parse_rss_item(item))
    return [e for e in entries if e]


def _parse_rss_item(item: ET.Element) -> dict | None:
    title = _strip_html(item.findtext("title", "") or "")
    link = (item.findtext("link", "") or "").strip()
    pub = item.findtext("pubDate", "") or item.findtext("date", "") or ""
    description = _strip_html(item.findtext("description", "") or "")
    author = (item.findtext("author", "") or "").strip()

    # Some feeds use <dc:creator>
    if not author:
        for child in item:
            if child.tag.endswith("creator") and child.text:
                author = child.text.strip()
                break

    if not title and not link:
        return None
    return {
        "title": title,
        "url": link,
        "published": _parse_date(pub),
        "summary": _truncate_summary(description),
        "authors": [author] if author else [],
    }


def _parse_atom_entry(entry: ET.Element) -> dict | None:
    title = _strip_html(entry.findtext(f"{_ATOM_NS}title", "") or "")

    link_elem = entry.find(f"{_ATOM_NS}link")
    url = ""
    if link_elem is not None:
        url = link_elem.get("href", "") or ""
    if not url:
        # Fallback: a text-bodied <link>?
        url = (entry.findtext(f"{_ATOM_NS}link", "") or "").strip()

    published = (
        entry.findtext(f"{_ATOM_NS}published", "")
        or entry.findtext(f"{_ATOM_NS}updated", "")
        or ""
    )
    summary = _strip_html(
        entry.findtext(f"{_ATOM_NS}summary", "")
        or entry.findtext(f"{_ATOM_NS}content", "")
        or ""
    )
    authors: list[str] = []
    for author_el in entry.findall(f"{_ATOM_NS}author"):
        name = author_el.findtext(f"{_ATOM_NS}name", "")
        if name:
            authors.append(name.strip())

    if not title and not url:
        return None
    return {
        "title": title,
        "url": url,
        "published": _parse_date(published),
        "summary": _truncate_summary(summary),
        "authors": authors,
    }


# ---------------------------------------------------------------------------
# HTML scraping (best-effort)
# ---------------------------------------------------------------------------


def _resolve_url(base: str, href: str) -> str:
    if not href:
        return ""
    if href.startswith("http://") or href.startswith("https://"):
        return href
    if href.startswith("//"):
        return "https:" + href
    if not base:
        return href
    if href.startswith("/"):
        # naive scheme+host extraction
        m = re.match(r"^(https?://[^/]+)", base)
        if m:
            return m.group(1) + href
    # Relative path: append.
    if base.endswith("/"):
        return base + href
    return base.rsplit("/", 1)[0] + "/" + href


def _extract_date_from_text(text: str) -> datetime | None:
    if not text:
        return None
    for pat in _HTML_DATE_PATTERNS:
        m = pat.search(text)
        if not m:
            continue
        groups = [g.lower() if isinstance(g, str) else g for g in m.groups()]
        try:
            if pat is _HTML_DATE_PATTERNS[0]:
                year, month, day = int(groups[0]), int(groups[1]), int(groups[2])
            elif pat is _HTML_DATE_PATTERNS[1]:
                month = _MONTHS_FULL[groups[0]]
                day = int(groups[1])
                year = int(groups[2])
            else:  # _HTML_DATE_PATTERNS[2]
                day = int(groups[0])
                month = _MONTHS_ABBR[groups[1][:3]]
                year = int(groups[2])
            return datetime(year, month, day, tzinfo=timezone.utc)
        except (KeyError, ValueError):
            continue
    return None


def parse_html_scrape(html: str, source_url: str) -> list[dict]:
    """Best-effort article extraction from a homepage HTML blob.

    Looks for ``<article>...</article>`` blocks first, then any anchor whose
    surrounding text contains a parseable date. Returns up to a few dozen
    candidates; filtering / capping happens in the caller.
    """
    if not html:
        return []

    entries: list[dict] = []
    seen_urls: set[str] = set()

    # 1) Try <article>...</article> blocks.
    for article_match in re.finditer(
        r"<article[^>]*>(.*?)</article>", html, re.DOTALL | re.IGNORECASE
    ):
        block = article_match.group(1)
        link_match = re.search(
            r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',
            block,
            re.DOTALL | re.IGNORECASE,
        )
        if not link_match:
            continue
        href = link_match.group(1)
        url = _resolve_url(source_url, href)
        if url in seen_urls:
            continue
        link_text = _strip_html(link_match.group(2))
        block_text = _strip_html(block)
        title = link_text or block_text[:120]
        published = _extract_date_from_text(block_text)
        seen_urls.add(url)
        entries.append({
            "title": title,
            "url": url,
            "published": published,
            "summary": _truncate_summary(block_text),
            "authors": [],
        })

    # 2) Fallback: any anchor whose nearby text contains a date.
    if not entries:
        # Scan anchor tags and inspect a window of text after them.
        for m in re.finditer(
            r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',
            html,
            re.DOTALL | re.IGNORECASE,
        ):
            href = m.group(1)
            url = _resolve_url(source_url, href)
            if url in seen_urls or not url.startswith(("http://", "https://")):
                continue
            anchor_text = _strip_html(m.group(2))
            if not anchor_text or len(anchor_text) < 5:
                continue
            # Look at a 400-char window around the anchor for a date.
            start = max(0, m.start() - 200)
            window = html[start:m.end() + 200]
            window_text = _strip_html(window)
            published = _extract_date_from_text(window_text)
            if not published:
                continue
            seen_urls.add(url)
            entries.append({
                "title": anchor_text,
                "url": url,
                "published": published,
                "summary": _truncate_summary(window_text),
                "authors": [],
            })

    return entries


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------


def _fetch_bytes(url: str, timeout: int) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


# ---------------------------------------------------------------------------
# Per-source orchestration
# ---------------------------------------------------------------------------


def _normalize(
    raw: dict,
    source: dict,
    fetched_at: str,
) -> dict | None:
    """Convert a raw entry dict + source metadata into a paper-record."""
    title = (raw.get("title") or "").strip()
    url = (raw.get("url") or "").strip()
    if not title or not url:
        return None

    published: datetime | None = raw.get("published")
    if published is None:
        return None
    year = published.year

    return {
        "title": title,
        "authors": raw.get("authors") or [],
        "year": year,
        "url": url,
        "abstract": raw.get("summary") or "",
        "venue": source.get("name", ""),
        "source_type": "tech_report",
        "source_tier": int(source.get("tier", 0)) or None,
        "cite_key": make_cite_key(source.get("name", ""), year, title),
        "fetched_at": fetched_at,
    }


def fetch_source(
    source: dict,
    year_start: int,
    year_end: int,
    max_entries: int,
    timeout: int,
) -> list[dict]:
    """Fetch + parse + filter a single source. Returns normalized records.

    Network/parse failures bubble up so callers can log and continue.
    """
    feed_url = source.get("feed_url") or ""
    src_type = (source.get("type") or "").lower()
    if not feed_url or not src_type:
        return []

    body = _fetch_bytes(feed_url, timeout=timeout)

    raw_entries: list[dict]
    if src_type in ("rss", "atom"):
        raw_entries = parse_rss(body, source.get("name", ""))
    elif src_type == "html_scrape":
        try:
            html = body.decode("utf-8", errors="replace")
        except (AttributeError, UnicodeDecodeError):
            html = ""
        raw_entries = parse_html_scrape(html, feed_url)
    else:
        raw_entries = []

    fetched_at = datetime.now(timezone.utc).isoformat()
    normalized: list[dict] = []
    for raw in raw_entries:
        rec = _normalize(raw, source, fetched_at)
        if rec is None:
            continue
        if rec["year"] < year_start or rec["year"] > year_end:
            continue
        normalized.append(rec)

    # Newest first, then cap.
    def _sort_key(rec: dict) -> tuple:
        # Tie-break by title for determinism.
        return (-rec["year"], rec.get("title", ""))

    normalized.sort(key=_sort_key)
    return normalized[:max_entries]


# ---------------------------------------------------------------------------
# CLI / main
# ---------------------------------------------------------------------------


def _resolve_registry_path(arg: str | None) -> str:
    if arg:
        return arg
    # Default: same directory as this script.
    return str(Path(__file__).resolve().parent / "source_registry.json")


def _select_sources(registry: dict, tiers: Iterable[int]) -> list[dict]:
    tier_keys = {
        1: "tier1_official",
        2: "tier2_official",
        3: "tier3_curated",
    }
    sources: list[dict] = []
    for tier in tiers:
        key = tier_keys.get(tier)
        if not key:
            continue
        for entry in registry.get(key, []) or []:
            # Ensure tier is set on each source even if registry omits it.
            entry = dict(entry)
            entry.setdefault("tier", tier)
            sources.append(entry)
    return sources


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=(
            "Fetch lab/vendor tech-report blog feeds (Tier 1 + Tier 2) and "
            "emit paper-record JSONL."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--output",
        required=True,
        help="Path to write JSONL output (one entry per line).",
    )
    p.add_argument(
        "--registry",
        default=None,
        help="Path to source_registry.json (default: tools/source_registry.json).",
    )
    p.add_argument("--year-start", type=int, default=2021)
    p.add_argument("--year-end", type=int, default=2026)
    p.add_argument(
        "--tier",
        default="1,2",
        help="Comma-separated tier numbers to include (default: '1,2').",
    )
    p.add_argument("--max-per-source", type=int, default=50)
    p.add_argument("--timeout", type=int, default=15)
    p.add_argument("--verbose", action="store_true")
    return p


def _parse_tiers(value: str) -> list[int]:
    tiers: list[int] = []
    for part in value.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            tiers.append(int(part))
        except ValueError:
            continue
    return tiers or [1, 2]


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    registry_path = _resolve_registry_path(args.registry)

    try:
        registry = load_registry(registry_path)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"failed to load registry {registry_path}: {exc}", file=sys.stderr)
        return 2

    tiers = _parse_tiers(args.tier)
    sources = _select_sources(registry, tiers)
    if not sources:
        print(f"no sources selected for tiers {tiers}", file=sys.stderr)
        return 2

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    total_entries = 0
    succeeded: list[str] = []
    failed: list[str] = []

    with out_path.open("w", encoding="utf-8") as out_fh:
        for source in sources:
            name = source.get("name", "<unknown>")
            try:
                records = fetch_source(
                    source,
                    year_start=args.year_start,
                    year_end=args.year_end,
                    max_entries=args.max_per_source,
                    timeout=args.timeout,
                )
            except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
                print(f"[skip] {name}: HTTP error {exc}", file=sys.stderr)
                failed.append(name)
                continue
            except ET.ParseError as exc:
                print(f"[skip] {name}: parse error {exc}", file=sys.stderr)
                failed.append(name)
                continue
            except Exception as exc:  # pragma: no cover - last-resort guard
                print(f"[skip] {name}: unexpected error {exc}", file=sys.stderr)
                failed.append(name)
                continue

            for rec in records:
                out_fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            total_entries += len(records)
            succeeded.append(name)
            if args.verbose:
                print(f"[ok]   {name}: {len(records)} entries", file=sys.stderr)

    total = len(sources)
    fetched_msg = (
        f"Fetched {total_entries} entries from "
        f"{len(succeeded)}/{total} sources. Failed: {failed}"
    )
    print(fetched_msg, file=sys.stderr)

    if failed and not succeeded:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
