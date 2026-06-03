"""Tests for tools/build_dimension_tables.py — wide LaTeX comparison tables."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import build_dimension_tables as bdt  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures (paper cards / outline / schema)
# ---------------------------------------------------------------------------


SAMPLE_CARDS = [
    {
        "cite_key": "deepseek2024v3",
        "title": "DeepSeek-V3 Technical Report",
        "extraction": {
            "architecture": {
                "layers": 61,
                "hidden_size": 7168,
                "n_heads": 128,
                "kv_heads": 128,
                "attention_type": "MLA",
                "moe_config": {"num_experts": 256, "top_k": 8},
                "activation": "SwiGLU",
                "normalization": "RMSNorm",
                "pos_encoding": "RoPE",
                "vocab_size": 129280,
            },
            "recipe": {
                "optimizer": "AdamW",
                "peak_lr": 7.3e-6,
                "global_batch_size": 15360,
                "precision": "BF16",
            },
            "scale": {
                "total_params": 671000000000,
                "active_params": 37000000000,
                "training_tokens": 14800000000000,
            },
        },
        "extraction_source": "arxiv_pdf",
        "extraction_completeness": 0.85,
        "missing_fields": [],
    },
    {
        "cite_key": "qwen2024qwen2",
        "title": "Qwen2 Technical Report",
        "extraction": {
            "architecture": {
                "layers": 80,
                "hidden_size": 8192,
                "n_heads": 64,
                "kv_heads": 8,
                "attention_type": "GQA",
                "moe_config": "N/R",
                "activation": "SwiGLU",
                "normalization": "RMSNorm",
                "pos_encoding": "RoPE",
                "vocab_size": 152064,
            },
            "recipe": {
                "optimizer": "AdamW",
                "peak_lr": 3e-4,
                "global_batch_size": "N/R",
                "precision": "BF16",
            },
            "scale": {
                "total_params": 72000000000,
                "active_params": 72000000000,
                "training_tokens": 7000000000000,
            },
        },
        "extraction_source": "abstract_fallback",
        "extraction_completeness": 0.65,
        "missing_fields": ["recipe.global_batch_size"],
    },
]


SAMPLE_OUTLINE = {
    "sections": [
        {
            "id": "architecture",
            "title": "Architectures",
            "primary_papers": ["deepseek2024v3", "qwen2024qwen2"],
            "secondary_papers": [],
        },
        {
            "id": "scale",
            "title": "Scale",
            "primary_papers": ["deepseek2024v3", "qwen2024qwen2"],
            "secondary_papers": [],
        },
    ]
}


SAMPLE_SCHEMA = {
    "_template_used": "llm-pretraining",
    "groups": {
        "architecture": {
            "layers": "int",
            "hidden_size": "int",
            "n_heads": "int",
            "kv_heads": "int",
            "attention_type": "str",
            "moe_config": "dict",
            "activation": "str",
            "normalization": "str",
            "pos_encoding": "str",
            "vocab_size": "int",
        },
        "recipe": {
            "optimizer": "str",
            "peak_lr": "float",
            "global_batch_size": "int",
            "precision": "str",
        },
        "scale": {
            "total_params": "int",
            "active_params": "int",
            "training_tokens": "int",
        },
    },
}


# ---------------------------------------------------------------------------
# format_number
# ---------------------------------------------------------------------------


def test_format_number_kmbt_scaling():
    assert bdt.format_number(670_000_000_000) == "670B"
    assert bdt.format_number(14_800_000_000_000) == "14.8T"
    assert bdt.format_number(4096) == "4096"
    assert bdt.format_number(1500) == "1.5k"


def test_format_number_floats():
    assert bdt.format_number(0.85) == "0.85"
    s = bdt.format_number(7.3e-6)
    assert s in {"7.3e-06", "7.3e-6"}


# ---------------------------------------------------------------------------
# escape_latex
# ---------------------------------------------------------------------------


def test_escape_latex_special_chars():
    s = bdt.escape_latex("MoE & dropout 5% _experts_")
    assert "\\&" in s
    assert "\\%" in s
    assert "\\_" in s


# ---------------------------------------------------------------------------
# format_cell
# ---------------------------------------------------------------------------


def test_format_cell_truncates_long_string():
    result = bdt.format_cell("a" * 100, "str")
    assert "..." in result
    assert len(result) <= 33


def test_format_cell_handles_NR():
    assert bdt.format_cell("N/R", "str") == "--"
    assert bdt.format_cell(None, "str") == "--"


def test_format_cell_compacts_dict():
    out = bdt.format_cell({"num_experts": 256, "top_k": 8}, "dict")
    assert "experts:256" in out
    assert "top_k:8" in out


# ---------------------------------------------------------------------------
# match_group_to_section
# ---------------------------------------------------------------------------


def test_match_group_to_section_exact_name():
    section = {"id": "architecture", "title": "Architectures"}
    groups = {
        "architecture": {"layers": "int"},
        "recipe": {"optimizer": "str"},
    }
    assert bdt.match_group_to_section(section, groups) == "architecture"


def test_match_group_to_section_fallback_overlap():
    section = {"id": "pretraining_recipe", "title": "Pretraining Recipe"}
    groups = {
        "recipe": {"optimizer": "str", "peak_lr": "float"},
        "scale": {"total_params": "int"},
        "data": {"sources": "list"},
    }
    assert bdt.match_group_to_section(section, groups) == "recipe"


def test_match_group_to_section_no_match_returns_none():
    section = {"id": "totally_unrelated", "title": "Totally Unrelated Topic"}
    groups = {
        "architecture": {"layers": "int"},
        "recipe": {"optimizer": "str"},
    }
    assert bdt.match_group_to_section(section, groups) is None


# ---------------------------------------------------------------------------
# select_columns
# ---------------------------------------------------------------------------


def test_select_columns_drops_all_NR_field():
    rows = [
        {"layers": 61, "moe_config": "N/R"},
        {"layers": 80, "moe_config": "N/R"},
    ]
    schema_group = {"layers": "int", "moe_config": "dict"}
    cols = bdt.select_columns(rows, schema_group, max_cols=5)
    assert "layers" in cols
    assert "moe_config" not in cols


def test_select_columns_respects_max_cols():
    schema_group = {f"f{i}": "int" for i in range(12)}
    rows = [{f"f{i}": i + 1 for i in range(12)} for _ in range(2)]
    cols = bdt.select_columns(rows, schema_group, max_cols=5)
    assert len(cols) <= 5


# ---------------------------------------------------------------------------
# generate_table
# ---------------------------------------------------------------------------


def test_generate_table_includes_citet_command():
    section = SAMPLE_OUTLINE["sections"][0]
    cards = {c["cite_key"]: c for c in SAMPLE_CARDS}
    columns = ["layers", "hidden_size", "attention_type"]
    schema_group = SAMPLE_SCHEMA["groups"]["architecture"]
    tex = bdt.generate_table(
        section=section,
        group_name="architecture",
        cards=cards,
        columns=columns,
        schema_group=schema_group,
    )
    assert "\\citet{deepseek2024v3}" in tex
    assert "\\citet{qwen2024qwen2}" in tex


def test_generate_table_uses_booktabs():
    section = SAMPLE_OUTLINE["sections"][0]
    cards = {c["cite_key"]: c for c in SAMPLE_CARDS}
    columns = ["layers", "hidden_size", "attention_type"]
    schema_group = SAMPLE_SCHEMA["groups"]["architecture"]
    tex = bdt.generate_table(
        section=section,
        group_name="architecture",
        cards=cards,
        columns=columns,
        schema_group=schema_group,
    )
    assert "\\toprule" in tex
    assert "\\midrule" in tex
    assert "\\bottomrule" in tex


# ---------------------------------------------------------------------------
# main / end-to-end
# ---------------------------------------------------------------------------


def _write_inputs(tmp_path: Path,
                  cards: list[dict] | None = None,
                  outline: dict | None = None,
                  schema: dict | None = None) -> tuple[Path, Path, Path, Path]:
    cards = cards if cards is not None else SAMPLE_CARDS
    outline = outline if outline is not None else SAMPLE_OUTLINE
    schema = schema if schema is not None else SAMPLE_SCHEMA

    cards_path = tmp_path / "cards.jsonl"
    with cards_path.open("w") as f:
        for c in cards:
            f.write(json.dumps(c) + "\n")
    outline_path = tmp_path / "outline.json"
    outline_path.write_text(json.dumps(outline))
    schema_path = tmp_path / "schema.json"
    schema_path.write_text(json.dumps(schema))
    out_dir = tmp_path / "tables"
    return cards_path, outline_path, schema_path, out_dir


def test_main_writes_one_file_per_section(tmp_path):
    third_card = {
        "cite_key": "llama2024",
        "title": "LLaMA-3 Card",
        "extraction": {
            "architecture": {
                "layers": 80, "hidden_size": 8192, "n_heads": 64,
                "kv_heads": 8, "attention_type": "GQA", "moe_config": "N/R",
                "activation": "SwiGLU", "normalization": "RMSNorm",
                "pos_encoding": "RoPE", "vocab_size": 128256,
            },
            "scale": {
                "total_params": 405_000_000_000,
                "active_params": 405_000_000_000,
                "training_tokens": 15_000_000_000_000,
            },
            "recipe": {
                "optimizer": "AdamW", "peak_lr": 8e-5,
                "global_batch_size": 16000, "precision": "BF16",
            },
        },
        "extraction_source": "arxiv_pdf",
        "extraction_completeness": 0.9,
        "missing_fields": [],
    }
    cards = SAMPLE_CARDS + [third_card]
    outline = {
        "sections": [
            {"id": "architecture", "title": "Architectures",
             "primary_papers": ["deepseek2024v3", "qwen2024qwen2", "llama2024"],
             "secondary_papers": []},
            {"id": "scale", "title": "Scale",
             "primary_papers": ["deepseek2024v3", "qwen2024qwen2", "llama2024"],
             "secondary_papers": []},
        ]
    }

    cards_p, outline_p, schema_p, out_dir = _write_inputs(
        tmp_path, cards=cards, outline=outline
    )
    rc = bdt.main([
        "--cards", str(cards_p),
        "--outline", str(outline_p),
        "--schema", str(schema_p),
        "--output-dir", str(out_dir),
    ])
    assert rc == 0
    files = sorted(p.name for p in out_dir.glob("*.tex"))
    assert files == ["architecture_comparison.tex", "scale_comparison.tex"]


def test_main_skips_section_with_no_matching_group(tmp_path, capsys):
    outline = {
        "sections": [
            {"id": "architecture", "title": "Architectures",
             "primary_papers": ["deepseek2024v3", "qwen2024qwen2"],
             "secondary_papers": []},
            {"id": "totally_orthogonal", "title": "Completely Random",
             "primary_papers": ["deepseek2024v3"],
             "secondary_papers": []},
        ]
    }
    cards_p, outline_p, schema_p, out_dir = _write_inputs(
        tmp_path, outline=outline
    )
    rc = bdt.main([
        "--cards", str(cards_p),
        "--outline", str(outline_p),
        "--schema", str(schema_p),
        "--output-dir", str(out_dir),
    ])
    assert rc == 0
    files = {p.name for p in out_dir.glob("*.tex")}
    assert "architecture_comparison.tex" in files
    assert "totally_orthogonal_comparison.tex" not in files
    captured = capsys.readouterr()
    assert "totally_orthogonal" in (captured.err + captured.out)


# ---------------------------------------------------------------------------
# CLI smoke test (--help exits 0)
# ---------------------------------------------------------------------------


def test_cli_help_exits_zero():
    rc = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "build_dimension_tables.py"), "--help"],
        capture_output=True,
    )
    assert rc.returncode == 0


# ---------------------------------------------------------------------------
# --mode decision
# ---------------------------------------------------------------------------


def test_decision_mode_basic_render(survey_run_dir, tmp_path):
    """generate_decision_table: legitimate _decision_summary cards must render
    a 6-column LaTeX table without NameError (the t1 bug regression guard).
    """
    cards = bdt.load_cards(survey_run_dir / "1_search" / "cards.jsonl")
    outline = bdt.load_outline(survey_run_dir / "4_outline" / "outline.json")

    section = next(s for s in outline if bdt._section_id(s) == "02_scaling")
    tex = bdt.generate_decision_table(section, cards)

    assert tex is not None, "decision table must render when cards have _decision_summary"
    # Header structure
    assert r"\textbf{System}" in tex
    assert r"\textbf{Tier}" in tex
    assert r"\textbf{Approach}" in tex
    assert r"\textbf{Capability}" in tex
    assert r"\textbf{Limitation}" in tex
    assert r"\textbf{Open?}" in tex
    # Per-row data appears
    assert "kaplan2020scaling" in tex
    assert "T1" in tex   # tier label
    # Availability glyphs
    assert r"$\checkmark$" in tex     # open
    assert r"$\times$" in tex         # closed
    assert "partial" in tex           # weights-only
    # Caption + label: identifier key now preserves underscores
    # (escape_latex_ident, not escape_latex)
    assert r"\caption{" in tex
    assert r"\label{tab:02_scaling_decision}" in tex
    # Confirm the regression is gone — over-escaped form must NOT appear
    assert r"02\_scaling_decision" not in tex
    assert r"\begin{table*}" in tex


def test_decision_mode_truncates_to_max_words(survey_run_dir):
    """≤cell_max_words tokens per cell — over-long descriptions get an ellipsis."""
    cards = bdt.load_cards(survey_run_dir / "1_search" / "cards.jsonl")
    # Inject a deliberately long key_capability into one card
    cards["kaplan2020scaling"]["_decision_summary"]["key_capability"] = (
        "Extremely thorough power-law fitting across many regimes"
    )
    outline = bdt.load_outline(survey_run_dir / "4_outline" / "outline.json")
    section = next(s for s in outline if bdt._section_id(s) == "02_scaling")

    tex = bdt.generate_decision_table(section, cards, cell_max_words=4)
    # Truncation marker
    assert "…" in tex, "expected ellipsis for a > 4-word cell"


def test_decision_mode_returns_none_without_summaries(survey_run_dir):
    """No _decision_summary on any matched card → None (caller decides)."""
    cards = bdt.load_cards(survey_run_dir / "1_search" / "cards.jsonl")
    for card in cards.values():
        card.pop("_decision_summary", None)
    outline = bdt.load_outline(survey_run_dir / "4_outline" / "outline.json")
    section = next(s for s in outline if bdt._section_id(s) == "02_scaling")
    assert bdt.generate_decision_table(section, cards) is None


def test_decision_mode_fallback_uses_default_schema_path(survey_run_dir, tmp_path):
    """when --mode decision is requested but no card has
    _decision_summary, the CLI falls back to --mode fields. Pre-fix it
    then dead-ended with 'ERROR: --schema is required' even though the
    user only ever asked for decision mode. Post-fix it auto-locates
    brief.derived_schema.json next to the run (one of two standard
    layouts) and proceeds — silently for the user, with a single
    explanatory WARN.
    """
    # Mutate the fixture to remove all _decision_summary, forcing fallback.
    cards_path = tmp_path / "cards.jsonl"
    src = (survey_run_dir / "1_search" / "cards.jsonl").read_text()
    out_lines = []
    for line in src.splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        rec.pop("_decision_summary", None)
        rec.pop("decision_summary", None)
        out_lines.append(json.dumps(rec))
    cards_path.write_text("\n".join(out_lines) + "\n")

    # Drop a minimal schema next to the run root (one of the layouts used
    # by real production runs). The fallback should auto-locate it.
    schema_path = survey_run_dir / "brief.derived_schema.json"
    schema_path.write_text(json.dumps({
        "fields": {"primary": []},
    }))

    out_dir = tmp_path / "tables"
    rc = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "build_dimension_tables.py"),
         "--mode", "decision",
         "--cards", str(cards_path),
         "--outline", str(survey_run_dir / "4_outline" / "outline.json"),
         "--output-dir", str(out_dir)],
        capture_output=True, text=True,
    )
    assert rc.returncode == 0, (
        f"CLI failed despite an auto-locatable schema:\n"
        f"stdout:{rc.stdout}\nstderr:{rc.stderr}"
    )
    assert "auto-located schema" in rc.stderr


def test_decision_mode_fallback_fails_loudly_when_no_schema_available(tmp_path):
    """If the schema cannot be auto-located AND wasn't passed, the tool
    must fail with a precise actionable message instead of dying on the
    downstream 'ERROR: --schema is required'."""
    # Build a minimal run directory: cards without _decision_summary,
    # outline.json present, but NO 0_brief/brief.derived_schema.json.
    run_dir = tmp_path / "run"
    (run_dir / "1_search").mkdir(parents=True)
    (run_dir / "4_outline").mkdir(parents=True)

    cards_path = run_dir / "1_search" / "cards.jsonl"
    cards_path.write_text(json.dumps({
        "cite_key": "x2024",
        "title": "X",
        "year": 2024,
        "fields": {},
    }) + "\n")

    outline_path = run_dir / "4_outline" / "outline.json"
    outline_path.write_text(json.dumps({
        "sections": [
            {"section_id": "02_arch", "title": "Architecture",
             "primary_papers": ["x2024"]}
        ]
    }))

    rc = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "build_dimension_tables.py"),
         "--mode", "decision",
         "--cards", str(cards_path),
         "--outline", str(outline_path),
         "--output-dir", str(run_dir / "out")],
        capture_output=True, text=True,
    )
    assert rc.returncode == 2
    # Message must mention BOTH the missing-schema path AND a remediation
    assert "brief.derived_schema.json" in rc.stderr
    assert "/survey-write" in rc.stderr or "decision summaries" in rc.stderr


def test_decision_mode_cli_writes_files(survey_run_dir, tmp_path):
    """python3 build_dimension_tables.py --mode decision <run> must succeed
    and emit one <section_id>_decision.tex per section with summaries.
    Guards against the t1 latex_escape NameError that crashed the CLI.
    """
    out_dir = tmp_path / "tables"
    rc = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "build_dimension_tables.py"),
         "--mode", "decision",
         "--cards", str(survey_run_dir / "1_search" / "cards.jsonl"),
         "--outline", str(survey_run_dir / "4_outline" / "outline.json"),
         "--output-dir", str(out_dir),
         "--verbose"],
        capture_output=True, text=True,
    )
    assert rc.returncode == 0, f"CLI failed:\nstdout:{rc.stdout}\nstderr:{rc.stderr}"
    # At least one section in the fixture has _decision_summary papers
    files = sorted(out_dir.glob("*_decision.tex"))
    assert files, f"no decision tables emitted; stdout: {rc.stdout}"
    # Sanity: file content has the standard column headers
    sample = files[0].read_text()
    assert r"\textbf{Tier}" in sample
    assert r"\textbf{Open?}" in sample


# ---------------------------------------------------------------------------
# escape_latex_ident t2
# ---------------------------------------------------------------------------


def test_escape_latex_ident_preserves_legal_chars():
    """alnum, underscore, hyphen, colon, period — all legal in label keys."""
    assert bdt.escape_latex_ident("02_scaling") == "02_scaling"
    assert bdt.escape_latex_ident("tab:abc") == "tab:abc"
    assert bdt.escape_latex_ident("foo-bar.v2") == "foo-bar.v2"
    assert bdt.escape_latex_ident("Section_2_a") == "Section_2_a"


def test_escape_latex_ident_strips_whitespace():
    """Whitespace splits identifiers in LaTeX → must be removed."""
    assert bdt.escape_latex_ident("foo bar") == "foobar"
    assert bdt.escape_latex_ident("a\tb\nc") == "abc"


def test_escape_latex_ident_strips_metachars():
    """Brace / backslash / hash / percent / dollar / tilde / caret / amp →
    each silently breaks identifier parsing if left in."""
    assert bdt.escape_latex_ident("a{b}c") == "abc"
    assert bdt.escape_latex_ident(r"a\b") == "ab"
    assert bdt.escape_latex_ident("a#b%c") == "abc"
    assert bdt.escape_latex_ident("a$b") == "ab"
    assert bdt.escape_latex_ident("a~b^c") == "abc"
    assert bdt.escape_latex_ident("a&b,c") == "abc"


def test_escape_latex_ident_handles_none_and_empty():
    assert bdt.escape_latex_ident(None) == ""
    assert bdt.escape_latex_ident("") == ""
    assert bdt.escape_latex_ident("   ") == ""


def test_escape_latex_ident_does_not_use_backslash_escapes():
    """Critical contract: identifier sanitization REMOVES, never escapes.
    \\_ inside \\ref{} is itself ill-formed LaTeX, so any \\<char> output
    would be a regression."""
    out = bdt.escape_latex_ident("foo&bar%baz#quux")
    assert "\\" not in out, f"escape_latex_ident must not emit \\: {out!r}"
    assert out == "foobarbazquux"


# ---------------------------------------------------------------------------
# escape_latex backslash sentinel (regression guard)
# ---------------------------------------------------------------------------


def test_escape_latex_backslash_no_double_escape():
    """Previously bug: 'a\\b' became 'a\\textbackslash\\{\\}b' because the
    ``{}`` introduced by ``\\textbackslash{}`` were re-escaped on the
    second pass. Sentinel pattern must not regress."""
    out = bdt.escape_latex(r"a\b")
    assert out == r"a\textbackslash{}b", f"got {out!r}"
    # The over-escaped form must NEVER appear
    assert r"\textbackslash\{\}" not in out


def test_escape_latex_backslash_combined_with_other_metachars():
    """Backslash + other metachars in same input still produce valid output."""
    out = bdt.escape_latex(r"x\y&z{w}")
    assert out == r"x\textbackslash{}y\&z\{w\}", f"got {out!r}"


def test_escape_latex_full_metachar_coverage():
    """All 10 LaTeX metacharacters: result must be valid LaTeX (not double-escaped)."""
    out = bdt.escape_latex(r"& % # _ $ { } ~ ^ \\")
    # Each metachar must be properly escaped exactly once
    assert r"\&" in out
    assert r"\%" in out
    assert r"\#" in out
    assert r"\_" in out
    assert r"\$" in out
    assert r"\{" in out
    assert r"\}" in out
    assert r"\textasciitilde{}" in out
    assert r"\textasciicircum{}" in out
    assert r"\textbackslash{}" in out
    # Critical: no double-escape sequences
    assert r"\textbackslash\{\}" not in out
