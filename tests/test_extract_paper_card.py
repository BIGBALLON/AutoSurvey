"""Tests for tools/extract_paper_card.py — paper-card extraction backend.

The tool no longer makes any LLM calls; in the Claude Code agent
running /survey-write's per-section inner loop supplies the thinking
(see shared-references/claims-contract.md). These tests cover the
deterministic helpers and the three CLI modes:

  --validate-schema  agent candidate JSON → canonical brief.derived_schema.json
  --fetch-all        download paper texts (mocked here)
  --write-cards      validate per-paper extractions → cards.md + cards.jsonl
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List

import pytest


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import extract_paper_card as epc  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _brief(topic: str, dimensions: List[str] | None = None) -> Dict[str, Any]:
    """Tiny ``brief.parsed.json``-shaped dict for keyword tests."""
    return {
        "topic": topic,
        "scope": {"include": [], "exclude": []},
        "sources": {"categories": ["arxiv"], "year_range": [2021, 2026]},
        "dimensions": [
            {"name": d, "description": ""} for d in (dimensions or [])
        ],
        "style": [],
        "configuration": {
            "trends_section": "include",
        },
        "_uncertainties": [],
    }


def _two_papers() -> List[Dict[str, Any]]:
    return [
        {
            "cite_key": "smith2024paper",
            "paper_id": "p1",
            "title": "Smith Paper",
            "abstract": "We propose method A with 64 layers.",
            "authors": ["Smith", "Jones"],
        },
        {
            "cite_key": "doe2024paper",
            "paper_id": "p2",
            "title": "Doe Paper",
            "abstract": "We propose method B with 80 layers.",
            "authors": ["Doe"],
        },
    ]


def _minimal_schema() -> Dict[str, Any]:
    """Tiny schema used by extract / completeness / render tests."""
    return {
        "_template_used": "generic",
        "groups": {
            "summary": {
                "one_sentence_summary": "str",
                "key_contribution": "str",
            },
            "method": {
                "approach": "str",
                "layers": "int",
            },
        },
    }


# ---------------------------------------------------------------------------
# match_template — only the "generic" target is shipped; topic-keyword
# buckets fall back to "generic" when their target file is absent.
# ---------------------------------------------------------------------------


def test_match_template_llm_pretraining():
    # The llm-pretraining bucket falls back to "generic" because no
    # topic-fitted asset is shipped on disk — the keyword routing is
    # preserved in the function signature, but the matching template
    # file is intentionally not bundled.
    result = epc.match_template(_brief("LLM pretraining"))
    assert result in ("llm-pretraining", "generic"), (
        f"expected llm-pretraining or generic, got {result!r}"
    )


def test_match_template_vision():
    # The vision-models bucket also falls back to generic.
    result = epc.match_template(_brief("diffusion models"))
    assert result in ("vision-models", "generic"), (
        f"expected vision-models or generic, got {result!r}"
    )


def test_match_template_generic_fallback():
    assert epc.match_template(_brief("bird identification")) == "generic"


# ---------------------------------------------------------------------------
# load_template — pure parser; tested with an inline fixture written to
# tmp_path so the test never depends on a shipped templates/ directory
# (the per-skill templates moved out of the repo when /survey-extract was
# absorbed into /survey-write's per-section inner loop).
# ---------------------------------------------------------------------------


_TEMPLATE_FIXTURE = """\
# Generic paper card

Some prose that load_template should ignore (no group section yet).

## Field groups

### _decision_summary

- decision: str # one-sentence verdict
- atomic_claims: list[str]

### evidence

- benchmark: str
- score: float

## Notes

This trailing section must be ignored.
- ignored_field: str
"""


def test_load_template_parses_groups_and_fields(tmp_path):
    """load_template must extract (group, field) pairs from a markdown file
    laid out as the in-repo schema spec describes."""
    template_path = tmp_path / "generic.md"
    template_path.write_text(_TEMPLATE_FIXTURE, encoding="utf-8")

    groups = epc.load_template(str(template_path))

    assert isinstance(groups, dict)
    assert set(groups.keys()) == {"_decision_summary", "evidence"}
    assert "decision" in groups["_decision_summary"]
    assert "atomic_claims" in groups["_decision_summary"]
    assert "benchmark" in groups["evidence"]
    assert "score" in groups["evidence"]
    # Trailing "## Notes" section must be ignored.
    assert "ignored_field" not in {f for fs in groups.values() for f in fs}


def test_load_template_raises_on_missing_file(tmp_path):
    missing = tmp_path / "does_not_exist.md"
    with pytest.raises(FileNotFoundError):
        epc.load_template(str(missing))


def test_load_template_raises_when_no_groups(tmp_path):
    template_path = tmp_path / "empty.md"
    template_path.write_text("# Title\n\nNo field groups section here.\n", encoding="utf-8")
    with pytest.raises(ValueError):
        epc.load_template(str(template_path))


# ---------------------------------------------------------------------------
# validate_schema (unchanged behaviour)
# ---------------------------------------------------------------------------


def test_validate_schema_rejects_missing_groups():
    ok, err = epc.validate_schema({"groups": {}})
    assert ok is False
    assert "at least one group" in err


def test_validate_schema_rejects_bad_type_hint():
    ok, err = epc.validate_schema({"groups": {"g": {"f": "monkey"}}})
    assert ok is False
    assert "monkey" in err or "type hint" in err


def test_validate_schema_accepts_canonical():
    ok, err = epc.validate_schema(
        {"groups": {"architecture": {"layers": "int"}}}
    )
    assert ok is True
    assert err == ""


# ---------------------------------------------------------------------------
# validate_synthesis_candidate
# ---------------------------------------------------------------------------


def test_validate_synthesis_candidate_accepts_canonical():
    candidate = {
        "_template_used": "llm-pretraining",
        "groups": {
            "architecture": {"layers": "int", "hidden_size": "int"},
            "recipe": {"optimizer": "str"},
        },
    }
    out = epc.validate_synthesis_candidate(candidate)
    assert out["_template_used"] == "llm-pretraining"
    assert out["groups"] == candidate["groups"]


def test_validate_synthesis_candidate_rejects_bad_type():
    candidate = {"groups": {"g": {"f": "monkey"}}}
    with pytest.raises(ValueError) as exc:
        epc.validate_synthesis_candidate(candidate)
    assert "monkey" in str(exc.value) or "type hint" in str(exc.value)


def test_validate_synthesis_candidate_rejects_missing_groups():
    with pytest.raises(ValueError) as exc:
        epc.validate_synthesis_candidate({"groups": {}})
    assert "at least one group" in str(exc.value)


def test_validate_synthesis_candidate_lifts_bare_dict():
    """A bare ``{group: {field: type}}`` dict is tolerated and lifted."""
    candidate = {"architecture": {"layers": "int"}}
    out = epc.validate_synthesis_candidate(candidate)
    assert out["groups"] == {"architecture": {"layers": "int"}}


# ---------------------------------------------------------------------------
# fetch_paper_text (unchanged)
# ---------------------------------------------------------------------------


def test_fetch_paper_text_falls_back_to_abstract_when_no_fetcher_returns(
    monkeypatch, tmp_path
):
    """When every fetcher returns None, the abstract is the fallback source."""
    paper = {
        "cite_key": "k",
        "paper_id": "p",
        "abstract": "ABSTRACT TEXT GOES HERE",
    }
    monkeypatch.setattr(epc, "_fetch_s2_pdf", lambda paper, timeout: None)
    monkeypatch.setattr(epc, "_fetch_arxiv_pdf", lambda paper, timeout: None)
    monkeypatch.setattr(epc, "_fetch_html_text", lambda paper, timeout: None)

    text, source = epc.fetch_paper_text(paper, tmp_path)
    assert source == "abstract_fallback"
    assert "ABSTRACT TEXT GOES HERE" in text


def test_fetch_paper_text_falls_through_priority_chain(monkeypatch, tmp_path):
    """S2 absent → arXiv hits → arxiv_pdf wins; abstract is prepended."""
    paper = {
        "cite_key": "kx",
        "paper_id": "p1",
        "arxiv_id": "2401.12345",
        "abstract": "ORIGINAL ABSTRACT",
    }

    monkeypatch.setattr(epc, "_fetch_s2_pdf", lambda paper, timeout: None)

    arxiv_text = "ARXIV BODY EXTRACTED FROM PDF"

    def fake_arxiv(paper, timeout):
        return arxiv_text

    monkeypatch.setattr(epc, "_fetch_arxiv_pdf", fake_arxiv)
    monkeypatch.setattr(epc, "_fetch_html_text", lambda paper, timeout: None)

    text, source = epc.fetch_paper_text(paper, tmp_path)
    assert source == "arxiv_pdf"
    assert "ARXIV BODY EXTRACTED FROM PDF" in text
    assert "ORIGINAL ABSTRACT" in text


# ---------------------------------------------------------------------------
# validate_extraction
# ---------------------------------------------------------------------------


def test_validate_extraction_accepts_canonical():
    schema = _minimal_schema()
    extraction = {
        "summary": {
            "one_sentence_summary": "Method A.",
            "key_contribution": "60-layer transformer.",
        },
        "method": {"approach": "transformer", "layers": 60},
    }
    coerced, errors = epc.validate_extraction(extraction, schema)
    assert errors == []
    assert coerced["method"]["layers"] == 60
    assert coerced["summary"]["one_sentence_summary"] == "Method A."
    # All declared fields present.
    for group, fields in schema["groups"].items():
        assert group in coerced
        for field in fields:
            assert field in coerced[group]


def test_validate_extraction_marks_invalid_field_NR():
    schema = {"groups": {"method": {"approach": "str", "layers": "int"}}}
    extraction = {"method": {"approach": "transformer", "layers": "many"}}
    coerced, errors = epc.validate_extraction(extraction, schema)
    assert coerced["method"]["approach"] == "transformer"
    assert coerced["method"]["layers"] == "N/R"
    assert "method.layers" in errors


def test_validate_extraction_handles_NR_passthrough():
    schema = {"groups": {"method": {"approach": "str", "layers": "int"}}}
    extraction = {"method": {"approach": "N/R", "layers": "N/R"}}
    coerced, errors = epc.validate_extraction(extraction, schema)
    assert coerced["method"]["approach"] == "N/R"
    assert coerced["method"]["layers"] == "N/R"
    assert errors == []


def test_validate_extraction_fills_missing_with_NR():
    schema = _minimal_schema()
    extraction = {"method": {"approach": "transformer"}}  # most fields absent
    coerced, errors = epc.validate_extraction(extraction, schema)
    assert errors == []  # absence is not an error, just missing
    assert coerced["method"]["approach"] == "transformer"
    assert coerced["method"]["layers"] == "N/R"
    assert coerced["summary"]["one_sentence_summary"] == "N/R"
    assert coerced["summary"]["key_contribution"] == "N/R"


# ---------------------------------------------------------------------------
# compute_completeness (unchanged)
# ---------------------------------------------------------------------------


def test_compute_completeness_reports_correct_ratio():
    schema = {
        "groups": {
            "g1": {f"f{i}": "str" for i in range(5)},
            "g2": {f"f{i}": "str" for i in range(5, 10)},
        }
    }
    extraction = {
        "g1": {"f0": "a", "f1": "b", "f2": "c", "f3": "N/R", "f4": "N/R"},
        "g2": {"f5": "d", "f6": "e", "f7": "f", "f8": "N/R", "f9": "g"},
    }  # 7 non-N/R, 3 N/R

    ratio, missing = epc.compute_completeness(extraction, schema)
    assert ratio == pytest.approx(0.7, abs=1e-4)
    assert len(missing) == 3
    assert "g1.f3" in missing
    assert "g1.f4" in missing
    assert "g2.f8" in missing


# ---------------------------------------------------------------------------
# render_card_markdown (unchanged)
# ---------------------------------------------------------------------------


def test_render_card_markdown_includes_meta_block():
    paper = {"cite_key": "k", "title": "Test Paper"}
    extraction = {
        "summary": {"key_contribution": "N/R"},
        "method": {"approach": "transformer"},
    }
    md = epc.render_card_markdown(
        paper,
        extraction,
        completeness=0.5,
        source="arxiv_pdf",
        missing=["summary.key_contribution"],
    )
    assert "# Test Paper" in md
    assert "**Source:** arxiv_pdf" in md
    assert "**Completeness:** 0.50" in md
    assert "## summary" in md
    assert "## method" in md
    assert "- approach: transformer" in md
    assert "## _meta" in md
    assert "extraction_source: arxiv_pdf" in md
    assert "summary.key_contribution" in md


# ---------------------------------------------------------------------------
# Mode 1: --validate-schema
# ---------------------------------------------------------------------------


def test_validate_schema_mode_writes_output(tmp_path):
    candidate_path = tmp_path / "candidate.json"
    output_path = tmp_path / "schema.json"

    candidate = {
        "_template_used": "llm-pretraining",
        "groups": {
            "architecture": {"layers": "int", "hidden_size": "int"},
            "recipe": {"optimizer": "str"},
        },
    }
    candidate_path.write_text(json.dumps(candidate), encoding="utf-8")

    rc = epc.main(
        [
            "--validate-schema",
            "--candidate",
            str(candidate_path),
            "--output",
            str(output_path),
        ]
    )
    assert rc == 0
    assert output_path.exists()

    written = json.loads(output_path.read_text(encoding="utf-8"))
    assert written["_template_used"] == "llm-pretraining"
    assert written["groups"]["architecture"]["layers"] == "int"


def test_validate_schema_mode_rejects_bad_candidate(tmp_path, capsys):
    candidate_path = tmp_path / "candidate.json"
    output_path = tmp_path / "schema.json"
    candidate_path.write_text(
        json.dumps({"groups": {"g": {"f": "monkey"}}}), encoding="utf-8"
    )

    rc = epc.main(
        [
            "--validate-schema",
            "--candidate",
            str(candidate_path),
            "--output",
            str(output_path),
        ]
    )
    assert rc == 1
    assert not output_path.exists()
    err = capsys.readouterr().err
    assert "monkey" in err or "type hint" in err


# ---------------------------------------------------------------------------
# Mode 2: --fetch-all
# ---------------------------------------------------------------------------


def test_fetch_all_mode_creates_cache_files(tmp_path, monkeypatch):
    """All-fetchers-return-None path: cache files are written from the abstract."""
    filtered_path = tmp_path / "filtered.jsonl"
    cache_dir = tmp_path / "cache"
    papers = _two_papers()
    filtered_path.write_text(
        "\n".join(json.dumps(p) for p in papers) + "\n", encoding="utf-8"
    )

    monkeypatch.setattr(epc, "_fetch_s2_pdf", lambda paper, timeout: None)
    monkeypatch.setattr(epc, "_fetch_arxiv_pdf", lambda paper, timeout: None)
    monkeypatch.setattr(epc, "_fetch_html_text", lambda paper, timeout: None)

    rc = epc.main(
        [
            "--fetch-all",
            "--filtered",
            str(filtered_path),
            "--cache-dir",
            str(cache_dir),
            "--concurrency",
            "1",
        ]
    )
    assert rc == 0
    assert (cache_dir / "smith2024paper.txt").exists()
    assert (cache_dir / "doe2024paper.txt").exists()

    smith = (cache_dir / "smith2024paper.txt").read_text(encoding="utf-8")
    assert smith.startswith("# source: abstract_fallback\n")
    assert "method A with 64 layers" in smith


def test_fetch_all_mode_uses_mocked_priority_chain(tmp_path, monkeypatch):
    """The priority chain (S2 → arXiv → HTML → abstract) is always exercised."""
    filtered_path = tmp_path / "filtered.jsonl"
    cache_dir = tmp_path / "cache"
    paper = {
        "cite_key": "kx",
        "paper_id": "p1",
        "arxiv_id": "2401.12345",
        "abstract": "ORIGINAL ABSTRACT",
    }
    filtered_path.write_text(json.dumps(paper) + "\n", encoding="utf-8")

    monkeypatch.setattr(epc, "_fetch_s2_pdf", lambda paper, timeout: None)
    monkeypatch.setattr(
        epc,
        "_fetch_arxiv_pdf",
        lambda paper, timeout: "ARXIV BODY EXTRACTED FROM PDF",
    )
    monkeypatch.setattr(epc, "_fetch_html_text", lambda paper, timeout: None)

    rc = epc.main(
        [
            "--fetch-all",
            "--filtered",
            str(filtered_path),
            "--cache-dir",
            str(cache_dir),
            "--concurrency",
            "1",
        ]
    )
    assert rc == 0
    cached = (cache_dir / "kx.txt").read_text(encoding="utf-8")
    assert cached.startswith("# source: arxiv_pdf\n")
    assert "ARXIV BODY EXTRACTED FROM PDF" in cached
    assert "ORIGINAL ABSTRACT" in cached


# ---------------------------------------------------------------------------
# Mode 3: --write-cards
# ---------------------------------------------------------------------------


def test_write_cards_mode_writes_jsonl_and_cards(tmp_path):
    # Set up the input directories.
    extractions_dir = tmp_path / "extractions"
    extractions_dir.mkdir()
    schema_path = tmp_path / "schema.json"
    filtered_path = tmp_path / "filtered.jsonl"
    output_dir = tmp_path / "1_search"
    output_dir.mkdir()

    schema = {
        "_template_used": "tiny",
        "groups": {"method": {"approach": "str", "layers": "int"}},
    }
    schema_path.write_text(json.dumps(schema), encoding="utf-8")

    papers = _two_papers()
    filtered_path.write_text(
        "\n".join(json.dumps(p) for p in papers) + "\n", encoding="utf-8"
    )

    # Pre-populate the fetched cache so source labels round-trip.
    cache_dir = output_dir / "cards" / "_fetched"
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / "smith2024paper.txt").write_text(
        "# source: arxiv_pdf\nbody body body\n", encoding="utf-8"
    )
    (cache_dir / "doe2024paper.txt").write_text(
        "# source: abstract_fallback\nbody body\n", encoding="utf-8"
    )

    # Agent-produced extraction JSONs (one per cite_key).
    (extractions_dir / "smith2024paper.json").write_text(
        json.dumps({"method": {"approach": "dense transformer", "layers": 60}}),
        encoding="utf-8",
    )
    (extractions_dir / "doe2024paper.json").write_text(
        json.dumps({"method": {"approach": "MoE", "layers": 80}}),
        encoding="utf-8",
    )

    rc = epc.main(
        [
            "--write-cards",
            "--extractions-dir",
            str(extractions_dir),
            "--schema",
            str(schema_path),
            "--filtered",
            str(filtered_path),
            "--output-dir",
            str(output_dir),
        ]
    )
    assert rc == 0

    # cards.jsonl has 2 lines.
    cards_jsonl = output_dir / "cards.jsonl"
    assert cards_jsonl.exists()
    lines = [
        json.loads(l)
        for l in cards_jsonl.read_text(encoding="utf-8").splitlines()
        if l.strip()
    ]
    assert len(lines) == 2
    keys = {r["cite_key"] for r in lines}
    assert keys == {"smith2024paper", "doe2024paper"}

    # Source labels round-trip from cache header line.
    by_key = {r["cite_key"]: r for r in lines}
    assert by_key["smith2024paper"]["extraction_source"] == "arxiv_pdf"
    assert by_key["doe2024paper"]["extraction_source"] == "abstract_fallback"

    # Per-paper card files exist.
    cards_dir = output_dir / "cards"
    assert (cards_dir / "smith2024paper.md").exists()
    assert (cards_dir / "doe2024paper.md").exists()

    smith_md = (cards_dir / "smith2024paper.md").read_text(encoding="utf-8")
    assert "# Smith Paper" in smith_md
    assert "## method" in smith_md
    assert "- layers: 60" in smith_md
    assert "## _meta" in smith_md


def test_write_cards_mode_coerces_invalid_to_NR(tmp_path, capsys):
    extractions_dir = tmp_path / "extractions"
    extractions_dir.mkdir()
    schema_path = tmp_path / "schema.json"
    filtered_path = tmp_path / "filtered.jsonl"
    output_dir = tmp_path / "1_search"
    output_dir.mkdir()

    schema = {"groups": {"method": {"approach": "str", "layers": "int"}}}
    schema_path.write_text(json.dumps(schema), encoding="utf-8")
    paper = _two_papers()[0]
    filtered_path.write_text(json.dumps(paper) + "\n", encoding="utf-8")

    # ``layers`` value can't be coerced to int.
    (extractions_dir / "smith2024paper.json").write_text(
        json.dumps({"method": {"approach": "transformer", "layers": "many"}}),
        encoding="utf-8",
    )

    rc = epc.main(
        [
            "--write-cards",
            "--extractions-dir",
            str(extractions_dir),
            "--schema",
            str(schema_path),
            "--filtered",
            str(filtered_path),
            "--output-dir",
            str(output_dir),
        ]
    )
    assert rc == 0

    record = json.loads(
        (output_dir / "cards.jsonl").read_text(encoding="utf-8").strip()
    )
    assert record["extraction"]["method"]["approach"] == "transformer"
    assert record["extraction"]["method"]["layers"] == "N/R"
    err = capsys.readouterr().err
    assert "method.layers" in err


# ---------------------------------------------------------------------------
# Default mode: no flag → usage + non-zero exit
# ---------------------------------------------------------------------------


def test_default_mode_prints_usage_and_exits_nonzero(capsys):
    rc = epc.main([])
    assert rc == 1
    err = capsys.readouterr().err
    assert "--validate-schema" in err
    assert "--fetch-all" in err
    assert "--write-cards" in err
