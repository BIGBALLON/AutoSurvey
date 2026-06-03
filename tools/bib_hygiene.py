#!/usr/bin/env python3
"""
bib_hygiene.py — survey-write Bibliography integrity pass.

Runs four checks against `5_paper/references.bib`:

  1. Dead-entry removal — drop @entries whose key is never cited in any .tex
  2. Phantom-cite detection — every cite key must resolve to a bib entry
  3. Special-character escaping — escape unescaped & % # in field VALUES
                                    (skips field NAMES and url field content)
  4. Required-field validation — every entry must have author, title, year

Modes:
  --check       — read-only; print findings, exit 0 if clean else 1
  --fix         — apply fixes in-place (with .bak backup)

Usage:
  bib_hygiene.py <run_dir> --check
  bib_hygiene.py <run_dir> --fix
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

REQUIRED_FIELDS = ("author", "title", "year")


def parse_bib(text: str) -> list[dict]:
    """Lightweight BibTeX parser. Returns list of dicts with keys: 'type', 'key', 'fields',
    'raw_start', 'raw_end' (so we can splice fixes back in)."""
    entries = []
    # Match @TYPE{KEY, ...}
    entry_re = re.compile(r"@(\w+)\s*\{\s*([^,\s]+)\s*,", re.DOTALL)
    for m in entry_re.finditer(text):
        start = m.start()
        type_ = m.group(1).lower()
        key = m.group(2)
        # Find matching closing brace using depth counter starting from after the opening {
        depth = 1
        i = m.end()
        while i < len(text) and depth > 0:
            c = text[i]
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
            i += 1
        end = i  # position after closing }
        body = text[m.end():end - 1]  # content between { and }
        # Parse fields
        fields = parse_fields(body)
        entries.append({
            "type": type_,
            "key": key,
            "fields": fields,
            "raw_start": start,
            "raw_end": end,
            "body": body,
        })
    return entries


def parse_fields(body: str) -> dict[str, str]:
    """Parse BibTeX fields from entry body (without the surrounding braces).
    Robust to nested {} in values and to whitespace."""
    fields: dict[str, str] = {}
    pos = 0
    n = len(body)
    while pos < n:
        # Skip leading whitespace and commas
        while pos < n and body[pos] in " \t\n\r,":
            pos += 1
        if pos >= n:
            break
        # Read field name
        m = re.match(r"(\w+)\s*=\s*", body[pos:])
        if not m:
            break
        name = m.group(1).lower()
        pos += m.end()
        # Read field value: either {...} (with depth) or "..." (with escaping)
        if pos >= n:
            break
        if body[pos] == "{":
            depth = 1
            i = pos + 1
            while i < n and depth > 0:
                if body[i] == "{":
                    depth += 1
                elif body[i] == "}":
                    depth -= 1
                i += 1
            value = body[pos + 1:i - 1]
            pos = i
        elif body[pos] == '"':
            i = pos + 1
            while i < n and body[i] != '"':
                if body[i] == "\\":
                    i += 2
                else:
                    i += 1
            value = body[pos + 1:i]
            pos = i + 1
        else:
            # Numeric / unquoted (year = 2023)
            m2 = re.match(r"([^,\s}]+)", body[pos:])
            if not m2:
                break
            value = m2.group(1)
            pos += m2.end()
        fields[name] = value.strip()
    return fields


def collect_cited_keys(run_dir: Path) -> set[str]:
    r"""Find every \cite{...} key across main.tex, sections/, and figures/.

    Figure and table fragments (e.g. the cross-cutting matrix and per-family
    comparison tables under ``figures/``) cite one key per row; if they are
    not scanned, those keys look ``dead`` and get pruned from the bib,
    turning every table citation into an undefined reference at compile.
    """
    paper_dir = run_dir / "5_paper"
    tex_files = [
        paper_dir / "main.tex",
        *sorted((paper_dir / "sections").glob("*.tex")),
        *sorted((paper_dir / "figures").rglob("*.tex")),
    ]
    cited: set[str] = set()
    cite_re = re.compile(r"\\cite[a-zA-Z]*\{([^}]+)\}")
    for fp in tex_files:
        if not fp.exists():
            continue
        text = fp.read_text()
        for m in cite_re.finditer(text):
            for key in m.group(1).split(","):
                key = key.strip()
                if key:
                    cited.add(key)
    return cited


def escape_field_value(name: str, value: str) -> tuple[str, list[str]]:
    r"""Escape unescaped & % # in a field value, normalize unicode dashes/quotes.
    Skip url field (uses \url{} or doc-package handles &).
    Returns (new_value, list_of_changes)."""
    changes: list[str] = []
    new_value = value

    # 1. Unicode normalization (always, even for url field)
    unicode_subs = [
        ("—", "---"),
        ("–", "--"),
        ("‐", "-"),
        ("‑", "-"),
        ("…", "\\ldots{}"),
        ("“", "``"),
        ("”", "''"),
        ("‘", "`"),
        ("’", "'"),
    ]
    for old, repl in unicode_subs:
        if old in new_value:
            count = new_value.count(old)
            new_value = new_value.replace(old, repl)
            changes.append(f"unicode {old!r}→{repl!r} ({count}x)")

    # 2. Special-char escaping (skip url/doi)
    if name in ("url", "doi"):
        # URL fields: still escape & defensively (some bibtex styles fail otherwise)
        before = new_value
        new_value = re.sub(r"(?<!\\)&", r"\\&", new_value)
        if new_value != before:
            changes.append("escaped & in url/doi")
        return new_value, changes

    for char, escaped in (("&", r"\&"), ("%", r"\%"), ("#", r"\#")):
        pattern = rf"(?<!\\){re.escape(char)}"
        count_before = len(re.findall(pattern, new_value))
        if count_before > 0:
            new_value = re.sub(pattern, escaped, new_value)
            changes.append(f"escaped {count_before} unescaped {char!r}")
    return new_value, changes


def load_card_annotations(run_dir: Path) -> dict[str, str]:
    """Return ``{cite_key: 1-2-sentence factual annotation}`` for every paper
    card we can find. Order of preference per cite_key:

    1. ``1_search/cards/<cite_key>.md`` — ``design_rationale:`` line in the
       Insights section. This is the verbatim source the writer prompt sees,
       so re-using it for the bibliography keeps annotations and prose
       grounded in the same evidence.
    2. ``1_search/cards.jsonl`` — ``insights.design_rationale`` field.
    3. ``1_search/filtered.jsonl`` — first sentence of the abstract.

    Annotations are clipped to ~ 280 chars (enough for 1–2 sentences;
    avoids blowing up bib length and keeps NeurIPS-style entries compact).
    """
    out: dict[str, str] = {}

    cards_dir = run_dir / "1_search" / "cards"
    if cards_dir.exists():
        for p in cards_dir.glob("*.md"):
            cite_key = p.stem
            text = p.read_text(encoding="utf-8", errors="replace")
            m = re.search(
                r"^\s*-\s*design_rationale:\s*(.+)$",
                text,
                re.MULTILINE | re.IGNORECASE,
            )
            if m:
                out[cite_key] = _trim_annotation(m.group(1))

    cards_jsonl = run_dir / "1_search" / "cards.jsonl"
    if cards_jsonl.exists():
        for lineno, line in enumerate(cards_jsonl.read_text().splitlines(), 1):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError as exc:
                print(f"[WARN] {cards_jsonl.name}:{lineno} JSON decode failed: {exc.msg}",
                      file=sys.stderr)
                continue
            cite_key = rec.get("cite_key")
            if not cite_key or cite_key in out:
                continue
            insights = rec.get("insights") or {}
            note = (insights.get("design_rationale")
                    or rec.get("_decision_summary")
                    or rec.get("decision_summary"))
            if note:
                out[cite_key] = _trim_annotation(note)

    filtered = run_dir / "1_search" / "filtered.jsonl"
    if filtered.exists():
        for lineno, line in enumerate(filtered.read_text().splitlines(), 1):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError as exc:
                print(f"[WARN] {filtered.name}:{lineno} JSON decode failed: {exc.msg}",
                      file=sys.stderr)
                continue
            cite_key = rec.get("cite_key")
            if not cite_key or cite_key in out:
                continue
            abstract = (rec.get("abstract") or "").strip()
            if abstract:
                # First sentence; tolerate missing punctuation.
                m = re.match(r"(.+?[.!?])(?:\s|$)", abstract)
                first = m.group(1) if m else abstract
                out[cite_key] = _trim_annotation(first)

    return out


def _trim_annotation(text: str) -> str:
    """Collapse whitespace + clip to a 1–2-sentence ceiling."""
    t = " ".join(text.split())
    if len(t) > 280:
        # Try to clip at a sentence boundary.
        clipped = t[:280]
        m = re.search(r"\.[^.]*$", clipped)
        if m:
            t = clipped[: m.start() + 1]
        else:
            t = clipped.rsplit(" ", 1)[0] + "…"
    return t


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("run_dir", type=Path)
    p.add_argument("--check", action="store_true")
    p.add_argument("--fix", action="store_true")
    p.add_argument("--report", type=Path)
    args = p.parse_args()
    if not args.fix:
        args.check = True

    run_dir = args.run_dir.expanduser().resolve()
    bib_path = run_dir / "5_paper" / "references.bib"
    if not bib_path.exists():
        print(f"ERROR: references.bib not found: {bib_path}", file=sys.stderr)
        return 2

    text = bib_path.read_text()
    entries = parse_bib(text)
    cited = collect_cited_keys(run_dir)
    entry_keys = {e["key"] for e in entries}
    annotations = load_card_annotations(run_dir)

    findings: dict = {
        "total_entries": len(entries),
        "total_cited_keys": len(cited),
        "dead_entries": sorted(entry_keys - cited),
        "phantom_cites": sorted(cited - entry_keys),
        "missing_required_fields": [],
        "char_escapes_applied": {},
        "annotations_added": 0,
        "annotations_already_present": 0,
        "annotations_unavailable": [],
    }

    # Check required fields
    for e in entries:
        missing = [f for f in REQUIRED_FIELDS if f not in e["fields"]]
        if missing:
            findings["missing_required_fields"].append({"key": e["key"], "missing": missing})

    # Annotation diagnostics (always computed, even in --check mode)
    for e in entries:
        if e["key"] in findings["dead_entries"]:
            continue
        if "annote" in e["fields"] and e["fields"]["annote"].strip():
            findings["annotations_already_present"] += 1
        elif e["key"] in annotations:
            pass  # will be added in fix mode
        else:
            findings["annotations_unavailable"].append(e["key"])

    # Apply fixes
    if args.fix:
        # Backup
        shutil.copy2(bib_path, bib_path.with_suffix(".bib.bak"))

        # Build new bib: skip dead entries, escape chars in surviving ones,
        # and inject `annote` from the run's cards / abstracts where possible.
        new_entries_text: list[str] = []
        for e in entries:
            if e["key"] in findings["dead_entries"]:
                continue
            # Reconstruct entry with escaped field values
            new_fields: dict[str, str] = {}
            entry_changes: list[str] = []
            for fname, fval in e["fields"].items():
                new_val, changes = escape_field_value(fname, fval)
                new_fields[fname] = new_val
                if changes:
                    entry_changes.extend([f"{fname}: {c}" for c in changes])
            # Inject annote if the entry doesn't already have one and the
            # run carries a usable annotation for this cite_key.
            if not new_fields.get("annote", "").strip():
                ann = annotations.get(e["key"])
                if ann:
                    # Re-run the field-value escape logic on the annotation.
                    esc, _changes = escape_field_value("annote", ann)
                    new_fields["annote"] = esc
                    findings["annotations_added"] += 1
            if entry_changes:
                findings["char_escapes_applied"][e["key"]] = entry_changes
            # Format entry
            lines = [f"@{e['type']}{{{e['key']},"]
            for fname, fval in new_fields.items():
                lines.append(f"  {fname} = {{{fval}}},")
            lines.append("}")
            new_entries_text.append("\n".join(lines))

        new_text = "\n\n".join(new_entries_text) + "\n"
        bib_path.write_text(new_text)

    # Print summary
    print("=" * 60)
    print(f"bib_hygiene — {'fix' if args.fix else 'check'} mode")
    print("=" * 60)
    print(f"  Entries:               {findings['total_entries']}")
    print(f"  Cited keys (in .tex):  {findings['total_cited_keys']}")
    print(f"  Dead entries:          {len(findings['dead_entries'])}"
          f"{'  (removed)' if args.fix else ''}")
    if findings["dead_entries"]:
        print(f"    → {findings['dead_entries']}")
    print(f"  Phantom cites:         {len(findings['phantom_cites'])}"
          f"{'  (CRITICAL)' if findings['phantom_cites'] else ''}")
    if findings["phantom_cites"]:
        print(f"    → {findings['phantom_cites']}")
    print(f"  Missing required:      {len(findings['missing_required_fields'])}")
    if findings["missing_required_fields"]:
        for r in findings["missing_required_fields"]:
            print(f"    → {r['key']}: missing {r['missing']}")
    print(f"  Char escapes applied:  {len(findings['char_escapes_applied'])}")
    if findings["char_escapes_applied"]:
        for k, changes in list(findings["char_escapes_applied"].items())[:5]:
            print(f"    → {k}: {changes}")

    n_alive = findings["total_entries"] - len(findings["dead_entries"])
    n_with_annote = (findings["annotations_added"]
                     + findings["annotations_already_present"])
    ann_ratio = (n_with_annote / n_alive) if n_alive else 0.0
    print(f"  Annotated entries:     {n_with_annote}/{n_alive} "
          f"({ann_ratio:.0%})"
          f"{'  (added: '+str(findings['annotations_added'])+')' if args.fix else ''}")
    if findings["annotations_unavailable"][:5]:
        print(f"    no annotation available for: "
              f"{findings['annotations_unavailable'][:5]}"
              f"{'…' if len(findings['annotations_unavailable']) > 5 else ''}")

    if args.report:
        args.report.write_text(json.dumps(findings, indent=2))
        print(f"  Report → {args.report}")

    # Exit code
    critical = bool(findings["phantom_cites"]) or bool(findings["missing_required_fields"])
    if critical:
        print("\n❌ Critical issues remain — phantom cites or missing required fields")
        return 1
    print(f"\n✅ {'Fixes applied' if args.fix else 'No critical issues'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
