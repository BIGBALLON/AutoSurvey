"""Unit tests for full-text evidence verification (tools/verify_evidence.py).

The fetch needs the network, so these pin the pure core: quote verification
(verbatim / near / unverified), quantitative-number extraction (and the
year/small-int filter), number grounding against source text with unit
tolerance, and the CLI input guard.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import verify_evidence as ve  # noqa: E402


# ── quote verification ──────────────────────────────────────────────────────

def test_quote_verbatim_match_ignores_latex_and_case():
    src = "We show that StreamingLLM yields a 22.2x speedup over recomputation."
    quote = r"StreamingLLM yields a 22.2x speedup \citep{xiao2023}"
    # \citep is stripped; case/punctuation-insensitive substring -> verbatim
    assert ve.quote_status(quote, src) == "verbatim"


def test_quote_near_match_on_reordered_tokens():
    src = "The router converged on a learned top-k token-choice assignment rule."
    quote = "learned top-k token-choice router converged"   # same tokens, reordered
    assert ve.quote_status(quote, src) == "near"


def test_quote_unverified_when_not_in_source():
    src = "This paper studies expert parallelism and all-to-all communication."
    quote = "auxiliary-loss-free balancing removes the quality tension entirely"
    assert ve.quote_status(quote, src) == "unverified"


def test_quote_empty_is_unverified():
    assert ve.quote_status("", "anything") == "unverified"
    assert ve.quote_status("something", "") == "unverified"


# ── quantitative number extraction ──────────────────────────────────────────

def test_quant_numbers_keeps_benchmark_figures():
    nums = ve.quant_numbers(r"reaches 88.5 on MMLU with a 22.2x speedup and 30\% memory cut")
    assert "88.5" in nums
    assert "22.2x" in nums
    assert "30%" in nums


def test_quant_numbers_drops_years_and_small_ints():
    nums = ve.quant_numbers("In 2024 the top-2 router used 8 experts")
    assert "2024" not in nums      # year dropped
    assert "8" not in nums         # small unitless int dropped
    assert "2" not in nums         # top-2 dropped


def test_quant_numbers_keeps_large_unitless_and_units():
    nums = ve.quant_numbers("a 671B-total model with 256 routed experts")
    assert "671b" in nums
    assert "256" in nums           # >12 unitless integer kept


# ── number grounding vs source ──────────────────────────────────────────────

def test_missing_numbers_grounded_returns_empty():
    sent = r"DeepSeek-V3 reaches 88.5 on MMLU \citep{x}"
    assert ve.missing_numbers(sent, ["... achieves 88.5 accuracy on MMLU ..."]) == []


def test_missing_numbers_flags_unsourced():
    sent = r"The method gives a 99.9x speedup \citep{x}"
    missing = ve.missing_numbers(sent, ["we report a modest 2.1x improvement"])
    assert "99.9x" in missing


def test_missing_numbers_unit_tolerance_bare_match():
    # prose '128k', source '128,000' -> bare 128 matches
    sent = r"extends to a 128k window \citep{x}"
    assert ve.missing_numbers(sent, ["a context of 128,000 tokens"]) == []


# ── CLI guard ───────────────────────────────────────────────────────────────

def test_cli_missing_inputs_returns_2(tmp_path: Path):
    assert ve.main([str(tmp_path)]) == 2


def test_cli_runs_and_passes_on_grounded_run(tmp_path: Path):
    search = tmp_path / "1_search"
    sections = tmp_path / "5_paper" / "sections"
    cache = search / ".cache"
    search.mkdir(parents=True)
    sections.mkdir(parents=True)
    cache.mkdir(parents=True)
    (search / "filtered.jsonl").write_text(
        '{"cite_key":"a2024","abstract":"We report 88.5 on MMLU."}\n')
    (search / "claims_cache.jsonl").write_text(
        '{"cite_key":"a2024","atomic_claims":[{"quote":"We report 88.5 on MMLU."}]}\n')
    (cache / "a2024.txt").write_text("# source: arxiv\nWe report 88.5 on MMLU and more.")
    (sections / "03_body.tex").write_text(
        r"The model reaches 88.5 on MMLU \citep{a2024}." + "\n")
    rc = ve.main([str(tmp_path), "--strict"])
    assert rc == 0
