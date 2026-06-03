#!/usr/bin/env python3
"""Fetch GitHub READMEs, HuggingFace model cards, and generic web pages.

Companion to ``tech_report_fetch.py`` / ``blog_fetch.py``: where those tools
iterate a registry, this one is **input-driven** -- it consumes URL lists
supplied by the parsed brief (``sources.github_repos``, ``sources.model_cards``,
``sources.websites``) and emits a single JSONL stream that matches the rest of
the AutoSurvey paper-record schema.

Each emitted record carries a ``body_md_preview`` (first 2000 chars) for the
extract stage; the full markdown/text body is persisted alongside the JSONL
under ``<output>.bodies/<cite_key>.md`` so /survey-write can read it later.

Stdlib only -- no ``requests``, no ``beautifulsoup4``.

Example
-------
::

    python3 tools/website_fetch.py \\
        --output runs/run-x/1_search/websites.jsonl \\
        --github-repos-file runs/run-x/0_brief/github_repos.txt \\
        --model-cards-file  runs/run-x/0_brief/model_cards.txt \\
        --websites-file     runs/run-x/0_brief/websites.txt \\
        --year-start 2021 --year-end 2026 --verbose
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from html import unescape
from pathlib import Path
from typing import Callable, Iterable

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_GITHUB_USER_AGENT = (
    "AutoSurvey-website-fetch/1.0 "
    "(https://github.com/wanshuiyin/AutoSurvey)"
)
# Realistic browser-style UA for generic websites that block scrapers.
_GENERIC_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

_BODY_MAX_CHARS = 50_000
_PREVIEW_CHARS = 2_000
_ABSTRACT_CHARS = 500

# Errors we treat as "this URL is unreachable, move on".
_NETWORK_ERRORS: tuple[type[BaseException], ...] = (
    urllib.error.URLError,
    urllib.error.HTTPError,
    TimeoutError,
    OSError,
    json.JSONDecodeError,
)


# ---------------------------------------------------------------------------
# Slug / cite-key helpers
# ---------------------------------------------------------------------------


def _slug(text: str) -> str:
    """Lowercase, collapse non-alphanumeric runs to single hyphens, trim."""
    if not text:
        return ""
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = re.sub(r"-+", "-", text)
    return text.strip("-")


def _now_iso() -> str:
    """Return current UTC time as an ISO-8601 string (seconds resolution)."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


# ---------------------------------------------------------------------------
# URL parsers
# ---------------------------------------------------------------------------


def parse_github_url(url: str) -> tuple[str, str]:
    """Return ``(org, repo)`` for a GitHub repo URL.

    Accepts ``https://github.com/<org>/<repo>``, optionally with a trailing
    ``.git`` suffix or extra path segments (the first two are used).
    """
    parsed = urllib.parse.urlparse(url)
    parts = [p for p in parsed.path.split("/") if p]
    if len(parts) < 2:
        raise ValueError(f"not a GitHub repo URL: {url!r}")
    org = parts[0]
    repo = parts[1]
    if repo.endswith(".git"):
        repo = repo[:-4]
    return org, repo


def parse_hf_url(url: str) -> tuple[str, str]:
    """Return ``(org, model)`` for a HuggingFace model URL."""
    parsed = urllib.parse.urlparse(url)
    parts = [p for p in parsed.path.split("/") if p]
    if len(parts) < 2:
        raise ValueError(f"not a HuggingFace model URL: {url!r}")
    return parts[0], parts[1]


def _hf_cite_key(org: str, model: str) -> str:
    """``hf-<org>-<model>``, with ``_`` and ``.`` normalized to ``-``."""
    raw = f"{org}-{model}".lower()
    raw = raw.replace("_", "-").replace(".", "-").replace("/", "-")
    raw = re.sub(r"-+", "-", raw).strip("-")
    return f"hf-{raw}"


def _gh_cite_key(org: str, repo: str) -> str:
    """``gh-<org>-<repo>`` (lowercased; existing hyphens preserved)."""
    return f"gh-{org.lower()}-{repo.lower()}"


def _web_cite_key(url: str) -> str:
    """``web-<host-slug>-<path-slug>`` for a generic page URL."""
    parsed = urllib.parse.urlparse(url)
    host_slug = _slug(parsed.netloc)
    path_slug = _slug(parsed.path) or "index"
    return f"web-{host_slug}-{path_slug}"


# ---------------------------------------------------------------------------
# Markdown / HTML extraction helpers
# ---------------------------------------------------------------------------


def extract_first_h1(markdown: str) -> str | None:
    """Return the text of the first ``# Heading`` line, or ``None`` if absent.

    A line counts as H1 only if it begins with exactly one ``#`` followed by
    whitespace; ``## subtitle`` is ignored.
    """
    if not markdown:
        return None
    for line in markdown.splitlines():
        stripped = line.strip()
        m = re.match(r"^#\s+(.+?)\s*$", stripped)
        if m:
            return m.group(1).strip()
    return None


def _strip_inline_html(html: str, *, strip_nav_footer: bool = False) -> str:
    """Remove tags/scripts/style and collapse whitespace into plain text."""
    if not html:
        return ""
    # Always drop <script>/<style> blocks entirely.
    cleaned = re.sub(
        r"<script\b[^>]*>.*?</script>",
        " ",
        html,
        flags=re.DOTALL | re.IGNORECASE,
    )
    cleaned = re.sub(
        r"<style\b[^>]*>.*?</style>",
        " ",
        cleaned,
        flags=re.DOTALL | re.IGNORECASE,
    )
    if strip_nav_footer:
        cleaned = re.sub(
            r"<nav\b[^>]*>.*?</nav>",
            " ",
            cleaned,
            flags=re.DOTALL | re.IGNORECASE,
        )
        cleaned = re.sub(
            r"<footer\b[^>]*>.*?</footer>",
            " ",
            cleaned,
            flags=re.DOTALL | re.IGNORECASE,
        )
    # Strip remaining tags.
    text = re.sub(r"<[^>]+>", " ", cleaned)
    text = unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def extract_main_text(html: str) -> str:
    """Readability-style extraction: ``<article>`` > ``<main>`` > body-minus-chrome.

    Always strips ``<script>`` and ``<style>`` content. When falling back to
    ``<body>`` it also drops ``<nav>`` and ``<footer>`` blocks.
    """
    if not html:
        return ""

    m = re.search(r"<article\b[^>]*>(.*?)</article>", html, re.DOTALL | re.IGNORECASE)
    if m:
        return _strip_inline_html(m.group(1))

    m = re.search(r"<main\b[^>]*>(.*?)</main>", html, re.DOTALL | re.IGNORECASE)
    if m:
        return _strip_inline_html(m.group(1))

    m = re.search(r"<body\b[^>]*>(.*?)</body>", html, re.DOTALL | re.IGNORECASE)
    body_html = m.group(1) if m else html
    return _strip_inline_html(body_html, strip_nav_footer=True)


def _extract_html_title(html: str) -> str | None:
    """Pull a page title from ``<title>`` or ``<meta property="og:title">``."""
    m = re.search(r"<title\b[^>]*>(.*?)</title>", html, re.DOTALL | re.IGNORECASE)
    if m:
        title = unescape(m.group(1)).strip()
        title = re.sub(r"\s+", " ", title)
        if title:
            return title
    m = re.search(
        r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']',
        html,
        re.IGNORECASE,
    )
    if m:
        return unescape(m.group(1)).strip()
    return None


def _extract_pub_year(html: str) -> int | None:
    """Try ``<meta article:published_time>``; fall back to a visible YYYY-MM-DD."""
    m = re.search(
        r'<meta[^>]+property=["\']article:published_time["\']'
        r'[^>]+content=["\']([^"\']+)["\']',
        html,
        re.IGNORECASE,
    )
    if m:
        try:
            return int(m.group(1)[:4])
        except ValueError:
            pass
    # Visible ISO date as a last-resort heuristic.
    m = re.search(r"\b(20\d{2})-\d{2}-\d{2}\b", html)
    if m:
        return int(m.group(1))
    return None


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------


def _http_get_bytes(
    url: str,
    *,
    timeout: int,
    user_agent: str = _GITHUB_USER_AGENT,
) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": user_agent})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _http_get_text(
    url: str,
    *,
    timeout: int,
    user_agent: str = _GITHUB_USER_AGENT,
) -> str:
    return _http_get_bytes(url, timeout=timeout, user_agent=user_agent).decode(
        "utf-8", errors="replace"
    )


def _http_get_json(
    url: str,
    *,
    timeout: int,
    user_agent: str = _GITHUB_USER_AGENT,
) -> dict:
    return json.loads(_http_get_text(url, timeout=timeout, user_agent=user_agent))


# ---------------------------------------------------------------------------
# GitHub
# ---------------------------------------------------------------------------


def fetch_github_readme(repo_url: str, timeout: int) -> dict | None:
    """Fetch a GitHub repo's README and return a normalized record dict.

    1. GET ``api.github.com/repos/<org>/<repo>`` to learn the default branch
       (and the ``description`` / ``pushed_at`` metadata). On any API failure
       we fall back to ``main``.
    2. GET ``raw.githubusercontent.com/<org>/<repo>/<branch>/<filename>`` for
       ``README.md``, ``README.rst``, then ``readme.md``. First success wins.
    3. Returns ``None`` if no README variant is reachable.
    """
    org, repo = parse_github_url(repo_url)
    api_url = f"https://api.github.com/repos/{org}/{repo}"

    default_branch = "main"
    description = ""
    pushed_at: str | None = None
    try:
        api_data = _http_get_json(api_url, timeout=timeout)
        default_branch = (api_data.get("default_branch") or "main").strip() or "main"
        description = (api_data.get("description") or "").strip()
        pushed_at = api_data.get("pushed_at")
    except _NETWORK_ERRORS:
        # Spec: on API failure, default to "main" (already set).
        pass

    body_md: str | None = None
    for filename in ("README.md", "README.rst", "readme.md"):
        raw_url = (
            f"https://raw.githubusercontent.com/"
            f"{org}/{repo}/{default_branch}/{filename}"
        )
        try:
            body_md = _http_get_text(raw_url, timeout=timeout)
            break
        except _NETWORK_ERRORS:
            continue

    if body_md is None:
        return None

    title = extract_first_h1(body_md) or f"{org}/{repo}"

    if pushed_at:
        try:
            year = int(pushed_at[:4])
        except (TypeError, ValueError):
            year = datetime.now(timezone.utc).year
    else:
        year = datetime.now(timezone.utc).year

    full_body = body_md[:_BODY_MAX_CHARS]

    return {
        "title": title,
        "authors": [],
        "year": year,
        "url": f"https://github.com/{org}/{repo}",
        "description": description,
        "abstract": full_body[:_ABSTRACT_CHARS],
        "body_md_preview": full_body[:_PREVIEW_CHARS],
        "venue": "GitHub",
        "source_type": "github_readme",
        "cite_key": _gh_cite_key(org, repo),
        "fetched_at": _now_iso(),
        "_full_body": full_body,
    }


# ---------------------------------------------------------------------------
# HuggingFace
# ---------------------------------------------------------------------------


def fetch_hf_model_card(model_url: str, timeout: int) -> dict | None:
    """Fetch a HF model card: API metadata + raw README, with cardData fallback."""
    org, model = parse_hf_url(model_url)
    api_url = f"https://huggingface.co/api/models/{org}/{model}"

    api_data: dict = {}
    try:
        api_data = _http_get_json(api_url, timeout=timeout)
    except _NETWORK_ERRORS:
        api_data = {}

    last_modified = api_data.get("lastModified")
    tags = list(api_data.get("tags") or [])
    downloads = api_data.get("downloads")
    card_data = api_data.get("cardData") or {}
    model_id = api_data.get("modelId") or f"{org}/{model}"

    body_md: str | None = None
    raw_url = f"https://huggingface.co/{org}/{model}/raw/main/README.md"
    try:
        body_md = _http_get_text(raw_url, timeout=timeout)
    except _NETWORK_ERRORS:
        if card_data:
            # Spec: fall back to cardData when README is unavailable.
            body_md = (
                "<!-- README unavailable; rendered from HuggingFace API cardData -->\n\n"
                + json.dumps(card_data, indent=2, ensure_ascii=False)
            )

    if body_md is None:
        return None

    title = model_id

    if last_modified:
        try:
            year = int(last_modified[:4])
        except (TypeError, ValueError):
            year = datetime.now(timezone.utc).year
    else:
        year = datetime.now(timezone.utc).year

    full_body = body_md[:_BODY_MAX_CHARS]

    return {
        "title": title,
        "authors": [],
        "year": year,
        "url": f"https://huggingface.co/{org}/{model}",
        "tags": tags,
        "downloads": downloads if isinstance(downloads, int) else 0,
        "abstract": full_body[:_ABSTRACT_CHARS],
        "body_md_preview": full_body[:_PREVIEW_CHARS],
        "venue": "HuggingFace",
        "source_type": "model_card",
        "cite_key": _hf_cite_key(org, model),
        "fetched_at": _now_iso(),
        "_full_body": full_body,
    }


# ---------------------------------------------------------------------------
# Generic websites
# ---------------------------------------------------------------------------


def fetch_generic_website(url: str, timeout: int) -> dict | None:
    """Fetch a generic web page and run readability-style extraction."""
    html = _http_get_text(url, timeout=timeout, user_agent=_GENERIC_USER_AGENT)
    if not html:
        return None

    title = _extract_html_title(html) or url
    body_text = extract_main_text(html)
    year = _extract_pub_year(html) or datetime.now(timezone.utc).year

    full_body = body_text[:_BODY_MAX_CHARS]

    return {
        "title": title,
        "authors": [],
        "year": year,
        "url": url,
        "abstract": full_body[:_ABSTRACT_CHARS],
        "body_md_preview": full_body[:_PREVIEW_CHARS],
        "venue": "Web",
        "source_type": "website",
        "cite_key": _web_cite_key(url),
        "fetched_at": _now_iso(),
        "_full_body": full_body,
    }


# ---------------------------------------------------------------------------
# CLI / main
# ---------------------------------------------------------------------------


def _read_url_list(arg_value: str | None) -> list[str]:
    """Read newline-separated URLs from a path or ``-`` for stdin.

    Blank lines and ``#``-prefixed comments are ignored.
    """
    if not arg_value:
        return []
    if arg_value == "-":
        text = sys.stdin.read()
    else:
        text = Path(arg_value).read_text(encoding="utf-8")
    out: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        out.append(line)
    return out


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=(
            "Fetch GitHub READMEs, HuggingFace model cards, and generic web "
            "pages cited in a brief. Emits one JSONL record per URL plus "
            "full-body markdown under <output>.bodies/."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--output",
        required=True,
        help="Path to write JSONL output (one record per line).",
    )
    p.add_argument(
        "--github-repos-file",
        default=None,
        help="Newline-separated GitHub repo URLs (or '-' for stdin).",
    )
    p.add_argument(
        "--model-cards-file",
        default=None,
        help="Newline-separated HuggingFace model URLs (or '-' for stdin).",
    )
    p.add_argument(
        "--websites-file",
        default=None,
        help="Newline-separated generic website URLs (or '-' for stdin).",
    )
    p.add_argument("--year-start", type=int, default=2021)
    p.add_argument("--year-end", type=int, default=2026)
    p.add_argument("--timeout", type=int, default=20)
    p.add_argument("--verbose", action="store_true")
    return p


def _safe_call(
    label: str,
    url: str,
    fn: Callable[[str, int], dict | None],
    timeout: int,
    verbose: bool,
) -> dict | None:
    """Invoke ``fn(url, timeout)``; log + swallow any exception."""
    try:
        rec = fn(url, timeout)
    except _NETWORK_ERRORS as exc:
        print(f"[skip] {label} {url}: {exc}", file=sys.stderr)
        return None
    except ValueError as exc:
        print(f"[skip] {label} {url}: {exc}", file=sys.stderr)
        return None
    except Exception as exc:  # pragma: no cover - last-resort guard
        print(f"[skip] {label} {url}: unexpected error {exc}", file=sys.stderr)
        return None

    if rec is None:
        print(f"[skip] {label} {url}: no body returned", file=sys.stderr)
        return None
    if verbose:
        print(f"[ok]   {label} {url}", file=sys.stderr)
    return rec


def _emit(rec: dict, out_fh, bodies_dir: Path) -> None:
    """Write the full body to disk and append the JSONL record (minus body)."""
    full_body = rec.pop("_full_body", "")
    body_path = bodies_dir / f"{rec['cite_key']}.md"
    body_path.write_text(full_body, encoding="utf-8")
    out_fh.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _year_in_range(rec: dict, year_start: int, year_end: int) -> bool:
    year = rec.get("year")
    if not isinstance(year, int):
        return True  # don't filter records with unknown years
    return year_start <= year <= year_end


def main(argv: Iterable[str] | None = None) -> int:
    args = _build_parser().parse_args(list(argv) if argv is not None else None)

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    bodies_dir = output_path.parent / f"{output_path.name}.bodies"
    bodies_dir.mkdir(parents=True, exist_ok=True)

    github_urls = _read_url_list(args.github_repos_file)
    hf_urls = _read_url_list(args.model_cards_file)
    web_urls = _read_url_list(args.websites_file)

    total_urls = len(github_urls) + len(hf_urls) + len(web_urls)
    if total_urls == 0:
        print(
            "no URLs supplied; pass --github-repos-file / --model-cards-file / "
            "--websites-file",
            file=sys.stderr,
        )
        # Still create the empty output so downstream stages don't break.
        output_path.touch()
        return 0

    succeeded = 0
    failed = 0
    out_of_range = 0

    work: list[tuple[str, str, Callable[[str, int], dict | None]]] = []
    work += [("github_readme", u, fetch_github_readme) for u in github_urls]
    work += [("model_card", u, fetch_hf_model_card) for u in hf_urls]
    work += [("website", u, fetch_generic_website) for u in web_urls]

    with output_path.open("w", encoding="utf-8") as out_fh:
        for label, url, fn in work:
            rec = _safe_call(label, url, fn, args.timeout, args.verbose)
            if rec is None:
                failed += 1
                continue
            if not _year_in_range(rec, args.year_start, args.year_end):
                out_of_range += 1
                if args.verbose:
                    print(
                        f"[drop] {label} {url}: year {rec.get('year')} "
                        f"outside [{args.year_start}, {args.year_end}]",
                        file=sys.stderr,
                    )
                continue
            _emit(rec, out_fh, bodies_dir)
            succeeded += 1

    print(
        f"Fetched {succeeded} records "
        f"({failed} failures, {out_of_range} out-of-range) "
        f"from {total_urls} URLs.",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
