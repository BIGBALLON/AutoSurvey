#!/usr/bin/env python3
"""Fetch curated personal blogs (Tier 3) listed in ``source_registry.json``.

A specialised sibling of ``tech_report_fetch.py``. Same fetch / parse skeleton,
but tuned for curated personal blogs (Lil'Log, Sebastian Raschka, Eugene Yan,
Nathan Lambert / Interconnects).

Differences from ``tech_report_fetch.py``:

* Iterates only the ``tier3_curated`` entries in the registry.
* Each output record gets ``source_type: "blog"`` and ``source_tier: 3``.
* Honours each source's optional ``warn_on_exclude`` array. When
  ``--exclude-keywords`` is supplied (the brief's ``scope.exclude`` list) and
  any keyword overlaps the source's ``warn_on_exclude`` list, every record
  emitted from that source is tagged ``out_of_scope_warning: true``. The
  downstream LLM-as-filter uses this as a stricter rejection signal.

CLI
---

    python3 blog_fetch.py --output blogs.jsonl
                          [--registry tools/source_registry.json]
                          [--year-start 2021] [--year-end 2026]
                          [--max-per-source 50]
                          [--exclude-keywords "RLHF, alignment"]
                          [--timeout 15] [--verbose]

Stdlib only — no third-party deps. This keeps the pretraining pipeline runnable
on a vanilla Python install.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import re
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Iterable

_USER_AGENT = (
    "AutoSurvey-blog-fetch/1.0 "
    "(github.com/wanshuiyin/Auto-claude-code-research-in-sleep)"
)
_ATOM_NS = "http://www.w3.org/2005/Atom"
_DEFAULT_REGISTRY = Path(__file__).resolve().parent / "source_registry.json"
_ABSTRACT_MAX_CHARS = 500


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def slugify(text: str, max_len: int = 50) -> str:
    """Lowercase, ascii-fold, hyphenate; strip apostrophes and punctuation.

    ``slugify("Lil'Log")`` -> ``"lillog"``.
    ``slugify("LLM Powered Autonomous Agents")`` -> ``"llm-powered-autonomous-agents"``.
    """
    if not text:
        return ""
    s = text.lower()
    # Drop apostrophes / quotes / curly variants outright (no separator).
    s = re.sub(r"[‘’“”'\"`]", "", s)
    # Replace any non-alphanumeric run with a single hyphen.
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = s.strip("-")
    if len(s) > max_len:
        s = s[:max_len].rstrip("-")
    return s


def make_cite_key(source_name: str, year: int, title: str) -> str:
    """Produce ``<source-slug>-<year>-<title-slug>`` for a blog post."""
    src = slugify(source_name, max_len=30) or "blog"
    ttl = slugify(title, max_len=50) or "untitled"
    return f"{src}-{year}-{ttl}"


def should_warn(source: dict, exclude_keywords: list[str]) -> bool:
    """Return True if any source.warn_on_exclude term appears in exclude_keywords.

    Match is case-insensitive substring (either direction): a registry term of
    ``"RLHF"`` matches an exclude entry of ``"rlhf"`` and vice versa.
    """
    warn_terms = source.get("warn_on_exclude") or []
    if not warn_terms or not exclude_keywords:
        return False
    excludes_lc = [(e or "").strip().lower() for e in exclude_keywords if e]
    excludes_lc = [e for e in excludes_lc if e]
    if not excludes_lc:
        return False
    for term in warn_terms:
        t = (term or "").strip().lower()
        if not t:
            continue
        for excl in excludes_lc:
            if t == excl or t in excl or excl in t:
                return True
    return False


def load_tier3_sources(registry_path: str | Path) -> list[dict]:
    """Return the ``tier3_curated`` array from ``source_registry.json``."""
    path = Path(registry_path)
    with path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    return list(data.get("tier3_curated", []))


# ---------------------------------------------------------------------------
# feed parsing
# ---------------------------------------------------------------------------


def _parse_pub_date(raw: str | None) -> _dt.datetime | None:
    """Parse RFC822 (RSS) or ISO8601 (Atom) timestamps; return None on failure."""
    if not raw:
        return None
    raw = raw.strip()
    # RFC822 (RSS pubDate) — try email.utils first.
    try:
        dt = parsedate_to_datetime(raw)
        if dt is not None:
            return dt
    except (TypeError, ValueError, IndexError):
        pass
    # ISO8601 (Atom <updated>/<published>). Handle trailing "Z".
    iso = raw.replace("Z", "+00:00")
    try:
        return _dt.datetime.fromisoformat(iso)
    except ValueError:
        pass
    # YYYY-MM-DD only.
    try:
        return _dt.datetime.strptime(raw[:10], "%Y-%m-%d")
    except ValueError:
        return None


def _trim_abstract(text: str | None) -> str:
    if not text:
        return ""
    # Strip HTML tags conservatively + collapse whitespace.
    cleaned = re.sub(r"<[^>]+>", " ", text)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if len(cleaned) > _ABSTRACT_MAX_CHARS:
        cleaned = cleaned[: _ABSTRACT_MAX_CHARS - 1].rstrip() + "…"
    return cleaned


def _local(tag: str) -> str:
    """Strip namespace from an ElementTree tag."""
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def parse_rss_or_atom(xml_bytes: bytes, source_name: str) -> list[dict]:
    """Parse an RSS 2.0 or Atom feed and return a list of normalized entries.

    Returned dicts have keys: ``title``, ``url``, ``authors`` (list[str]),
    ``abstract``, ``published`` (datetime | None). Year filtering and
    record-shaping happens in :func:`fetch_blog`.
    """
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return []

    entries: list[dict] = []
    root_tag = _local(root.tag).lower()

    # RSS: <rss><channel><item>...
    if root_tag == "rss":
        for item in root.iter("item"):
            entries.append(_parse_rss_item(item))
    # Atom: <feed><entry>...
    elif root_tag == "feed":
        for entry in root.findall(f"{{{_ATOM_NS}}}entry"):
            entries.append(_parse_atom_entry(entry))
    else:
        # Fallback: walk by local-name; some feeds use weird wrappers.
        for elt in root.iter():
            ln = _local(elt.tag).lower()
            if ln == "item":
                entries.append(_parse_rss_item(elt))
            elif ln == "entry":
                entries.append(_parse_atom_entry(elt))

    # Drop entries with no link or title.
    return [e for e in entries if e and e.get("title") and e.get("url")]


def _parse_rss_item(item: ET.Element) -> dict:
    title = (item.findtext("title") or "").strip()
    link = (item.findtext("link") or "").strip()
    pub_date = _parse_pub_date(item.findtext("pubDate"))
    description = item.findtext("description") or ""

    # author / dc:creator
    authors: list[str] = []
    raw_author = (item.findtext("author") or "").strip()
    if raw_author:
        authors.append(raw_author)
    for child in item:
        if _local(child.tag).lower() == "creator" and (child.text or "").strip():
            authors.append(child.text.strip())

    return {
        "title": title,
        "url": link,
        "authors": authors,
        "abstract": _trim_abstract(description),
        "published": pub_date,
    }


def _parse_atom_entry(entry: ET.Element) -> dict:
    title = (entry.findtext(f"{{{_ATOM_NS}}}title") or "").strip()

    # <link href="..." rel="alternate"/> preferred, else first <link>.
    link = ""
    chosen = None
    for link_el in entry.findall(f"{{{_ATOM_NS}}}link"):
        if link_el.get("rel", "alternate") == "alternate":
            chosen = link_el
            break
    if chosen is None:
        links = entry.findall(f"{{{_ATOM_NS}}}link")
        chosen = links[0] if links else None
    if chosen is not None:
        link = chosen.get("href", "").strip()

    pub_date = (
        _parse_pub_date(entry.findtext(f"{{{_ATOM_NS}}}published"))
        or _parse_pub_date(entry.findtext(f"{{{_ATOM_NS}}}updated"))
    )
    summary = (
        entry.findtext(f"{{{_ATOM_NS}}}summary")
        or entry.findtext(f"{{{_ATOM_NS}}}content")
        or ""
    )

    authors = [
        (a.findtext(f"{{{_ATOM_NS}}}name") or "").strip()
        for a in entry.findall(f"{{{_ATOM_NS}}}author")
    ]
    authors = [a for a in authors if a]

    return {
        "title": title,
        "url": link,
        "authors": authors,
        "abstract": _trim_abstract(summary),
        "published": pub_date,
    }


# ---------------------------------------------------------------------------
# per-source fetch
# ---------------------------------------------------------------------------


def _http_get(url: str, timeout: int) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def fetch_blog(
    source: dict,
    year_start: int,
    year_end: int,
    max_entries: int,
    timeout: int,
) -> list[dict]:
    """Fetch and parse a single Tier 3 blog feed.

    Returns a list of partially-shaped dicts with keys ``title``, ``url``,
    ``authors``, ``abstract``, ``published``. The caller (``main``) finalises
    them into the output JSONL schema (cite_key, source_type, etc).
    """
    feed_url = source.get("feed_url")
    if not feed_url:
        return []

    xml_bytes = _http_get(feed_url, timeout=timeout)
    parsed = parse_rss_or_atom(xml_bytes, source.get("name", "unknown"))

    in_range: list[dict] = []
    for entry in parsed:
        pub: _dt.datetime | None = entry.get("published")
        if pub is None:
            # Without a date we can't year-filter. Be permissive: keep it,
            # let the LLM filter handle it downstream. Year defaults to 0
            # so the caller can decide.
            entry["year"] = 0
            in_range.append(entry)
            continue
        year = pub.year
        if year_start <= year <= year_end:
            entry["year"] = year
            in_range.append(entry)
        if len(in_range) >= max_entries:
            break

    return in_range[:max_entries]


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def _shape_record(
    source: dict,
    raw: dict,
    out_of_scope_warning: bool,
    fetched_at: str,
) -> dict:
    title = raw.get("title", "").strip()
    year = int(raw.get("year") or 0)
    cite_key = make_cite_key(source.get("name", "blog"), year, title)
    return {
        "title": title,
        "authors": raw.get("authors", []) or [],
        "year": year,
        "url": raw.get("url", ""),
        "abstract": raw.get("abstract", "") or "",
        "venue": source.get("name", ""),
        "source_type": "blog",
        "source_tier": 3,
        "cite_key": cite_key,
        "fetched_at": fetched_at,
        "out_of_scope_warning": bool(out_of_scope_warning),
    }


def _split_keywords(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [k.strip() for k in raw.split(",") if k and k.strip()]


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Fetch Tier 3 curated personal blogs into a JSONL file.",
    )
    parser.add_argument("--output", required=True, help="Output JSONL path.")
    parser.add_argument(
        "--registry",
        default=str(_DEFAULT_REGISTRY),
        help="Path to source_registry.json (default: tools/source_registry.json).",
    )
    parser.add_argument("--year-start", type=int, default=2021)
    parser.add_argument("--year-end", type=int, default=2026)
    parser.add_argument(
        "--max-per-source",
        type=int,
        default=50,
        help="Cap on entries pulled per blog (default: 50).",
    )
    parser.add_argument(
        "--exclude-keywords",
        default="",
        help=(
            "Comma-separated list of brief.scope.exclude keywords. "
            "Used to flag records whose source declares warn_on_exclude."
        ),
    )
    parser.add_argument("--timeout", type=int, default=15)
    parser.add_argument("--verbose", action="store_true")
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = _build_parser().parse_args(list(argv) if argv is not None else None)

    sources = load_tier3_sources(args.registry)
    exclude_keywords = _split_keywords(args.exclude_keywords)
    fetched_at = _dt.datetime.now(_dt.timezone.utc).isoformat()

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    total_written = 0
    failures: list[tuple[str, str]] = []

    with output_path.open("w", encoding="utf-8") as fh:
        for source in sources:
            name = source.get("name", "<unnamed>")
            warn_flag = should_warn(source, exclude_keywords)
            try:
                if args.verbose:
                    print(f"[blog_fetch] fetching {name}", file=sys.stderr)
                raw_entries = fetch_blog(
                    source,
                    year_start=args.year_start,
                    year_end=args.year_end,
                    max_entries=args.max_per_source,
                    timeout=args.timeout,
                )
            except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
                failures.append((name, f"{type(exc).__name__}: {exc}"))
                if args.verbose:
                    print(
                        f"[blog_fetch] WARN {name} fetch failed: {exc}",
                        file=sys.stderr,
                    )
                continue
            except Exception as exc:  # noqa: BLE001 — isolate per-source failures
                failures.append((name, f"{type(exc).__name__}: {exc}"))
                if args.verbose:
                    print(
                        f"[blog_fetch] WARN {name} unexpected error: {exc}",
                        file=sys.stderr,
                    )
                continue

            for raw in raw_entries:
                record = _shape_record(source, raw, warn_flag, fetched_at)
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
                total_written += 1

    if args.verbose or failures:
        print(
            f"[blog_fetch] wrote {total_written} records to {output_path} "
            f"({len(sources) - len(failures)}/{len(sources)} sources OK)",
            file=sys.stderr,
        )
        for name, msg in failures:
            print(f"[blog_fetch]   FAIL {name}: {msg}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
