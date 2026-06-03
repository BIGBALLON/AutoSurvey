"""Tests for tools/scaffold_cross_cutting_matrix.py — invariant 4 populator.

The scaffolder reads an outline that declares a ``cross_cutting_matrix``
slot plus the closed paper pool, and emits a fillable LaTeX
``\\begin{table*}`` skeleton. Tests cover:

  * the matrix-slot detector (top-level field, section-level, subsection-level)
  * cell rendering (numeric scaling, unknown values, list flattening)
  * end-to-end CLI on the survey-run fixture
  * the round-trip property: a scaffolded matrix passes
    ``audit_writing.audit_structural_template`` invariant 4.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import scaffold_cross_cutting_matrix as scm  # noqa: E402
import audit_writing as aw  # noqa: E402


# ---------------------------------------------------------------------------
# Pure-function helpers
# ---------------------------------------------------------------------------


def test_normalise_col_strips_punctuation_and_lowercases():
    assert scm._normalise_col("Open-Source") == "opensource"
    assert scm._normalise_col("Total Params") == "totalparams"
    assert scm._normalise_col("FLOPs / Compute") == "flopscompute"


def test_walk_returns_leaf_through_dotted_path():
    obj = {"scale": {"total_params": 47_000_000_000}}
    assert scm._walk(obj, "scale.total_params") == 47_000_000_000


def test_walk_returns_none_on_missing_branch():
    obj = {"scale": {}}
    assert scm._walk(obj, "scale.total_params") is None
    assert scm._walk(obj, "missing.path") is None


def test_format_cell_unknown_value_yields_textit_question():
    assert scm._format_cell(None) == r"\textit{?}"
    assert scm._format_cell("") == r"\textit{?}"
    assert scm._format_cell([]) == r"\textit{?}"
    assert scm._format_cell({}) == r"\textit{?}"


def test_format_cell_scales_large_numbers():
    assert scm._format_cell(47_000_000_000) == "47.0B"
    assert scm._format_cell(1_400_000_000_000) == "1.4T"
    assert scm._format_cell(2.8e25).endswith("T") or "T" in scm._format_cell(2.8e25)


def test_format_cell_treats_4digit_year_as_year_not_scaled():
    """A numeric ``year`` field (e.g. 2024) must render verbatim, not
    as ``2.0K``. This was the most visible bug from the first scaffolder
    run on the llm-pretraining baseline."""
    assert scm._format_cell(2024) == "2024"
    assert scm._format_cell(1999) == "1999"
    # Boundary: 1900 / 2100 inclusive
    assert scm._format_cell(1900) == "1900"
    assert scm._format_cell(2100) == "2100"
    # Just outside the year window → scaling resumes
    assert scm._format_cell(1899) == "1.9K"
    assert scm._format_cell(2101) == "2.1K"


def test_format_cell_truncates_long_strings():
    long = "a very very very very long pretraining objective description string"
    cell = scm._format_cell(long)
    assert cell.endswith("…")
    assert len(cell) <= 28


def test_format_cell_escapes_latex_metachars():
    # An unprotected '%' would be a LaTeX comment marker mid-row.
    assert scm._format_cell("70%") == r"70\%"
    assert scm._format_cell("a_b") == r"a\_b"


def test_format_cell_list_takes_first_non_null():
    assert scm._format_cell([None, "ok"]).startswith("ok")
    assert scm._format_cell([None, None, 5]).startswith("5")


def test_format_cell_bool_renders_check_or_cross():
    assert scm._format_cell(True) == r"\cmark"
    assert scm._format_cell(False) == r"\xmark"


# ---------------------------------------------------------------------------
# Matrix-slot detector
# ---------------------------------------------------------------------------


def test_find_matrix_slot_top_level():
    outline = {"cross_cutting_matrix": {"col_labels": ["A"]}}
    slot = scm._find_matrix_slot(outline)
    assert slot == {"col_labels": ["A"]}


def test_find_matrix_slot_section_level():
    outline = {"sections": [
        {"id": "01", "section_type": "intro"},
        {"id": "04e", "section_type": "cross_cutting_matrix",
         "col_labels": ["X", "Y"]},
    ]}
    slot = scm._find_matrix_slot(outline)
    assert slot is not None
    assert slot["id"] == "04e"
    assert slot["col_labels"] == ["X", "Y"]


def test_find_matrix_slot_subsection_level():
    outline = {"sections": [
        {"id": "04", "section_type": "body",
         "subsections": [
             {"id": "04a", "name": "thing"},
             {"id": "04e", "section_type": "cross_cutting_matrix",
              "col_labels": ["A"]},
         ]},
    ]}
    slot = scm._find_matrix_slot(outline)
    assert slot is not None
    assert slot["id"] == "04e"


def test_find_matrix_slot_returns_none_when_absent():
    outline = {"sections": [{"id": "01", "section_type": "intro"}]}
    assert scm._find_matrix_slot(outline) is None


# ---------------------------------------------------------------------------
# render_matrix_tex
# ---------------------------------------------------------------------------


_SAMPLE_CARDS = [
    {"cite_key": "kaplan2020scaling",
     "title":    "Scaling Laws for Neural Language Models",
     "scale":    {"total_params": 1_500_000_000},
     "kind":     "dense"},
    {"cite_key": "hoffmann2022chinchilla",
     "title":    "Training Compute-Optimal Large Language Models",
     "scale":    {"total_params": 70_000_000_000,
                  "training_tokens": 1_400_000_000_000},
     "kind":     "dense"},
    {"cite_key": "jiang2024mixtral",
     "title":    "Mixtral of Experts",
     "architecture": {"attention_type": "GQA"},
     "scale":    {"total_params": 47_000_000_000,
                  "active_params":  13_000_000_000},
     "kind":     "MoE"},
]


def test_render_matrix_emits_well_formed_table_block():
    slot = {"col_labels": ["Architecture", "Total Params", "Tokens"],
            "row_label": "System", "expected_rows": 3}
    tex = scm.render_matrix_tex(slot, _SAMPLE_CARDS)
    assert r"\begin{table*}" in tex
    assert r"\end{table*}" in tex
    assert r"\toprule" in tex and r"\midrule" in tex and r"\bottomrule" in tex
    # Every row carries its citation
    assert r"\citep{kaplan2020scaling}" in tex
    assert r"\citep{hoffmann2022chinchilla}" in tex
    assert r"\citep{jiang2024mixtral}" in tex


def test_render_matrix_fills_known_cells_and_marks_unknown():
    slot = {"col_labels": ["Total Params", "Tokens", "Architecture"],
            "expected_rows": 3}
    tex = scm.render_matrix_tex(slot, _SAMPLE_CARDS)
    # Known: chinchilla.total_params → 70.0B; tokens → 1.4T
    assert "70.0B" in tex
    assert "1.4T" in tex
    # Mixtral has no training_tokens → that cell is unknown
    assert r"\textit{?}" in tex


def test_render_matrix_caps_rows_to_max_rows():
    slot = {"col_labels": ["X"], "expected_rows": 2}
    tex = scm.render_matrix_tex(slot, _SAMPLE_CARDS, max_rows=2)
    # 2 data rows + header row = 3 \\
    n_rowbreaks = tex.count(r"\\")
    assert n_rowbreaks == 3, tex


def test_render_matrix_picks_high_completeness_when_capping():
    """When ``max_rows < len(cards)``, prefer cards with higher
    ``_completeness`` so the matrix is rich-by-construction."""
    cards = [
        {"cite_key": "low",  "_completeness": 0.1, "title": "Low"},
        {"cite_key": "high", "_completeness": 0.9, "title": "High"},
        {"cite_key": "mid",  "_completeness": 0.5, "title": "Mid"},
    ]
    slot = {"col_labels": ["X"], "expected_rows": 2}
    tex = scm.render_matrix_tex(slot, cards, max_rows=2)
    assert r"\citep{high}" in tex
    assert r"\citep{mid}" in tex
    assert r"\citep{low}" not in tex


def test_render_matrix_raises_on_empty_col_labels():
    with pytest.raises(ValueError, match="col_labels"):
        scm.render_matrix_tex({"col_labels": []}, _SAMPLE_CARDS)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(ROOT / "tools" / "scaffold_cross_cutting_matrix.py"),
         *args],
        capture_output=True, text=True,
    )


def test_cli_emits_matrix_for_run_fixture(survey_run_dir):
    """End-to-end: the scaffolder must write a valid .tex file given a
    fixture whose outline has a cross_cutting_matrix slot."""
    # Inject a cross_cutting_matrix slot into the fixture's outline.
    outline_path = survey_run_dir / "4_outline" / "outline.json"
    outline = json.loads(outline_path.read_text())
    outline["sections"].append({
        "id":           "05_matrix",
        "section_type": "cross_cutting_matrix",
        "col_labels":   ["Architecture", "Total Params", "Open-Source"],
        "row_label":    "Model",
        "expected_rows": 3,
    })
    outline_path.write_text(json.dumps(outline, indent=2))

    # Build a cards.jsonl alongside the fixture's filtered.jsonl.
    cards_path = survey_run_dir / "1_search" / "cards.jsonl"
    cards_path.write_text("\n".join(json.dumps(c) for c in [
        {"cite_key": "kaplan2020scaling", "title": "Scaling Laws",
         "scale": {"total_params": 1_500_000_000}, "kind": "dense"},
        {"cite_key": "hoffmann2022chinchilla", "title": "Chinchilla",
         "scale": {"total_params": 70_000_000_000,
                   "training_tokens": 1_400_000_000_000}, "kind": "dense"},
        {"cite_key": "touvron2023llama", "title": "LLaMA",
         "scale": {"total_params": 65_000_000_000}, "kind": "dense"},
    ]) + "\n")

    res = _run_cli(str(survey_run_dir))
    assert res.returncode == 0, (
        f"scaffolder failed:\nstdout:{res.stdout}\nstderr:{res.stderr}"
    )

    out_path = survey_run_dir / "5_paper" / "sections" / "05_matrix.tex"
    assert out_path.exists()
    tex = out_path.read_text()
    assert r"\begin{table*}" in tex
    # Known scaling: 70B / 1.4T should both render
    assert "70.0B" in tex
    assert "65.0B" in tex


def test_cli_returns_1_when_outline_lacks_matrix_slot(survey_run_dir):
    """Default outline has no matrix slot → exit 1."""
    res = _run_cli(str(survey_run_dir))
    assert res.returncode == 1
    assert "no cross_cutting_matrix slot" in res.stderr


def test_cli_returns_2_when_outline_missing(tmp_path):
    res = _run_cli(str(tmp_path))
    assert res.returncode == 2
    assert "outline.json not found" in res.stderr


def test_cli_dry_run_prints_without_writing(survey_run_dir):
    """--dry-run must print to stdout and NOT create the output file."""
    outline_path = survey_run_dir / "4_outline" / "outline.json"
    outline = json.loads(outline_path.read_text())
    outline["sections"].append({
        "id":         "05_matrix",
        "section_type": "cross_cutting_matrix",
        "col_labels": ["X"],
    })
    outline_path.write_text(json.dumps(outline))

    cards_path = survey_run_dir / "1_search" / "cards.jsonl"
    cards_path.write_text(json.dumps(
        {"cite_key": "x", "title": "X"}) + "\n")

    res = _run_cli(str(survey_run_dir), "--dry-run")
    assert res.returncode == 0
    assert r"\begin{table*}" in res.stdout
    out_path = survey_run_dir / "5_paper" / "sections" / "05_matrix.tex"
    # File must NOT exist (dry-run doesn't write)
    assert not out_path.exists()


# ---------------------------------------------------------------------------
# Round-trip: scaffolded matrix satisfies the audit_writing invariant
# ---------------------------------------------------------------------------


def test_scaffolded_matrix_satisfies_audit_invariant(survey_run_dir, tmp_path):
    """When the outline declares the slot AND the scaffolder writes the
    .tex, audit_writing.audit_structural_template's invariant 4 must
    pass: matrix is in outline AND aux_tables ≤ 3."""
    outline_path = survey_run_dir / "4_outline" / "outline.json"
    outline = json.loads(outline_path.read_text())
    outline["sections"].append({
        "id":           "05_matrix",
        "section_type": "cross_cutting_matrix",
        "col_labels":   ["Architecture"],
    })
    outline_path.write_text(json.dumps(outline))

    cards_path = survey_run_dir / "1_search" / "cards.jsonl"
    cards_path.write_text("\n".join(json.dumps(c) for c in [
        {"cite_key": "kaplan2020scaling", "title": "K"},
        {"cite_key": "hoffmann2022chinchilla", "title": "H"},
    ]) + "\n")

    res = _run_cli(str(survey_run_dir))
    assert res.returncode == 0, res.stderr

    sections = aw._read_section_files(survey_run_dir / "5_paper" / "sections")
    info = aw.audit_structural_template(
        sections,
        bib_path=None,
        outline_doc=outline,
    )
    assert info["invariants"]["cross_cutting_matrix"]["ok"] is True, (
        info["invariants"]["cross_cutting_matrix"]
    )
