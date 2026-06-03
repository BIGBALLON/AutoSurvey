"""Tests for tools/bib_generator.py — filtered.jsonl → references.bib.

Two layers:
  - Pure-function unit tests (`_first_author_last`, `_clean_key_token`,
    `make_cite_key`, `paper_to_bibtex`, `_entry_type`, `_format_authors`)
  - End-to-end CLI tests via the `convert(input_path, output_path)`
    in-process entry point — no subprocess.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import bib_generator as bg  # noqa: E402


# ---------------------------------------------------------------------------
# _first_author_last + _clean_key_token (pure helpers)
# ---------------------------------------------------------------------------


def test_first_author_last_string_list():
    assert bg._first_author_last(["Jared Kaplan", "Sam McCandlish"]) == "kaplan"


def test_first_author_last_dict_list():
    """authors as list of {"name": ...} dicts (some sources emit that shape)."""
    assert bg._first_author_last([{"name": "Jared Kaplan"}]) == "kaplan"


def test_first_author_last_empty_returns_anon():
    assert bg._first_author_last([]) == "anon"


def test_clean_key_token_strips_punctuation_and_lowercases():
    assert bg._clean_key_token("Hello, World!") == "helloworld"
    assert bg._clean_key_token("Foo-Bar_42") == "foobar42"
    assert bg._clean_key_token("") == ""


# ---------------------------------------------------------------------------
# make_cite_key — author / year / title / conflict-resolution
# ---------------------------------------------------------------------------


def _paper(**kw) -> dict:
    """Helper: minimal paper dict for cite-key tests."""
    base = {
        "authors": ["Jared Kaplan"],
        "year":    2020,
        "title":   "Scaling Laws for Neural Language Models",
    }
    base.update(kw)
    return base


def test_make_cite_key_canonical():
    seen: set[str] = set()
    key = bg.make_cite_key(_paper(), seen)
    assert key == "kaplan2020scaling"
    assert key in seen   # mutates seen


def test_make_cite_key_skips_stopwords_in_title():
    """Title 'The Survey on X' uses 'survey' not 'the' as the title token."""
    seen: set[str] = set()
    key = bg.make_cite_key(
        _paper(title="The Survey on X"), seen)
    assert key == "kaplan2020survey"


def test_make_cite_key_falls_back_to_paper_when_all_stopwords():
    """All-stopword title → fallback 'paper'."""
    seen: set[str] = set()
    key = bg.make_cite_key(
        _paper(title="The On Of A"), seen)
    assert key == "kaplan2020paper"


def test_make_cite_key_handles_missing_author_year():
    seen: set[str] = set()
    key = bg.make_cite_key({"title": "Something"}, seen)
    assert key == "anon0000something"


def test_make_cite_key_resolves_conflicts_with_numeric_suffix():
    """Spec confirmed: first conflict appends '2', second '3', etc.

    Counter starts at 1 then ``suffix += 1`` before use, so 1 is skipped."""
    seen: set[str] = set()
    k1 = bg.make_cite_key(_paper(), seen)
    k2 = bg.make_cite_key(_paper(), seen)
    k3 = bg.make_cite_key(_paper(), seen)
    assert k1 == "kaplan2020scaling"
    assert k2 == "kaplan2020scaling2"
    assert k3 == "kaplan2020scaling3"


# ---------------------------------------------------------------------------
# Entry-type selection
# ---------------------------------------------------------------------------


def test_entry_type_arxiv_only_is_misc():
    assert bg._entry_type({"arxiv_id": "2001.08361"}) == "misc"


def test_entry_type_neurips_is_inproceedings():
    assert bg._entry_type({"venue": "Advances in NeurIPS 2024"}) == "inproceedings"


def test_entry_type_journal_is_article():
    """A journal venue with no conference-hint token → @article."""
    assert bg._entry_type({"venue": "Journal of Statistical Physics",
                            "volume": "5"}) == "article"


def test_entry_type_machine_does_not_match_chi():
    """Regression: substring matching previously fired 'chi' on
    'Machine' (so 'Nature Machine Intelligence', 'Journal of Machine
    Learning Research', etc. were misclassified as @inproceedings).
    The fix uses word-boundary token matching."""
    assert bg._entry_type({"venue": "Nature Machine Intelligence",
                           "volume": "7"}) == "article"
    assert bg._entry_type({"venue": "Journal of Machine Learning Research",
                           "volume": "25"}) == "article"
    # 'architecture' / 'archive' would have falsely matched 'chi' too.
    assert bg._entry_type({"venue": "IEEE Architectures Quarterly",
                           "volume": "3"}) == "article"


def test_entry_type_chi_conference_still_matches():
    """Word-boundary fix must NOT regress the genuine CHI conference."""
    assert bg._entry_type(
        {"booktitle": "Proceedings of the CHI Conference on Human Factors"}
    ) == "inproceedings"
    assert bg._entry_type({"venue": "CHI 2024"}) == "inproceedings"


def test_entry_type_other_short_hints_match_on_word_boundary():
    """Other 3-letter conference codes (acl, www) also work."""
    assert bg._entry_type({"venue": "Proceedings of ACL 2024"}) == "inproceedings"
    assert bg._entry_type({"venue": "WWW '24"}) == "inproceedings"
    # And substrings inside other tokens do NOT match:
    assert bg._entry_type({"venue": "Oracle Database Journal",
                            "volume": "1"}) == "article"  # 'oracle' contains 'acl' as substring


# ---------------------------------------------------------------------------
# paper_to_bibtex output structure
# ---------------------------------------------------------------------------


def test_paper_to_bibtex_emits_required_fields():
    bib = bg.paper_to_bibtex(_paper(), "kaplan2020scaling")
    assert bib.startswith("@")
    assert "{kaplan2020scaling," in bib
    # Three required fields always present
    assert "title = {" in bib
    assert "author = {" in bib
    assert "year = {" in bib
    assert bib.rstrip().endswith("}")


def test_paper_to_bibtex_emits_eprint_for_arxiv():
    paper = _paper(arxiv_id="2001.08361")
    bib = bg.paper_to_bibtex(paper, "k2020s")
    assert "eprint = {2001.08361}" in bib
    assert "archivePrefix = {arXiv}" in bib


def test_paper_to_bibtex_truncates_long_abstract():
    """Abstracts longer than 500 chars must be truncated with '...'."""
    long_abs = "x " * 400   # 800 chars
    paper = _paper(abstract=long_abs)
    bib = bg.paper_to_bibtex(paper, "k2020s")
    # The abstract field is present and contains '...'
    assert "abstract = {" in bib
    assert "..." in bib


# ---------------------------------------------------------------------------
# convert() — end-to-end CLI without subprocess
# ---------------------------------------------------------------------------


def _write_jsonl(path: Path, papers: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(p) for p in papers) + "\n",
                    encoding="utf-8")


def test_convert_writes_bib_and_sidecar(tmp_path):
    """Standard happy path: jsonl → references.bib + sidecar map."""
    src = tmp_path / "filtered.jsonl"
    _write_jsonl(src, [
        _paper(),
        _paper(authors=["Jordan Hoffmann"], year=2022,
                title="Training Compute-Optimal Large Language Models"),
    ])
    out = tmp_path / "references.bib"
    bg.convert(str(src), str(out))

    bib = out.read_text()
    assert "@article{kaplan2020scaling" in bib or \
           "@misc{kaplan2020scaling" in bib
    assert "hoffmann2022training" in bib

    # Sidecar file
    sidecar = out.with_suffix(".cite_keys.json")
    assert sidecar.exists()
    mapping = json.loads(sidecar.read_text())
    assert any(v == "kaplan2020scaling" for v in mapping.values())


def test_convert_to_stdout_does_not_mutate_input(tmp_path, capsys):
    """When --output is omitted, writes to stdout and does NOT rewrite the
    input jsonl in-place."""
    src = tmp_path / "filtered.jsonl"
    _write_jsonl(src, [_paper()])
    original_jsonl = src.read_text()

    bg.convert(str(src), None)
    out = capsys.readouterr().out
    assert "@" in out and "kaplan2020scaling" in out

    # Source jsonl unchanged
    assert src.read_text() == original_jsonl


def test_convert_with_output_does_not_mutate_input_by_default(tmp_path):
    """Regression: writing --output must NOT silently rewrite the input
    filtered.jsonl. Users who want the cite_key field written back must
    opt in via update_input=True (--update-input flag in the CLI)."""
    src = tmp_path / "filtered.jsonl"
    _write_jsonl(src, [_paper()])  # input has no cite_key field
    original_jsonl = src.read_text()
    assert "cite_key" not in original_jsonl

    out = tmp_path / "references.bib"
    bg.convert(str(src), str(out))  # update_input defaults to False

    # The .bib was written...
    assert out.exists() and "kaplan2020scaling" in out.read_text()
    # ...but the source file is byte-for-byte unchanged.
    assert src.read_text() == original_jsonl


def test_convert_update_input_writes_cite_key_back(tmp_path):
    """When update_input=True, the assigned cite_key is persisted back into
    the input filtered.jsonl (opt-in pipeline behaviour)."""
    src = tmp_path / "filtered.jsonl"
    _write_jsonl(src, [_paper()])
    assert "cite_key" not in src.read_text()

    out = tmp_path / "references.bib"
    bg.convert(str(src), str(out), update_input=True)

    rewritten = [json.loads(l) for l in src.read_text().splitlines() if l.strip()]
    assert rewritten[0]["cite_key"] == "kaplan2020scaling"
