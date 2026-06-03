#!/usr/bin/env python3
"""
extract_paper_card.py — paper-card extraction backend.

The /survey-write per-section inner loop drives extraction lazily (see
shared-references/claims-contract.md) and calls this tool in three
deterministic sub-modes:

  --validate-schema  Validate an agent-produced schema candidate JSON and
                     write the canonical ``brief.derived_schema.json``.
  --fetch-all        Download paper texts (S2 OpenAccess → arXiv PDF →
                     HTML scrape → abstract fallback) into a cache dir.
                     I/O-only; no LLM calls.
  --write-cards      Validate per-paper extraction JSONs (one file per
                     cite_key) against the schema and emit
                     ``cards/<cite_key>.md`` and ``cards.jsonl``.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import io
import json
import re
import sys
import urllib.error
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pypdf


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_CONCURRENCY = 8
DEFAULT_TIMEOUT = 30  # seconds for HTTP

VALID_TYPE_HINTS = {
    "int",
    "float",
    "str",
    "list[str]",
    "list[int]",
    "list[float]",
    "dict",
    "list[dict]",
}

# Single per-paper text budget. The pipeline always tries the full priority
# chain (S2 → arXiv PDF → HTML scrape → abstract fallback) and truncates the
# result to MAX_BUDGET_CHARS. There is no separate abstract-only mode: weak
# extractions always cost more downstream than the saved fetch.
MAX_BUDGET_CHARS = 40_000

LOW_COMPLETENESS_FLAG = 0.5

# Template keyword rules (longest-first within a bucket).
TEMPLATE_KEYWORDS: List[Tuple[str, List[str]]] = [
    (
        "llm-pretraining",
        [
            "pretraining",
            "pre-training",
            "language model",
            "language models",
            "moe",
            "mixture of experts",
            "scaling laws",
            "scaling law",
            "llm",
            "gpt",
            "llama",
            "transformer architecture",
            "foundation model",
        ],
    ),
    (
        "general-nlp",
        [
            "parsing",
            "summarization",
            "summarisation",
            "named entity",
            "machine translation",
            "question answering",
            "dialogue",
            "rag",
            "retrieval-augmented",
            "retrieval augmented",
            "retrieval",
        ],
    ),
    (
        "vision-models",
        [
            "vision",
            "image",
            "diffusion",
            "video",
            "multimodal",
            "generative",
        ],
    ),
]

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


# ---------------------------------------------------------------------------
# 1. Template matching (rule-based)
# ---------------------------------------------------------------------------


def _brief_text_blob(brief_parsed: Dict[str, Any]) -> str:
    """Concatenate the strings the keyword matcher should look at."""
    parts: List[str] = []
    parts.append(str(brief_parsed.get("topic", "")))
    for d in brief_parsed.get("dimensions", []) or []:
        if isinstance(d, dict):
            parts.append(str(d.get("name", "")))
            parts.append(str(d.get("description", "")))
        elif isinstance(d, str):
            parts.append(d)
    scope = brief_parsed.get("scope", {}) or {}
    for inc in scope.get("include", []) or []:
        parts.append(str(inc))
    return " ".join(parts).lower()


def match_template(brief_parsed: Dict[str, Any]) -> str:
    """Return the starting-template name picked by keyword match."""
    blob = _brief_text_blob(brief_parsed)
    for name, keywords in TEMPLATE_KEYWORDS:
        for kw in keywords:
            if kw in blob:
                return name
    return "generic"


# ---------------------------------------------------------------------------
# 2. Template loading (markdown → dict)
# ---------------------------------------------------------------------------


_GROUP_HEADER_RE = re.compile(r"^###\s+([A-Za-z_][A-Za-z0-9_]*)\s*$")
# Bullet line like ``- field_name: type # comment`` (comment optional).
_BULLET_RE = re.compile(
    r"^-\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*:\s*([^#]+?)(?:\s*#.*)?$"
)


def load_template(path: str) -> Dict[str, Dict[str, str]]:
    """Parse a markdown template into ``{group: {field: type_hint}}``."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"template not found: {path}")

    groups: Dict[str, Dict[str, str]] = {}
    current_group: Optional[str] = None
    in_field_section = False

    for raw_line in p.read_text(encoding="utf-8").splitlines():
        line = raw_line.rstrip()

        if line.startswith("## Field groups"):
            in_field_section = True
            current_group = None
            continue

        if in_field_section and line.startswith("## ") and not line.startswith(
            "## Field groups"
        ):
            break

        if not in_field_section:
            continue

        m_group = _GROUP_HEADER_RE.match(line)
        if m_group:
            current_group = m_group.group(1)
            groups.setdefault(current_group, {})
            continue

        if current_group is None:
            continue

        m_bullet = _BULLET_RE.match(line)
        if m_bullet:
            field = m_bullet.group(1).strip()
            type_hint = m_bullet.group(2).strip()
            type_hint_norm = _normalise_type_hint(type_hint)
            groups[current_group][field] = type_hint_norm

    if not groups or not any(groups.values()):
        raise ValueError(
            f"template {path} produced no (group, field) pairs; "
            "check the markdown format."
        )

    return groups


def _normalise_type_hint(hint: str) -> str:
    """Map free-form template type hints onto the canonical enum."""
    s = re.sub(r"\s+", "", hint)
    if s in VALID_TYPE_HINTS:
        return s
    if s.startswith("list["):
        inner = s[len("list[") : -1]
        if inner.startswith("dict"):
            return "list[dict]"
        if inner.startswith("str"):
            return "list[str]"
        if inner.startswith("int"):
            return "list[int]"
        if inner.startswith("float"):
            return "list[float]"
        return "list[str]"
    if s.startswith("dict"):
        return "dict"
    return s


# ---------------------------------------------------------------------------
# 3. Schema validation
# ---------------------------------------------------------------------------


def validate_schema(schema: Dict[str, Any]) -> Tuple[bool, str]:
    """Validate a schema dict.

    Accepts either ``{"groups": {...}}`` (canonical) or a bare
    ``{group: {field: type}}`` dict (loose).

    Returns ``(True, "")`` if valid; ``(False, error_message)`` otherwise.
    """
    if not isinstance(schema, dict):
        return False, "schema must be a JSON object."

    groups = schema.get("groups", schema if "groups" not in schema else None)
    if not isinstance(groups, dict):
        return False, "schema.groups must be a JSON object."

    if len(groups) == 0:
        return False, "schema must contain at least one group."

    seen_groups: set = set()
    for group_name, fields in groups.items():
        if not isinstance(group_name, str) or not group_name.strip():
            return False, f"group name must be a non-empty string: {group_name!r}"
        if group_name in seen_groups:
            return False, f"duplicate group name: {group_name}"
        seen_groups.add(group_name)

        if not isinstance(fields, dict) or len(fields) == 0:
            return (
                False,
                f"group {group_name!r} must contain at least one field.",
            )

        seen_fields: set = set()
        for field_name, type_hint in fields.items():
            if not isinstance(field_name, str) or not field_name.strip():
                return (
                    False,
                    f"field name must be a non-empty string in group "
                    f"{group_name!r}: {field_name!r}",
                )
            if field_name in seen_fields:
                return (
                    False,
                    f"duplicate field {field_name!r} in group {group_name!r}",
                )
            seen_fields.add(field_name)

            if not isinstance(type_hint, str) or type_hint not in VALID_TYPE_HINTS:
                return (
                    False,
                    f"unknown type hint {type_hint!r} for "
                    f"{group_name}.{field_name}; expected one of "
                    f"{sorted(VALID_TYPE_HINTS)}",
                )

    return True, ""


def validate_synthesis_candidate(
    candidate: Dict[str, Any],
    template: Optional[Dict[str, Dict[str, str]]] = None,
) -> Dict[str, Any]:
    """Validate an agent-produced schema candidate; return canonical form.

    The agent reads the brief + matched template and writes a JSON file with
    shape::

        {"_template_used": "<name>", "groups": {"<group>": {"<field>": "<type>"}, ...}}

    This function checks the structure and returns the validated,
    canonicalised dict.

    Args:
        candidate: the agent's candidate (parsed JSON).
        template: optional starting template. Currently unused for validation
            but accepted for callers that want to round-trip the matched
            template name.

    Raises:
        ValueError: with a precise diagnostic if validation fails.
    """
    if not isinstance(candidate, dict):
        raise ValueError("synthesis candidate must be a JSON object.")

    if "groups" not in candidate:
        # Tolerate a bare ``{group: {field: type}}`` shape and lift it.
        candidate = {"groups": candidate}

    ok, err = validate_schema(candidate)
    if not ok:
        raise ValueError(f"schema validation failed: {err}")

    # Build canonical output: keep _template_used if present, then groups.
    out: Dict[str, Any] = {}
    if "_template_used" in candidate:
        out["_template_used"] = candidate["_template_used"]
    out["groups"] = candidate["groups"]
    return out


# ---------------------------------------------------------------------------
# 4. Per-paper text fetch
# ---------------------------------------------------------------------------


class _PlainTextHTMLParser(HTMLParser):
    """Strip HTML tags down to whitespace-collapsed plain text."""

    SKIP_TAGS = {"script", "style", "noscript", "head", "nav", "footer"}

    def __init__(self) -> None:
        super().__init__()
        self._chunks: List[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if tag in self.SKIP_TAGS:
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in self.SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0:
            self._chunks.append(data)

    def get_text(self) -> str:
        joined = " ".join(self._chunks)
        return re.sub(r"\s+", " ", joined).strip()


def _http_get(url: str, timeout: int = DEFAULT_TIMEOUT) -> Optional[bytes]:
    """GET ``url`` and return the body bytes, or None on any error."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError):
        return None
    except Exception:  # pragma: no cover - unexpected
        return None


def _pdf_to_text(pdf_bytes: bytes) -> Optional[str]:
    """Extract text from a PDF byte string using pypdf."""
    try:
        reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
        chunks: List[str] = []
        for page in reader.pages:
            try:
                chunks.append(page.extract_text() or "")
            except Exception:
                continue
        return "\n".join(chunks).strip() or None
    except Exception:
        return None


def _fetch_s2_pdf(paper: Dict[str, Any], timeout: int) -> Optional[str]:
    """Try Semantic Scholar OpenAccess → download PDF → extract text."""
    paper_id = paper.get("paperId") or paper.get("paper_id")
    if not paper_id:
        return None
    api_url = (
        f"https://api.semanticscholar.org/graph/v1/paper/{paper_id}"
        "?fields=openAccessPdf"
    )
    body = _http_get(api_url, timeout=timeout)
    if body is None:
        return None
    try:
        meta = json.loads(body.decode("utf-8", errors="replace"))
    except json.JSONDecodeError:
        return None
    pdf_url = (meta.get("openAccessPdf") or {}).get("url")
    if not pdf_url:
        return None
    pdf_bytes = _http_get(pdf_url, timeout=timeout)
    if pdf_bytes is None:
        return None
    return _pdf_to_text(pdf_bytes)


def _fetch_arxiv_pdf(paper: Dict[str, Any], timeout: int) -> Optional[str]:
    """Try arXiv PDF → extract text. Returns None if no arxiv_id."""
    arxiv_id = paper.get("arxiv_id") or paper.get("arxivId")
    if not arxiv_id:
        return None
    pdf_url = f"https://arxiv.org/pdf/{arxiv_id}.pdf"
    pdf_bytes = _http_get(pdf_url, timeout=timeout)
    if pdf_bytes is None:
        return None
    return _pdf_to_text(pdf_bytes)


def _fetch_html_text(paper: Dict[str, Any], timeout: int) -> Optional[str]:
    """Fetch the paper's URL (if any non-arxiv URL is set) and strip to text."""
    url = paper.get("url")
    if not url:
        return None
    if "arxiv.org" in url:  # already covered by _fetch_arxiv_pdf
        return None
    body = _http_get(url, timeout=timeout)
    if body is None:
        return None
    try:
        text = body.decode("utf-8", errors="replace")
    except Exception:
        return None
    parser = _PlainTextHTMLParser()
    try:
        parser.feed(text)
    except Exception:
        return None
    out = parser.get_text()
    return out or None


def fetch_paper_text(
    paper: Dict[str, Any],
    cache_dir: Path,
    timeout: int = DEFAULT_TIMEOUT,
) -> Tuple[str, str]:
    """Fetch source text for one paper.

    Always runs the full priority chain
    (S2 OpenAccess → arXiv PDF → HTML scrape → abstract fallback) and
    truncates to ``MAX_BUDGET_CHARS``. Returns ``(text, source_label)``
    where ``source_label`` ∈ ``{"abstract_fallback", "s2_openaccess",
    "arxiv_pdf", "html_scrape"}``.

    Caches the result at ``cache_dir/<cite_key>.txt`` so re-runs skip the
    network. The cache file's first line is ``# source: <label>``.
    """
    cite_key = paper.get("cite_key") or paper.get("paper_id") or "unknown"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / f"{cite_key}.txt"

    if cache_path.exists():
        cached = cache_path.read_text(encoding="utf-8")
        if cached.startswith("# source: "):
            first_nl = cached.find("\n")
            if first_nl != -1:
                label = cached[len("# source: ") : first_nl].strip()
                body = cached[first_nl + 1 :]
                return body, label

    abstract = paper.get("abstract") or ""

    text: Optional[str] = None
    label: str = "abstract_fallback"

    # 1. S2 OpenAccess
    s2 = _fetch_s2_pdf(paper, timeout)
    if s2:
        text, label = s2, "s2_openaccess"

    # 2. arXiv PDF
    if text is None:
        ax = _fetch_arxiv_pdf(paper, timeout)
        if ax:
            text, label = ax, "arxiv_pdf"

    # 3. HTML scrape
    if text is None:
        html = _fetch_html_text(paper, timeout)
        if html:
            text, label = html, "html_scrape"

    # 4. Abstract fallback
    if text is None:
        text = abstract
        label = "abstract_fallback"

    # Prepend abstract if the body lacks it; truncate to the single budget.
    if abstract and abstract not in text[:1000]:
        text = abstract + "\n\n" + text
    if len(text) > MAX_BUDGET_CHARS:
        text = text[:MAX_BUDGET_CHARS]

    _write_cache(cache_path, text, label)
    return text, label


def _write_cache(path: Path, text: str, label: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"# source: {label}\n{text}", encoding="utf-8")


# ---------------------------------------------------------------------------
# 5. Per-paper extraction validation
# ---------------------------------------------------------------------------


def _coerce_value(value: Any, type_hint: str) -> Tuple[bool, Any]:
    """Best-effort coerce ``value`` to ``type_hint``.

    Returns ``(ok, value)``. ``"N/R"`` is always accepted. On failure returns
    ``(False, "N/R")`` so callers can mark the field reported.
    """
    if isinstance(value, str) and value.strip().upper() in {"N/R", "N/A", "NA"}:
        return True, "N/R"

    try:
        if type_hint == "int":
            if isinstance(value, bool):
                return False, "N/R"
            if isinstance(value, int):
                return True, value
            if isinstance(value, float) and float(value).is_integer():
                return True, int(value)
            if isinstance(value, str):
                cleaned = value.replace(",", "").replace("_", "").strip()
                return True, int(float(cleaned))
            return False, "N/R"
        if type_hint == "float":
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                return True, float(value)
            if isinstance(value, str):
                cleaned = value.replace(",", "").replace("_", "").strip()
                return True, float(cleaned)
            return False, "N/R"
        if type_hint == "str":
            if isinstance(value, str):
                return True, value
            return True, json.dumps(value)
        if type_hint == "list[str]":
            if isinstance(value, list):
                return True, [str(x) for x in value]
            return False, "N/R"
        if type_hint == "list[int]":
            if isinstance(value, list):
                out: List[int] = []
                for x in value:
                    ok, v = _coerce_value(x, "int")
                    if not ok:
                        return False, "N/R"
                    out.append(v)
                return True, out
            return False, "N/R"
        if type_hint == "list[float]":
            if isinstance(value, list):
                out_f: List[float] = []
                for x in value:
                    ok, v = _coerce_value(x, "float")
                    if not ok:
                        return False, "N/R"
                    out_f.append(v)
                return True, out_f
            return False, "N/R"
        if type_hint == "dict":
            if isinstance(value, dict):
                return True, value
            return False, "N/R"
        if type_hint == "list[dict]":
            if isinstance(value, list) and all(isinstance(x, dict) for x in value):
                return True, value
            return False, "N/R"
    except (ValueError, TypeError):
        return False, "N/R"

    return False, "N/R"


def validate_extraction(
    extraction: Any, schema: Dict[str, Any]
) -> Tuple[Dict[str, Dict[str, Any]], List[str]]:
    """Validate (and coerce) an agent-produced extraction against ``schema``.

    Returns ``(coerced_extraction, error_fields)`` where ``error_fields`` lists
    ``"group.field"`` entries whose value could not be coerced and were
    force-set to ``"N/R"``. Missing fields are filled with ``"N/R"``.

    The agent produces the extraction; this function only validates and
    coerces.
    """
    groups = schema.get("groups", {}) or {}
    coerced: Dict[str, Dict[str, Any]] = {}

    if not isinstance(extraction, dict):
        for g, fields in groups.items():
            coerced[g] = {f: "N/R" for f in fields}
        return coerced, ["<top-level not a JSON object>"]

    error_fields: List[str] = []
    for group, fields in groups.items():
        coerced[group] = {}
        sub = extraction.get(group, {})
        if not isinstance(sub, dict):
            sub = {}
        for field, type_hint in fields.items():
            if field not in sub:
                coerced[group][field] = "N/R"
                continue
            ok, val = _coerce_value(sub[field], type_hint)
            if not ok:
                coerced[group][field] = "N/R"
                error_fields.append(f"{group}.{field}")
            else:
                coerced[group][field] = val

    return coerced, error_fields


# ---------------------------------------------------------------------------
# 6. Completeness + rendering
# ---------------------------------------------------------------------------


def compute_completeness(
    extraction: Dict[str, Dict[str, Any]], schema: Dict[str, Any]
) -> Tuple[float, List[str]]:
    """Return ``(ratio, missing_fields)``."""
    groups = schema.get("groups", {}) or {}
    total = 0
    missing: List[str] = []
    for group, fields in groups.items():
        sub = extraction.get(group, {}) or {}
        for field in fields:
            total += 1
            v = sub.get(field, "N/R")
            if isinstance(v, str) and v == "N/R":
                missing.append(f"{group}.{field}")
    if total == 0:
        return 1.0, []
    completeness = 1.0 - (len(missing) / total)
    return round(completeness, 4), missing


def render_card_markdown(
    paper: Dict[str, Any],
    extraction: Dict[str, Dict[str, Any]],
    completeness: float,
    source: str,
    missing: List[str],
) -> str:
    """Render the human-readable per-paper card."""
    title = paper.get("title", "(untitled)")
    lines: List[str] = []
    lines.append(f"# {title}")
    lines.append(
        f"**Source:** {source}  **Completeness:** {completeness:.2f}"
    )
    lines.append("")

    for group, fields in extraction.items():
        lines.append(f"## {group}")
        if isinstance(fields, dict):
            for field, value in fields.items():
                lines.append(f"- {field}: {_format_value(value)}")
        else:
            lines.append(f"- (group is not a dict): {fields!r}")
        lines.append("")

    lines.append("## _meta")
    lines.append(f"- extraction_source: {source}")
    lines.append(f"- completeness: {completeness:.4f}")
    if missing:
        lines.append(f"- missing_fields: {json.dumps(missing)}")
    else:
        lines.append("- missing_fields: []")

    return "\n".join(lines).rstrip() + "\n"


def _format_value(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


# ---------------------------------------------------------------------------
# 7. Helpers shared by modes
# ---------------------------------------------------------------------------


def load_filtered(path: str) -> List[Dict[str, Any]]:
    """Load papers from filtered.jsonl (one paper per line)."""
    out: List[Dict[str, Any]] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


# ---------------------------------------------------------------------------
# 8. Mode 1: --validate-schema
# ---------------------------------------------------------------------------


def run_validate_schema(args: argparse.Namespace) -> int:
    """Validate an agent-produced candidate JSON; write canonical schema."""
    candidate_path = Path(args.candidate)
    output_path = Path(args.output)

    if not candidate_path.exists():
        print(f"Error: candidate not found: {candidate_path}", file=sys.stderr)
        return 2

    try:
        candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(
            f"Error: candidate is not valid JSON ({candidate_path}): {e}",
            file=sys.stderr,
        )
        return 1

    template: Optional[Dict[str, Dict[str, str]]] = None
    template_name = candidate.get("_template_used") if isinstance(candidate, dict) else None
    if template_name and args.templates_dir:
        templates_dir = Path(args.templates_dir)
        candidate_template = templates_dir / f"{template_name}.md"
        if candidate_template.exists():
            try:
                template = load_template(str(candidate_template))
            except (FileNotFoundError, ValueError):
                template = None

    try:
        canonical = validate_synthesis_candidate(candidate, template)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(canonical, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(
        f"Schema written to {output_path} "
        f"({len(canonical['groups'])} groups, "
        f"{sum(len(f) for f in canonical['groups'].values())} fields)."
    )
    return 0


# ---------------------------------------------------------------------------
# 9. Mode 2: --fetch-all
# ---------------------------------------------------------------------------


def _fetch_one(
    paper: Dict[str, Any],
    cache_dir: Path,
    timeout: int,
) -> Tuple[str, str]:
    """Wrapper for thread-pool calls; returns (cite_key, source_label)."""
    cite_key = paper.get("cite_key") or paper.get("paper_id") or "unknown"
    try:
        _, label = fetch_paper_text(paper, cache_dir, timeout=timeout)
    except Exception as e:  # pragma: no cover - defensive
        print(f"WARN: fetch crashed for {cite_key}: {e}", file=sys.stderr)
        label = "abstract_fallback"
    return cite_key, label


def run_fetch_all(args: argparse.Namespace) -> int:
    """Download paper texts in parallel; cache to ``--cache-dir``."""
    filtered_path = Path(args.filtered)
    cache_dir = Path(args.cache_dir)

    if not filtered_path.exists():
        print(f"Error: filtered not found: {filtered_path}", file=sys.stderr)
        return 2

    papers = load_filtered(str(filtered_path))
    if args.max_papers is not None:
        papers = papers[: args.max_papers]

    if not papers:
        print("Warn: no papers in filtered.jsonl; nothing to fetch.", file=sys.stderr)
        return 0

    cache_dir.mkdir(parents=True, exist_ok=True)
    concurrency = args.concurrency or DEFAULT_CONCURRENCY

    label_counts: Dict[str, int] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as ex:
        futures = [
            ex.submit(_fetch_one, p, cache_dir, args.timeout)
            for p in papers
        ]
        for fut in concurrent.futures.as_completed(futures):
            try:
                _, label = fut.result()
            except Exception as e:  # pragma: no cover
                print(f"WARN: paper fetch crashed: {e}", file=sys.stderr)
                continue
            label_counts[label] = label_counts.get(label, 0) + 1

    summary = ", ".join(f"{k}={v}" for k, v in sorted(label_counts.items()))
    print(
        f"Fetched {len(papers)} papers into {cache_dir}. Sources: {summary}."
    )
    return 0


# ---------------------------------------------------------------------------
# 10. Mode 3: --write-cards
# ---------------------------------------------------------------------------


def _read_source_label(cache_dir: Path, cite_key: str) -> str:
    """Read the cached fetched text's source label, if any."""
    cached = cache_dir / f"{cite_key}.txt"
    if not cached.exists():
        return "abstract_fallback"
    try:
        first_line = cached.read_text(encoding="utf-8").splitlines()[0]
    except (OSError, IndexError):
        return "abstract_fallback"
    if first_line.startswith("# source: "):
        return first_line[len("# source: ") :].strip() or "abstract_fallback"
    return "abstract_fallback"


def run_write_cards(args: argparse.Namespace) -> int:
    """Validate per-paper extractions and write cards.md + cards.jsonl."""
    extractions_dir = Path(args.extractions_dir)
    schema_path = Path(args.schema)
    filtered_path = Path(args.filtered)
    output_dir = Path(args.output_dir)

    for label, p in [
        ("extractions-dir", extractions_dir),
        ("schema", schema_path),
        ("filtered", filtered_path),
    ]:
        if not p.exists():
            print(f"Error: {label} not found: {p}", file=sys.stderr)
            return 2

    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    papers = load_filtered(str(filtered_path))
    paper_index: Dict[str, Dict[str, Any]] = {}
    for p in papers:
        ck = p.get("cite_key") or p.get("paper_id") or ""
        if ck:
            paper_index[ck] = p

    cards_dir = output_dir / "cards"
    cache_dir = cards_dir / "_fetched"
    cards_dir.mkdir(parents=True, exist_ok=True)

    jsonl_path = output_dir / "cards.jsonl"
    jsonl_path.write_text("", encoding="utf-8")

    completeness_values: List[float] = []
    low_completeness: List[str] = []
    written = 0

    for ext_file in sorted(extractions_dir.glob("*.json")):
        cite_key = ext_file.stem
        try:
            extraction_raw = json.loads(ext_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            print(
                f"WARN: skipping {ext_file.name}: invalid JSON ({e})",
                file=sys.stderr,
            )
            continue

        coerced, error_fields = validate_extraction(extraction_raw, schema)
        if error_fields:
            print(
                f"WARN: {cite_key}: forced N/R on {', '.join(error_fields)}",
                file=sys.stderr,
            )

        completeness, missing = compute_completeness(coerced, schema)
        source = _read_source_label(cache_dir, cite_key)

        paper = paper_index.get(cite_key, {"cite_key": cite_key})
        md = render_card_markdown(paper, coerced, completeness, source, missing)
        (cards_dir / f"{cite_key}.md").write_text(md, encoding="utf-8")

        record = {
            "cite_key": cite_key,
            "title": paper.get("title", ""),
            "extraction": coerced,
            "extraction_source": source,
            "extraction_completeness": completeness,
            "missing_fields": missing,
        }
        with open(jsonl_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

        completeness_values.append(completeness)
        if completeness < LOW_COMPLETENESS_FLAG:
            low_completeness.append(cite_key)
        written += 1

    avg = (
        round(sum(completeness_values) / len(completeness_values), 4)
        if completeness_values
        else 0.0
    )
    flagged = ", ".join(low_completeness) if low_completeness else "(none)"
    print(
        f"Wrote {written} cards. Average completeness: {avg * 100:.1f}%. "
        f"Flagged low-completeness: [{flagged}]."
    )
    return 0


# ---------------------------------------------------------------------------
# 11. CLI entry point
# ---------------------------------------------------------------------------


def _parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="extract_paper_card.py",
        description=(
            "Paper-card extraction. Three deterministic sub-modes that the "
            "Claude Code agent calls in sequence (driven by /survey-write's "
            "per-section inner loop). The agent supplies the "
            "LLM-thinking parts; this tool validates, fetches, and writes."
        ),
    )

    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--validate-schema",
        action="store_true",
        help="Validate an agent-produced schema candidate; emit canonical schema.",
    )
    mode.add_argument(
        "--fetch-all",
        action="store_true",
        help="Download paper texts (S2 → arXiv → HTML → abstract) into a cache dir.",
    )
    mode.add_argument(
        "--write-cards",
        action="store_true",
        help="Validate per-paper extraction JSONs; write cards.jsonl + cards/<key>.md.",
    )

    # --validate-schema arguments
    parser.add_argument(
        "--candidate",
        help="(--validate-schema) Path to the agent's synthesis candidate JSON.",
    )
    parser.add_argument(
        "--templates-dir",
        default=None,
        help="(--validate-schema) Optional directory of reference templates.",
    )

    # --fetch-all arguments
    parser.add_argument(
        "--cache-dir",
        help="(--fetch-all) Directory to cache fetched paper texts.",
    )
    parser.add_argument(
        "--max-papers",
        type=int,
        default=None,
        help="(--fetch-all) Limit number of papers fetched (API budget; "
        "not a paper-length cap).",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=None,
        help="(--fetch-all) Override the default fetch concurrency "
        f"({DEFAULT_CONCURRENCY}).",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT,
        help="(--fetch-all) Per-request HTTP timeout in seconds.",
    )

    # --write-cards arguments
    parser.add_argument(
        "--extractions-dir",
        help="(--write-cards) Directory of <cite_key>.json agent-produced extractions.",
    )
    parser.add_argument(
        "--schema",
        help="(--write-cards) Path to brief.derived_schema.json.",
    )
    parser.add_argument(
        "--output-dir",
        help="(--write-cards) Destination 1_search/ directory.",
    )

    # Shared
    parser.add_argument(
        "--filtered",
        help="(--fetch-all, --write-cards) Path to 1_search/filtered.jsonl.",
    )
    parser.add_argument(
        "--output",
        help="(--validate-schema) Path to write canonical brief.derived_schema.json.",
    )

    return parser.parse_args(argv)


def _print_usage_and_exit() -> int:
    print(
        "extract_paper_card.py: choose one of the 3 modes:\n"
        "  --validate-schema --candidate <path> --output <path>\n"
        "  --fetch-all       --filtered <path> --cache-dir <path>\n"
        "  --write-cards     --extractions-dir <path> --schema <path> "
        "--filtered <path> --output-dir <path>\n"
        "\nRun with --help for full options.",
        file=sys.stderr,
    )
    return 1


def main(argv: Optional[List[str]] = None) -> int:
    args = _parse_args(argv)

    if args.validate_schema:
        if not args.candidate or not args.output:
            print(
                "Error: --validate-schema requires --candidate and --output.",
                file=sys.stderr,
            )
            return 1
        return run_validate_schema(args)

    if args.fetch_all:
        if not args.filtered or not args.cache_dir:
            print(
                "Error: --fetch-all requires --filtered and --cache-dir.",
                file=sys.stderr,
            )
            return 1
        return run_fetch_all(args)

    if args.write_cards:
        if (
            not args.extractions_dir
            or not args.schema
            or not args.filtered
            or not args.output_dir
        ):
            print(
                "Error: --write-cards requires --extractions-dir, --schema, "
                "--filtered, --output-dir.",
                file=sys.stderr,
            )
            return 1
        return run_write_cards(args)

    return _print_usage_and_exit()


if __name__ == "__main__":
    sys.exit(main())
