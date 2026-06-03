#!/usr/bin/env python3
"""bib_generator.py — Converts filtered.jsonl to references.bib.

Usage:
    python3 bib_generator.py filtered.jsonl > references.bib
    python3 bib_generator.py filtered.jsonl --output references.bib

Each line in filtered.jsonl must be a JSON object with at minimum:
  - paper_id   : unique identifier (used as cite key base)
  - title      : paper title
  - authors    : list of str (["First Last", ...])  OR  list of {"name": "..."} dicts
  - year       : int or str

Optional fields (used when present):
  - arxiv_id   : "2401.12345" → sets @article eprint/archivePrefix
  - doi        : "10.xxxxx/yyy"
  - venue      : conference/journal name
  - booktitle  : overrides venue for @inproceedings
  - volume     : journal volume
  - pages      : "1--10"
  - url        : paper URL
  - abstract   : stored as note if present (truncated to 500 chars)
"""

import sys
import json
import re
import argparse
import unicodedata
from pathlib import Path


# Standalone letters that have no base+combining decomposition and must map to
# a dedicated LaTeX command. Without this, pdflatex (T1/lmodern) emits
# "could not represent character" and drops the glyph from the bibliography.
_LATEX_SPECIAL_LETTERS = {
    "ı": r"{\i}", "ȷ": r"{\j}", "ł": r"{\l}", "Ł": r"{\L}",
    "ø": r"{\o}", "Ø": r"{\O}", "ß": r"{\ss}", "æ": r"{\ae}", "Æ": r"{\AE}",
    "œ": r"{\oe}", "Œ": r"{\OE}", "å": r"{\aa}", "Å": r"{\AA}",
    "đ": r"{\dj}", "Đ": r"{\DJ}", "ð": r"{\dh}", "Ð": r"{\DH}",
    "þ": r"{\th}", "Þ": r"{\TH}",
}

# Combining diacritical marks (Unicode) → LaTeX accent command.
_LATEX_COMBINING = {
    "\u0300": "`", "\u0301": "'", "\u0302": "^", "\u0303": "~",
    "\u0304": "=", "\u0306": "u", "\u0307": ".", "\u0308": '"',
    "\u0309": "h", "\u030a": "r", "\u030b": "H", "\u030c": "v",
    "\u0327": "c", "\u0328": "k", "\u0323": "d", "\u0331": "b",
}


def _unicode_to_latex(text: str) -> str:
    """Convert non-ASCII letters to LaTeX so the bibliography compiles under
    pdflatex's T1 encoding. Accented letters are NFD-decomposed into a base
    char + an accent command; standalone special letters use a fixed map;
    anything still non-ASCII is dropped rather than emitted raw."""
    out: list[str] = []
    for ch in text:
        if ord(ch) < 128:
            out.append(ch)
            continue
        if ch in _LATEX_SPECIAL_LETTERS:
            out.append(_LATEX_SPECIAL_LETTERS[ch])
            continue
        decomp = unicodedata.normalize("NFD", ch)
        base = decomp[0]
        marks = decomp[1:]
        if ord(base) < 128 and marks and all(m in _LATEX_COMBINING for m in marks):
            acc = base
            for m in marks:
                cmd = _LATEX_COMBINING[m]
                acc = f"\\{cmd}{{{acc}}}"
            out.append(acc)
            continue
        # Last resort: keep ASCII-foldable letters, otherwise drop the char.
        ascii_fold = "".join(c for c in decomp if ord(c) < 128)
        out.append(ascii_fold)
    return "".join(out)


# ── cite key generation ────────────────────────────────────────────────────

def _first_author_last(authors: list) -> str:
    """Extract last name of first author."""
    if not authors:
        return "anon"
    first = authors[0]
    name = first.get("name", first) if isinstance(first, dict) else first
    parts = str(name).strip().split()
    return parts[-1].lower() if parts else "anon"


def _clean_key_token(s: str) -> str:
    """Keep only alphanumeric chars (ASCII)."""
    return re.sub(r"[^a-z0-9]", "", s.lower())


def make_cite_key(paper: dict, seen: set) -> str:
    """Generate a unique BibTeX cite key: <firstauthor><year><titleword>."""
    author = _clean_key_token(_first_author_last(paper.get("authors", [])))
    year = str(paper.get("year", "0000"))[:4]
    title = paper.get("title", "")
    # first meaningful word from title (skip stopwords)
    stopwords = {"a", "an", "the", "of", "on", "in", "for", "and", "to", "with",
                 "is", "are", "at", "via", "from", "by", "towards"}
    title_words = [_clean_key_token(w) for w in title.split()
                   if _clean_key_token(w) and _clean_key_token(w) not in stopwords]
    title_tok = title_words[0] if title_words else "paper"

    base = f"{author}{year}{title_tok}"
    key = base
    suffix = 1
    while key in seen:
        suffix += 1
        key = f"{base}{suffix}"
    seen.add(key)
    return key


# ── BibTeX field helpers ───────────────────────────────────────────────────

def _brace(value: str) -> str:
    """Wrap value in double braces (protects capitalisation).

    Non-ASCII letters are converted to LaTeX accent/command sequences so the
    bibliography compiles under pdflatex's T1 font encoding.
    """
    v = str(value).replace("{", r"\{").replace("}", r"\}")
    v = _unicode_to_latex(v)
    return "{" + v + "}"


def _format_authors(authors: list) -> str:
    """Return BibTeX-style author string."""
    names = []
    for a in authors:
        name = a.get("name", a) if isinstance(a, dict) else a
        names.append(str(name).strip())
    return " and ".join(names) if names else "Unknown"


_CONFERENCE_HINTS = frozenset({
    "proceedings", "conference", "workshop", "symposium",
    "acl", "emnlp", "naacl", "neurips", "icml", "iclr",
    "cvpr", "iccv", "eccv", "aaai", "ijcai", "sigkdd",
    "www", "chi",  # short tokens — must match on word boundary
})

# Tokenise venue strings on non-alphanumeric boundaries before matching.
# Substring match (`"chi" in venue`) was previously firing on
# "Journal of Statistical Physics" (-> "chi" in "machine") and on any
# venue containing "machine". Word-level set membership is safe for
# all hints whether long ("proceedings") or short ("chi", "www", "acl").
_VENUE_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _entry_type(paper: dict) -> str:
    """Decide @article vs @inproceedings vs @misc."""
    if paper.get("arxiv_id") and not paper.get("venue") and not paper.get("doi"):
        return "misc"
    venue = (paper.get("booktitle") or paper.get("venue") or "").lower()
    venue_tokens = set(_VENUE_TOKEN_RE.findall(venue))
    if venue_tokens & _CONFERENCE_HINTS:
        return "inproceedings"
    if paper.get("venue") or paper.get("volume"):
        return "article"
    return "misc"


def paper_to_bibtex(paper: dict, cite_key: str) -> str:
    """Convert a paper dict to a BibTeX entry string."""
    etype = _entry_type(paper)
    lines = [f"@{etype}{{{cite_key},"]

    lines.append(f"  title = {_brace(paper.get('title', 'Untitled'))},")
    lines.append(f"  author = {_brace(_format_authors(paper.get('authors', [])))},")
    lines.append(f"  year = {_brace(str(paper.get('year', '0000')))},")

    if paper.get("doi"):
        lines.append(f"  doi = {_brace(paper['doi'])},")
    if paper.get("arxiv_id"):
        lines.append(f"  eprint = {_brace(paper['arxiv_id'])},")
        lines.append(f"  archivePrefix = {_brace('arXiv')},")
        lines.append(f"  primaryClass = {_brace(paper.get('arxiv_cat', 'cs.LG'))},")
    if paper.get("url"):
        lines.append(f"  url = {_brace(paper['url'])},")

    venue = paper.get("booktitle") or paper.get("venue")
    if venue:
        field = "booktitle" if etype == "inproceedings" else "journal"
        lines.append(f"  {field} = {_brace(venue)},")
    if paper.get("volume"):
        lines.append(f"  volume = {_brace(paper['volume'])},")
    if paper.get("pages"):
        lines.append(f"  pages = {_brace(paper['pages'])},")
    if paper.get("abstract"):
        truncated = paper["abstract"][:500] + ("..." if len(paper["abstract"]) > 500 else "")
        lines.append(f"  abstract = {_brace(truncated)},")

    lines.append("}")
    return "\n".join(lines)


# ── main ──────────────────────────────────────────────────────────────────

def convert(input_path: str, output_path: str | None = None,
            update_input: bool = False):
    papers = []
    with open(input_path) as f:
        for line in f:
            line = line.strip()
            if line:
                papers.append(json.loads(line))

    seen_keys: set = set()
    # Map paper_id → cite_key for downstream consumers
    id_to_key: dict = {}
    bib_entries: list[str] = []

    for paper in papers:
        key = make_cite_key(paper, seen_keys)
        id_to_key[paper.get("paper_id", key)] = key
        paper["cite_key"] = key  # write back into paper dict (in-memory only)
        # Also expose `paper_id` (canonical) so downstream tools that read the
        # canonical field name don't have to alias-match cite_key. If the input
        # already carried a paper_id, leave it; otherwise mirror the cite_key.
        paper.setdefault("paper_id", key)
        bib_entries.append(paper_to_bibtex(paper, key))

    bib_content = "\n\n".join(bib_entries) + "\n"

    if output_path:
        Path(output_path).write_text(bib_content)
        # Also write id→key mapping as JSON sidecar
        sidecar = Path(output_path).with_suffix(".cite_keys.json")
        sidecar.write_text(json.dumps(id_to_key, indent=2))
        print(f"Wrote {len(bib_entries)} entries → {output_path}", file=sys.stderr)
        print(f"Cite key map → {sidecar}", file=sys.stderr)
        # Optional: write cite_key back into the input filtered.jsonl.
        # Disabled by default — input files should not be silently mutated when
        # the user only asked for an output .bib. Pipeline drivers that need
        # the cite_key field on filtered.jsonl must opt in via --update-input.
        if update_input:
            with open(input_path, "w") as f:
                for p in papers:
                    f.write(json.dumps(p, ensure_ascii=False) + "\n")
            print(f"Updated cite_key field in {input_path}", file=sys.stderr)
    else:
        print(bib_content, end="")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Convert filtered.jsonl → references.bib")
    parser.add_argument("input", help="Path to filtered.jsonl")
    parser.add_argument("--output", "-o", help="Output .bib path (default: stdout)")
    parser.add_argument(
        "--update-input",
        action="store_true",
        help="Also write the assigned cite_key field back into the input "
             "filtered.jsonl file (in-place). Off by default to keep --output "
             "side-effect free.",
    )
    args = parser.parse_args()
    convert(args.input, args.output, update_input=args.update_input)
