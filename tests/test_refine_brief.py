"""Tests for tools/refine_brief.py — Stage 0 brief refiner.

The refiner no longer makes any LLM calls; the agent (Claude Code while
interpreting skills/survey-run/SKILL.md) extracts structured JSON from
the free-form brief and passes it via --candidate. These tests therefore
hand-craft candidate JSON payloads instead of mocking an LLM client.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

# Make tools/ importable for direct function access.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import refine_brief  # noqa: E402


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


REFERENCE_BRIEF_PATH = ROOT / "examples" / "briefs" / "long-context-extension.md"


def _full_parsed_for_reference_brief() -> dict:
    """Hand-crafted JSON the agent should extract from the
    long-context-extension reference brief."""
    return {
        "topic": "Long-Context Extension Methods for Pretrained Language Models",
        "scope": {
            "include": [
                "decoder-only language models",
                "context-window extension at pretraining, mid-training, or inference time",
                "positional-encoding interpolation (RoPE, NTK, YaRN, LongRoPE)",
                "sparse / sliding-window attention used post-hoc on a pretrained Transformer",
                "KV-cache compression and eviction at decode time",
                "retrieval-in-context that keeps the LM context long",
                "linear-attention models used as drop-in replacements",
            ],
            "exclude": [
                "encoder-only and encoder-decoder models",
                "vision and multimodal long-context",
                "from-scratch linear-attention architectures (Mamba, RWKV, RetNet)",
                "pure RAG with short LM context",
            ],
        },
        "sources": {
            "categories": [
                "arxiv",
                "semantic_scholar",
                "openalex",
                "tech_reports",
                "blogs",
            ],
            "year_range": [2023, 2026],
            "github_repos": [],
            "model_cards": [],
        },
        "dimensions": [
            {"name": "family", "description": "PE interpolation / sparse attention / KV management / retrieval / SSM hybrid / training-free trick"},
            {"name": "reach", "description": "max context window claimed and demonstrably useful (NIAH, RULER, ∞Bench)"},
            {"name": "adaptation_cost", "description": "training-free, finetune-only, continued pretraining, or full pretraining"},
            {"name": "decode_compute", "description": "prefill cost, per-token decode cost, KV-cache memory"},
            {"name": "long_quality", "description": "perplexity / accuracy degradation vs short-context baseline on long benchmarks"},
            {"name": "failure_modes", "description": "lost-in-the-middle, attention sinks, retrieval collapse, repeating-token failures"},
        ],
        "style": [
            "detail-driven",
            "forward-looking",
            "SOTA comparison",
            "for each paper extract: method, base model, max context, benchmark numbers, adaptation budget, decode cost, failure mode",
        ],
        "configuration": {
            "trends_section": "include",
        },
        "_uncertainties": [],
    }


def _minimal_valid_parsed() -> dict:
    return {
        "topic": "Efficient Transformers",
        "scope": {
            "include": ["efficiency techniques for attention"],
            "exclude": [],
        },
        "sources": {
            "categories": ["arxiv", "semantic_scholar"],
            "year_range": [2021, 2026],
            "github_repos": [],
            "model_cards": [],
        },
        "dimensions": [
            {"name": "attention", "description": "linear, sparse, kernelised"},
            {"name": "memory", "description": "cache compression, KV reuse"},
            {"name": "throughput", "description": "kernels, batching"},
        ],
        "style": ["detail-driven"],
        "configuration": {
            "trends_section": "include",
        },
        "_uncertainties": ["sources not specified, defaulted to common venues"],
    }


def _write_candidate(tmp_path: Path, payload: dict) -> Path:
    """Persist a hand-crafted candidate JSON and return its path."""
    p = tmp_path / "candidate.json"
    p.write_text(json.dumps(payload), encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# 1. Reference-brief candidate validates cleanly
# ---------------------------------------------------------------------------


def test_reference_brief_validates_when_well_formed():
    """A candidate matching the long-context-extension reference brief
    passes validation with all required fields populated."""
    parsed = _full_parsed_for_reference_brief()

    ok, err = refine_brief.validate_parsed(parsed)
    assert ok, f"validation failed: {err}"

    assert "Long-Context" in parsed["topic"]
    excludes = parsed["scope"]["exclude"]
    assert len(excludes) >= 3
    assert any("encoder" in x for x in excludes)
    assert any("vision" in x.lower() or "multimodal" in x.lower() for x in excludes)
    assert len(parsed["dimensions"]) >= 5
    # required structural fields present after validation
    assert isinstance(parsed["sources"]["categories"], list)
    assert isinstance(parsed["sources"]["year_range"], list)
    assert isinstance(parsed["style"], list)
    assert isinstance(parsed["configuration"], dict)


# ---------------------------------------------------------------------------
# 2. Minimal brief passes with exactly 3 dimensions
# ---------------------------------------------------------------------------


def test_minimal_brief_passes_with_3_dimensions():
    parsed = _minimal_valid_parsed()
    ok, err = refine_brief.validate_parsed(parsed)
    assert ok, f"validation failed: {err}"
    assert len(parsed["dimensions"]) == 3


# ---------------------------------------------------------------------------
# 3. Too-short brief fails fast (no candidate involved)
# ---------------------------------------------------------------------------


def test_too_short_brief_fails_fast(tmp_path):
    p = tmp_path / "tiny.md"
    p.write_text("topic: tiny brief with only ten words here please")

    with pytest.raises(ValueError) as exc:
        refine_brief.read_brief(str(p))
    assert "too short" in str(exc.value)

    # Now via CLI: should exit 1. We pass a (valid-shaped) candidate so the
    # short-brief check is what trips, not the candidate gate.
    candidate_path = _write_candidate(tmp_path, _minimal_valid_parsed())
    out_path = tmp_path / "out.json"
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "tools" / "refine_brief.py"),
            "--brief",
            str(p),
            "--candidate",
            str(candidate_path),
            "--output",
            str(out_path),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1, result.stderr
    assert "too short" in result.stderr


# ---------------------------------------------------------------------------
# 4. No topic detected fails
# ---------------------------------------------------------------------------


def test_no_topic_detected_fails():
    parsed = _minimal_valid_parsed()
    parsed["topic"] = ""
    ok, err = refine_brief.validate_parsed(parsed)
    assert ok is False
    assert "could not identify a topic" in err


# ---------------------------------------------------------------------------
# 5. Under 3 dimensions fails
# ---------------------------------------------------------------------------


def test_under_3_dimensions_fails():
    parsed = _minimal_valid_parsed()
    parsed["dimensions"] = [
        {"name": "X", "description": "Y"},
        {"name": "Z", "description": "W"},
    ]
    ok, err = refine_brief.validate_parsed(parsed)
    assert ok is False
    assert "needs >=3 thematic axes" in err


# ---------------------------------------------------------------------------
# 6. Explicit URLs preserved through the full pipeline
# ---------------------------------------------------------------------------


def test_explicit_urls_preserved(tmp_path):
    brief = (
        "topic: nanoGPT-style pretraining survey\n\n"
        "We want a focused survey of nanoGPT-flavoured pretraining tips. "
        "Notable references include https://github.com/karpathy/nanoGPT and "
        "https://github.com/foo/bar for canonical implementations. The "
        "survey should cover architecture, optimization, and data, and "
        "report concrete numbers throughout the document where possible. "
        "Cover years 2021 through 2026 across academic and industry work, "
        "with extra emphasis on training stability."
    )
    assert len(brief.split()) >= 50

    parsed_payload = _minimal_valid_parsed()
    parsed_payload["topic"] = "nanoGPT-style pretraining survey"
    parsed_payload["sources"]["github_repos"] = [
        "https://github.com/karpathy/nanoGPT",
        "https://github.com/foo/bar",
    ]

    brief_path = tmp_path / "brief.md"
    brief_path.write_text(brief)
    candidate_path = _write_candidate(tmp_path, parsed_payload)
    out_path = tmp_path / "brief.parsed.json"

    # Validate via the candidate first (unit-level).
    ok, err = refine_brief.validate_parsed(parsed_payload)
    assert ok, f"validation failed: {err}"

    # Now end-to-end through main().
    rc = refine_brief.main(
        [
            "--brief",
            str(brief_path),
            "--candidate",
            str(candidate_path),
            "--output",
            str(out_path),
        ]
    )
    assert rc == 0

    written = json.loads(out_path.read_text())
    assert written["sources"]["github_repos"] == [
        "https://github.com/karpathy/nanoGPT",
        "https://github.com/foo/bar",
    ]


# ---------------------------------------------------------------------------
# 7. Default style augmentation appends forward-looking
# ---------------------------------------------------------------------------


def test_default_style_augmentation_appends_forward_looking():
    parsed = {
        "style": ["detail-driven"],
        "configuration": {"trends_section": "include"},
    }
    refine_brief.apply_default_style_augmentation(parsed)
    assert refine_brief.FORWARD_LOOKING_RULE in parsed["style"]
    assert "detail-driven" in parsed["style"]


# ---------------------------------------------------------------------------
# 8. no-forward-looking opt-out
# ---------------------------------------------------------------------------


def test_no_forward_looking_opt_out():
    parsed = {
        "style": ["detail-driven", "no-forward-looking"],
        "configuration": {"trends_section": "include"},
    }
    refine_brief.apply_default_style_augmentation(parsed)
    assert refine_brief.FORWARD_LOOKING_RULE not in parsed["style"]


# ---------------------------------------------------------------------------
# 9. trends_section: skip opt-out
# ---------------------------------------------------------------------------


def test_trends_skip_opt_out():
    parsed = {
        "style": ["detail-driven"],
        "configuration": {"trends_section": "skip"},
    }
    refine_brief.apply_default_style_augmentation(parsed)
    assert refine_brief.FORWARD_LOOKING_RULE not in parsed["style"]


# ---------------------------------------------------------------------------
# 9b. Configuration only carries `trends_section` (no dead flags)
# ---------------------------------------------------------------------------


def test_coerce_defaults_only_injects_trends_section():
    """`_coerce_defaults` must inject only `trends_section`. It must not
    inject `evolution_dag` or `geo_landscape`: those keys have no consumer
    anywhere in the pipeline and adding them to every parsed brief just
    pollutes the user-visible display with dead config."""
    parsed = {"topic": "demo", "configuration": {}}
    refine_brief._coerce_defaults(parsed)

    cfg = parsed["configuration"]
    assert cfg == {"trends_section": "include"}, (
        f"configuration must collapse to a single key; got {cfg}"
    )


def test_validate_parsed_accepts_brief_without_dead_flags():
    """A brief whose configuration only declares `trends_section` must
    pass validation; the validator must not require `evolution_dag` /
    `geo_landscape`."""
    parsed = _minimal_valid_parsed()
    parsed["configuration"] = {"trends_section": "include"}
    ok, err = refine_brief.validate_parsed(parsed)
    assert ok, err


# ---------------------------------------------------------------------------
# 10. Caching: skip when output is newer
# ---------------------------------------------------------------------------


def test_caching_skips_when_output_newer(tmp_path):
    brief_path = tmp_path / "brief.md"
    brief_path.write_text("contents")
    out_path = tmp_path / "out.json"
    out_path.write_text("{}")

    # Make output explicitly newer.
    older = time.time() - 100
    newer = time.time()
    os.utime(brief_path, (older, older))
    os.utime(out_path, (newer, newer))

    assert refine_brief.is_cache_valid(str(brief_path), str(out_path)) is True


# ---------------------------------------------------------------------------
# 11. Caching: invalid when brief is newer
# ---------------------------------------------------------------------------


def test_caching_invalid_when_brief_newer(tmp_path):
    brief_path = tmp_path / "brief.md"
    brief_path.write_text("contents")
    out_path = tmp_path / "out.json"
    out_path.write_text("{}")

    older = time.time() - 100
    newer = time.time()
    os.utime(out_path, (older, older))
    os.utime(brief_path, (newer, newer))

    assert refine_brief.is_cache_valid(str(brief_path), str(out_path)) is False


# ---------------------------------------------------------------------------
# 12. Display includes topic and dimensions
# ---------------------------------------------------------------------------


def test_format_display_includes_topic_and_dims(sample_brief, tmp_path):
    # The sample_brief fixture stores dimensions as bare strings; convert
    # them to {name, description} entries the way refine_brief expects.
    parsed = {
        "topic": sample_brief["topic"],
        "scope": sample_brief["scope"],
        "sources": dict(sample_brief["sources"]),
        "dimensions": [
            {"name": d, "description": f"axis: {d}"}
            for d in sample_brief["dimensions"]
        ],
        "style": ["detail-driven"],
        "configuration": {
            "trends_section": "include",
        },
        "_uncertainties": [],
    }

    out = tmp_path / "brief.parsed.json"
    brief = tmp_path / "brief.md"
    text = refine_brief.format_display(parsed, str(out), str(brief))

    assert sample_brief["topic"] in text
    for d in sample_brief["dimensions"]:
        assert d in text
    assert "Saved to:" in text


# ---------------------------------------------------------------------------
# 13. Display omits empty uncertainties
# ---------------------------------------------------------------------------


def test_format_display_omits_empty_uncertainties(sample_brief, tmp_path):
    parsed = {
        "topic": sample_brief["topic"],
        "scope": sample_brief["scope"],
        "sources": dict(sample_brief["sources"]),
        "dimensions": [
            {"name": d, "description": f"axis: {d}"}
            for d in sample_brief["dimensions"]
        ],
        "style": ["detail-driven"],
        "configuration": {
            "trends_section": "include",
        },
        "_uncertainties": [],
    }
    out = tmp_path / "brief.parsed.json"
    brief = tmp_path / "brief.md"

    text = refine_brief.format_display(parsed, str(out), str(brief))
    assert "Uncertainties:" not in text


# ---------------------------------------------------------------------------
# 14. CLI writes output file (now via --candidate, no LLM mocking)
# ---------------------------------------------------------------------------


def test_cli_writes_output_file(tmp_path):
    brief_path = tmp_path / "brief.md"
    brief_path.write_text(REFERENCE_BRIEF_PATH.read_text(encoding="utf-8"))
    out_path = tmp_path / "brief.parsed.json"

    candidate_path = _write_candidate(
        tmp_path, _full_parsed_for_reference_brief()
    )

    rc = refine_brief.main(
        [
            "--brief",
            str(brief_path),
            "--candidate",
            str(candidate_path),
            "--output",
            str(out_path),
        ]
    )
    assert rc == 0
    assert out_path.exists()
    data = json.loads(out_path.read_text())
    assert "Long-Context" in data["topic"]
    # forward-looking rule should have been auto-appended.
    assert refine_brief.FORWARD_LOOKING_RULE in data["style"]
