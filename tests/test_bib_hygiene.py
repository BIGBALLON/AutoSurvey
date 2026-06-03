"""Tests for tools/bib_hygiene.py — bibliography integrity pass.

Two layers:
  - Pure-function unit tests (parse_bib, parse_fields, escape_field_value,
    collect_cited_keys) — fast, deterministic.
  - CLI integration tests — construct a small `<run_dir>/5_paper/{main.tex,
    references.bib}` and exercise --check / --fix exit codes plus the
    .bak backup contract.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import bib_hygiene as bh  # noqa: E402


# ---------------------------------------------------------------------------
# parse_bib + parse_fields — pure functions
# ---------------------------------------------------------------------------


def test_parse_bib_basic_entry():
    text = """@article{kaplan2020,
  author = {Kaplan, Jared},
  title = {Scaling Laws},
  year = {2020}
}
"""
    entries = bh.parse_bib(text)
    assert len(entries) == 1
    e = entries[0]
    assert e["type"] == "article"
    assert e["key"] == "kaplan2020"
    assert e["fields"]["author"] == "Kaplan, Jared"
    assert e["fields"]["title"] == "Scaling Laws"
    assert e["fields"]["year"] == "2020"


def test_parse_bib_lowercases_type():
    """`@Article{...}` must yield type='article' (lowercased)."""
    entries = bh.parse_bib(r"@INPROCEEDINGS{x, title = {T}, author = {A}, year = {2024}}")
    assert entries[0]["type"] == "inproceedings"


def test_parse_bib_handles_nested_braces():
    """Title with nested {} must not derail the depth counter — the early
    bug was right here."""
    text = r"""@article{x,
  title = {On {LLaMA} and {Mixtral} Models},
  author = {A},
  year = {2024}
}
"""
    entries = bh.parse_bib(text)
    assert len(entries) == 1
    assert entries[0]["fields"]["title"] == "On {LLaMA} and {Mixtral} Models"


def test_parse_fields_lowercases_field_names():
    """`Author = {...}` must yield fields['author']."""
    fields = bh.parse_fields("Author = {A}, Title = {T}, YEAR = {2024}")
    assert "author" in fields
    assert "title" in fields
    assert "year" in fields


def test_parse_fields_accepts_quoted_values():
    fields = bh.parse_fields(r'author = "Jane Doe", title = "Foo Bar", year = 2024')
    assert fields["author"] == "Jane Doe"
    assert fields["title"] == "Foo Bar"
    assert fields["year"] == "2024"


# ---------------------------------------------------------------------------
# escape_field_value — char-class behaviour matrix
# ---------------------------------------------------------------------------


def test_escape_field_value_escapes_amp_pct_hash_in_title():
    new_val, changes = bh.escape_field_value("title", "Foo & Bar 100% C#")
    assert "&" not in new_val.replace(r"\&", "")
    assert r"\&" in new_val
    assert r"\%" in new_val
    assert r"\#" in new_val
    assert any("&" in c for c in changes)
    assert any("%" in c for c in changes)
    assert any("#" in c for c in changes)


def test_escape_field_value_does_not_double_escape():
    """Already-escaped \\& must not become \\\\&."""
    new_val, changes = bh.escape_field_value("title", r"Foo \& Bar")
    assert r"\\&" not in new_val
    # No change should be made — input is already escaped
    assert all("&" not in c for c in changes)


def test_escape_field_value_url_only_escapes_amp():
    """In url/doi fields, % and # are LEGAL in URL syntax — only & is escaped."""
    new_val, _ = bh.escape_field_value("url",
        "https://x.com/?a=1&b=2#frag")
    assert r"\&" in new_val
    # # and % must survive intact in URLs
    assert "#frag" in new_val


def test_escape_field_value_normalises_unicode():
    """em-dash → ---, smart quotes → `` '' regardless of field type."""
    new_val, _ = bh.escape_field_value("title", "Foo—bar “quoted”")
    assert "---" in new_val
    assert "``" in new_val and "''" in new_val


# ---------------------------------------------------------------------------
# collect_cited_keys — multi-file scan
# ---------------------------------------------------------------------------


def _make_run_dir(tmp_path: Path, *,
                  main_cites: list[str] | None = None,
                  section_cites: dict[str, list[str]] | None = None,
                  bib_entries: list[tuple[str, dict[str, str]]] | None = None,
                  ) -> Path:
    """Helper: build a minimal `<run_dir>/5_paper/{main.tex,sections/*.tex,
    references.bib}` for hygiene testing."""
    rd = tmp_path / "run"
    pap = rd / "5_paper"
    sec = pap / "sections"
    sec.mkdir(parents=True)

    if main_cites:
        pap.joinpath("main.tex").write_text(
            "\n".join(f"\\cite{{{','.join(g) if isinstance(g, list) else g}}}"
                     for g in main_cites)
        )
    else:
        pap.joinpath("main.tex").write_text("% empty main\n")

    for name, cites in (section_cites or {}).items():
        sec.joinpath(name).write_text(
            "\n".join(f"\\citep{{{c}}}" for c in cites)
        )

    if bib_entries:
        bib_lines = []
        for key, fields in bib_entries:
            field_lines = ",\n".join(f"  {n} = {{{v}}}" for n, v in fields.items())
            bib_lines.append("@article{" + key + ",\n" + field_lines + "\n}")
        pap.joinpath("references.bib").write_text("\n\n".join(bib_lines) + "\n")

    return rd


def test_collect_cited_keys_walks_main_and_sections(tmp_path):
    rd = _make_run_dir(
        tmp_path,
        main_cites=[["a", "b"]],
        section_cites={"01.tex": ["c"], "02.tex": ["d"]},
    )
    keys = bh.collect_cited_keys(rd)
    assert keys == {"a", "b", "c", "d"}


def test_collect_cited_keys_missing_files_silent(tmp_path):
    """No 5_paper/ at all → empty set, no exception."""
    keys = bh.collect_cited_keys(tmp_path)
    assert keys == set()


# ---------------------------------------------------------------------------
# CLI integration — exit codes + .bak backup contract
# ---------------------------------------------------------------------------


def _run_cli(run_dir: Path, *flags: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(ROOT / "tools" / "bib_hygiene.py"),
         str(run_dir), *flags],
        capture_output=True, text=True,
    )


def test_cli_check_clean_returns_0(tmp_path):
    rd = _make_run_dir(
        tmp_path,
        main_cites=["good"],
        bib_entries=[("good", {"author": "A", "title": "T", "year": "2024"})],
    )
    res = _run_cli(rd, "--check")
    assert res.returncode == 0, f"stdout:\n{res.stdout}\nstderr:\n{res.stderr}"
    assert "No critical issues" in res.stdout
    # Dead/phantom counts shown
    assert "Phantom cites:" in res.stdout
    assert "Dead entries:" in res.stdout


def test_cli_check_phantom_returns_1(tmp_path):
    rd = _make_run_dir(
        tmp_path,
        main_cites=["missing_in_bib"],
        bib_entries=[("present", {"author": "A", "title": "T", "year": "2024"})],
    )
    res = _run_cli(rd, "--check")
    assert res.returncode == 1
    assert "Critical" in res.stdout or "CRITICAL" in res.stdout
    assert "missing_in_bib" in res.stdout


def test_cli_check_missing_required_returns_1(tmp_path):
    rd = _make_run_dir(
        tmp_path,
        main_cites=["incomplete"],
        bib_entries=[("incomplete", {"title": "T"})],   # no author, no year
    )
    res = _run_cli(rd, "--check")
    assert res.returncode == 1
    assert "Missing required" in res.stdout
    assert "incomplete" in res.stdout


def test_cli_check_dead_only_returns_0(tmp_path):
    """Dead entries are NOT critical → exit 0."""
    rd = _make_run_dir(
        tmp_path,
        main_cites=["used"],
        bib_entries=[
            ("used",   {"author": "A", "title": "T", "year": "2024"}),
            ("unused", {"author": "B", "title": "U", "year": "2024"}),
        ],
    )
    res = _run_cli(rd, "--check")
    assert res.returncode == 0
    assert "unused" in res.stdout
    # Output uses column alignment; relax to a regex-friendly check
    import re
    assert re.search(r"Dead entries:\s+1", res.stdout), (
        f"expected 'Dead entries: 1' (any whitespace), got:\n{res.stdout}"
    )


def test_cli_fix_creates_bak_and_rewrites(tmp_path):
    """--fix must (1) make references.bib.bak == original, (2) rewrite
    references.bib, (3) drop dead entries, (4) escape & in titles."""
    rd = _make_run_dir(
        tmp_path,
        main_cites=["used"],
        bib_entries=[
            ("used",   {"author": "A & B", "title": "Foo & Bar 100%",
                        "year": "2024"}),
            ("unused", {"author": "C", "title": "U", "year": "2024"}),
        ],
    )
    bib_path = rd / "5_paper" / "references.bib"
    original = bib_path.read_text()

    res = _run_cli(rd, "--fix")
    assert res.returncode == 0, f"stdout:\n{res.stdout}\nstderr:\n{res.stderr}"

    # .bak backup exists and matches original
    bak = bib_path.with_suffix(".bib.bak")
    assert bak.exists()
    assert bak.read_text() == original

    rewritten = bib_path.read_text()
    # Dead entry removed
    assert "unused" not in rewritten
    # & in title escaped
    assert r"\&" in rewritten
    # Original entry survives
    assert "used" in rewritten


def test_cli_missing_bib_returns_2(tmp_path):
    """No references.bib → exit 2 (input error)."""
    (tmp_path / "5_paper").mkdir()
    res = _run_cli(tmp_path, "--check")
    assert res.returncode == 2
    assert "not found" in res.stderr


def test_cli_report_writes_json(tmp_path):
    """--report writes a structured JSON findings file."""
    rd = _make_run_dir(
        tmp_path,
        main_cites=["only_in_tex"],
        bib_entries=[("only_in_bib",
                       {"author": "A", "title": "T", "year": "2024"})],
    )
    report_path = tmp_path / "report.json"
    res = _run_cli(rd, "--check", "--report", str(report_path))
    assert res.returncode == 1   # phantom present
    assert report_path.exists()
    report = json.loads(report_path.read_text())
    assert "only_in_tex" in report["phantom_cites"]
    assert "only_in_bib" in report["dead_entries"]


# ---------------------------------------------------------------------------
# Annotated bibliography — design_rationale → annote injection
# (shared-references/structural-template.md invariant 3)
# ---------------------------------------------------------------------------


def _attach_card_md(run_dir: Path, cite_key: str, design_rationale: str) -> None:
    cards_dir = run_dir / "1_search" / "cards"
    cards_dir.mkdir(parents=True, exist_ok=True)
    cards_dir.joinpath(f"{cite_key}.md").write_text(
        f"# {cite_key}\n\n"
        "## Insights\n"
        f"- design_rationale: {design_rationale}\n"
    )


def _attach_filtered(run_dir: Path, cite_key: str, abstract: str) -> None:
    f = run_dir / "1_search" / "filtered.jsonl"
    f.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps({"cite_key": cite_key, "abstract": abstract})
    if f.exists():
        f.write_text(f.read_text() + line + "\n")
    else:
        f.write_text(line + "\n")


def test_load_card_annotations_prefers_card_markdown(tmp_path):
    rd = _make_run_dir(
        tmp_path,
        main_cites=["alice2024"],
        bib_entries=[("alice2024", {"author": "A", "title": "T", "year": "2024"})],
    )
    _attach_card_md(rd, "alice2024", "Introduces method X with property Y.")
    _attach_filtered(rd, "alice2024", "Some abstract sentence.")

    out = bh.load_card_annotations(rd)
    # Card markdown wins over the filtered.jsonl abstract fallback
    assert out["alice2024"] == "Introduces method X with property Y."


def test_load_card_annotations_falls_back_to_abstract_first_sentence(tmp_path):
    rd = _make_run_dir(
        tmp_path,
        main_cites=["bob2024"],
        bib_entries=[("bob2024", {"author": "B", "title": "T", "year": "2024"})],
    )
    _attach_filtered(
        rd,
        "bob2024",
        "We propose a new method. The method outperforms baselines on benchmark Z.",
    )
    out = bh.load_card_annotations(rd)
    assert out["bob2024"] == "We propose a new method."


def test_load_card_annotations_clips_long_text(tmp_path):
    rd = _make_run_dir(
        tmp_path,
        main_cites=["c2024"],
        bib_entries=[("c2024", {"author": "C", "title": "T", "year": "2024"})],
    )
    long_text = "Sentence one. " * 80  # well above 280 chars
    _attach_card_md(rd, "c2024", long_text)
    out = bh.load_card_annotations(rd)
    assert len(out["c2024"]) <= 280
    assert out["c2024"].endswith(".") or out["c2024"].endswith("…")


def test_cli_fix_injects_annote_from_card_design_rationale(tmp_path):
    rd = _make_run_dir(
        tmp_path,
        main_cites=["alice2024", "bob2024"],
        bib_entries=[
            ("alice2024", {"author": "A", "title": "T1", "year": "2024"}),
            ("bob2024",   {"author": "B", "title": "T2", "year": "2024"}),
        ],
    )
    _attach_card_md(rd, "alice2024", "Establishes baseline X.")
    _attach_card_md(rd, "bob2024",   "Extends X with feature Y.")

    res = _run_cli(rd, "--fix")
    assert res.returncode == 0, res.stdout + res.stderr

    bib_text = (rd / "5_paper" / "references.bib").read_text()
    assert "annote = {Establishes baseline X.}" in bib_text
    assert "annote = {Extends X with feature Y.}" in bib_text
    assert "Annotated entries:" in res.stdout
    assert "100%" in res.stdout  # 2/2 entries annotated


def test_cli_fix_preserves_existing_annote(tmp_path):
    """If an entry already has annote=, we keep it verbatim and don't double-add."""
    rd = _make_run_dir(
        tmp_path,
        main_cites=["alice2024"],
        bib_entries=[("alice2024", {
            "author": "A", "title": "T1", "year": "2024",
            "annote": "Hand-curated annotation.",
        })],
    )
    _attach_card_md(rd, "alice2024", "AI-suggested annotation.")

    res = _run_cli(rd, "--fix")
    assert res.returncode == 0, res.stdout + res.stderr
    bib_text = (rd / "5_paper" / "references.bib").read_text()
    assert "annote = {Hand-curated annotation.}" in bib_text
    assert "AI-suggested annotation." not in bib_text


def test_cli_fix_reports_unavailable_annotations(tmp_path):
    """An entry with no card / no abstract gets listed as unavailable."""
    rd = _make_run_dir(
        tmp_path,
        main_cites=["unknown2024"],
        bib_entries=[("unknown2024", {
            "author": "U", "title": "T", "year": "2024",
        })],
    )
    res = _run_cli(rd, "--check")
    assert res.returncode == 0
    assert "no annotation available for" in res.stdout
    assert "unknown2024" in res.stdout
