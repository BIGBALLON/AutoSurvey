"""Regression tests: tools/validate_outline.py and tools/build_dimension_tables.py
must accept BOTH the canonical schema (paper_id / section_id / primary_papers /
clusters.assignments / schema.groups) and the common drift-tolerant aliases
(cite_key / id / papers / inverted clusters dict / schema.fields[]).

If an agent run drifts slightly from the canonical shape we should still get
a clean validation pass, not a cryptic KeyError or zero-tables output.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
TOOLS = REPO / "tools"
VALIDATE = TOOLS / "validate_outline.py"
BUILD_TABLES = TOOLS / "build_dimension_tables.py"


# ---------------------------------------------------------------------------
# helpers — assemble run-dir fixtures in either schema shape
# ---------------------------------------------------------------------------


PAPERS = [
    {"id_field": "p1", "title": "Mixtral of Experts",                 "year": 2024},
    {"id_field": "p2", "title": "DeepSeek-V3",                         "year": 2024},
    {"id_field": "p3", "title": "OLMoE",                               "year": 2024},
    {"id_field": "p4", "title": "Better and Faster MTP",               "year": 2024},
    {"id_field": "p5", "title": "Leap MTP",                            "year": 2025},
    {"id_field": "p6", "title": "Scaling Law with LR Annealing",       "year": 2024},
]


def _write_filtered(run_dir: Path, *, key: str) -> None:
    """Write filtered.jsonl using either `paper_id` (canonical) or `cite_key` (alias)."""
    search = run_dir / "1_search"
    search.mkdir(parents=True, exist_ok=True)
    lines = []
    for p in PAPERS:
        record = {key: p["id_field"], "title": p["title"], "year": p["year"]}
        lines.append(json.dumps(record))
    (search / "filtered.jsonl").write_text("\n".join(lines) + "\n")


def _write_clusters_canonical(run_dir: Path) -> None:
    cluster_dir = run_dir / "2_cluster"
    cluster_dir.mkdir(parents=True, exist_ok=True)
    (cluster_dir / "clusters.json").write_text(json.dumps({
        "assignments": {
            "p1": "arch",  "p2": "arch",  "p3": "arch",
            "p4": "objective", "p5": "objective",
            "p6": "scaling",
        }
    }))


def _write_clusters_inverted(run_dir: Path) -> None:
    cluster_dir = run_dir / "2_cluster"
    cluster_dir.mkdir(parents=True, exist_ok=True)
    (cluster_dir / "clusters.json").write_text(json.dumps({
        "arch":      ["p1", "p2", "p3"],
        "objective": ["p4", "p5"],
        "scaling":   ["p6"],
    }))


def _write_clusters_flat(run_dir: Path) -> None:
    """Alternative cluster shape: bare {paper_id: node_id} dict, no wrapper."""
    cluster_dir = run_dir / "2_cluster"
    cluster_dir.mkdir(parents=True, exist_ok=True)
    (cluster_dir / "clusters.json").write_text(json.dumps({
        "p1": "arch", "p2": "arch", "p3": "arch",
        "p4": "objective", "p5": "objective",
        "p6": "scaling",
    }))


def _write_outline_canonical(run_dir: Path) -> None:
    """Outline using `section_id` / `primary_papers` / `taxonomy_nodes`."""
    outline_dir = run_dir / "4_outline"
    outline_dir.mkdir(parents=True, exist_ok=True)
    outline = {
        "topic": "test",
        "sections": [
            {"section_id": "intro", "title": "Introduction",
             "taxonomy_nodes": [], "primary_papers": [], "secondary_papers": []},
            {"section_id": "arch",  "title": "Architectures",
             "taxonomy_nodes": ["arch"],  "primary_papers": ["p1"], "secondary_papers": []},
            {"section_id": "objective", "title": "Objectives",
             "taxonomy_nodes": ["objective"], "primary_papers": [], "secondary_papers": []},
            {"section_id": "scaling", "title": "Scaling",
             "taxonomy_nodes": ["scaling"], "primary_papers": [], "secondary_papers": []},
        ],
    }
    (outline_dir / "outline.json").write_text(json.dumps(outline, indent=2))


def _write_outline_alias(run_dir: Path) -> None:
    """Outline using `id` / `papers` (no taxonomy_nodes — id == node id)."""
    outline_dir = run_dir / "4_outline"
    outline_dir.mkdir(parents=True, exist_ok=True)
    outline = {
        "topic": "test",
        "sections": [
            {"id": "intro",     "title": "Introduction", "papers": []},
            {"id": "arch",      "title": "Architectures", "papers": ["p1"]},
            {"id": "objective", "title": "Objectives",   "papers": []},
            {"id": "scaling",   "title": "Scaling",      "papers": []},
        ],
    }
    (outline_dir / "outline.json").write_text(json.dumps(outline, indent=2))


# ---------------------------------------------------------------------------
# validate_outline.py
# ---------------------------------------------------------------------------


def _run_validator(run_dir: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(VALIDATE), str(run_dir)],
        capture_output=True, text=True,
    )


def test_validate_outline_canonical(tmp_path: Path) -> None:
    """Canonical shape must validate without errors."""
    _write_filtered(tmp_path, key="paper_id")
    _write_clusters_canonical(tmp_path)
    _write_outline_canonical(tmp_path)

    res = _run_validator(tmp_path)
    assert res.returncode == 0, f"stderr:\n{res.stderr}\nstdout:\n{res.stdout}"

    repaired = json.loads((tmp_path / "4_outline/outline.json").read_text())
    arch = next(s for s in repaired["sections"] if s["section_id"] == "arch")
    # Back-fill should reach min_primary=3 from the cluster's three papers.
    assert len(arch["primary_papers"]) >= 3


def test_validate_outline_with_aliases(tmp_path: Path) -> None:
    """Drift-tolerant aliases (cite_key / id / papers / inverted clusters) must validate."""
    _write_filtered(tmp_path, key="cite_key")
    _write_clusters_inverted(tmp_path)
    _write_outline_alias(tmp_path)

    res = _run_validator(tmp_path)
    assert res.returncode == 0, f"stderr:\n{res.stderr}\nstdout:\n{res.stdout}"

    repaired = json.loads((tmp_path / "4_outline/outline.json").read_text())
    # The validator emits canonical names on output, regardless of input alias.
    arch = next(s for s in repaired["sections"]
                if s.get("section_id") == "arch" or s.get("id") == "arch")
    primary = arch.get("primary_papers", arch.get("papers", []))
    assert len(primary) >= 3


def test_validate_outline_with_flat_clusters(tmp_path: Path) -> None:
    """The flat cluster shape (bare {paper_id: node_id} dict) must validate too."""
    _write_filtered(tmp_path, key="paper_id")
    _write_clusters_flat(tmp_path)
    _write_outline_canonical(tmp_path)

    res = _run_validator(tmp_path)
    assert res.returncode == 0, f"stderr:\n{res.stderr}\nstdout:\n{res.stdout}"
    repaired = json.loads((tmp_path / "4_outline/outline.json").read_text())
    arch = next(s for s in repaired["sections"] if s["section_id"] == "arch")
    assert len(arch["primary_papers"]) >= 3


# ---------------------------------------------------------------------------
# build_dimension_tables.py
# ---------------------------------------------------------------------------


CARDS = [
    {"cite_key": "p1", "params_total": "47B",  "params_active": "13B",
     "attention_type": "GQA"},
    {"cite_key": "p2", "params_total": "671B", "params_active": "37B",
     "attention_type": "MLA"},
    {"cite_key": "p3", "params_total": "7B",   "params_active": "1B",
     "attention_type": "GQA"},
    {"cite_key": "p4", "params_total": "13B",  "training_objective": "MTP"},
    {"cite_key": "p5", "params_total": "7B",   "training_objective": "L-MTP"},
    {"cite_key": "p6"},
]


def _write_cards(run_dir: Path) -> None:
    search = run_dir / "1_search"
    search.mkdir(parents=True, exist_ok=True)
    (search / "cards.jsonl").write_text(
        "\n".join(json.dumps(c) for c in CARDS) + "\n"
    )


def _write_schema_groups(run_dir: Path) -> Path:
    """Canonical {groups: {<group>: {<field>: <type>}}} schema."""
    schema_path = run_dir / "brief.derived_schema.json"
    schema_path.write_text(json.dumps({
        "groups": {
            "arch": {
                "params_total": "str",
                "params_active": "str",
                "attention_type": "str",
            },
            "objective": {
                "params_total": "str",
                "training_objective": "str",
            },
        }
    }))
    return schema_path


def _write_schema_fields(run_dir: Path) -> Path:
    """Drift-tolerant {fields: [...]} schema with optional `group` per field."""
    schema_path = run_dir / "brief.derived_schema.json"
    schema_path.write_text(json.dumps({
        "fields": [
            {"name": "params_total",       "type": "str", "group": "arch"},
            {"name": "params_active",      "type": "str", "group": "arch"},
            {"name": "attention_type",     "type": "str", "group": "arch"},
            {"name": "training_objective", "type": "str", "group": "objective"},
        ]
    }))
    return schema_path


def _run_build_tables(run_dir: Path, schema_path: Path,
                      outline_writer) -> tuple[subprocess.CompletedProcess[str], list[Path]]:
    outline_writer(run_dir)
    _write_cards(run_dir)
    out_dir = run_dir / "5_paper/figures/tables"
    out_dir.mkdir(parents=True, exist_ok=True)

    res = subprocess.run(
        [
            sys.executable, str(BUILD_TABLES),
            "--cards", str(run_dir / "1_search/cards.jsonl"),
            "--outline", str(run_dir / "4_outline/outline.json"),
            "--schema",  str(schema_path),
            "--output-dir", str(out_dir),
            "--no-natbib",
        ],
        capture_output=True, text=True,
    )
    return res, sorted(out_dir.glob("*.tex"))


def test_build_tables_canonical_schema(tmp_path: Path) -> None:
    schema_path = _write_schema_groups(tmp_path)
    res, tex_files = _run_build_tables(tmp_path, schema_path, _write_outline_canonical)
    assert res.returncode == 0, f"stderr:\n{res.stderr}"
    assert tex_files, f"no tables generated. stderr:\n{res.stderr}"
    body = "\n".join(p.read_text() for p in tex_files)
    assert "DeepSeek" not in body  # tables use \cite{p2}, not titles
    assert r"\cite{p1}" in body or r"\cite{p2}" in body


def test_build_tables_alias_fields_schema(tmp_path: Path) -> None:
    """A `fields[]` schema (brief-contract style) should be normalised to groups internally."""
    schema_path = _write_schema_fields(tmp_path)
    res, tex_files = _run_build_tables(tmp_path, schema_path, _write_outline_alias)
    assert res.returncode == 0, f"stderr:\n{res.stderr}"
    assert tex_files, f"no tables generated. stderr:\n{res.stderr}"


# ---------------------------------------------------------------------------
# maturity_tier — 'mature → frontier → speculative' axis
# ---------------------------------------------------------------------------


def _outline_with_maturity(tiers_per_section: list[str | None]) -> dict:
    """Build a minimal outline whose body sections carry the given
    maturity_tier values (None = field absent on that section)."""
    sections: list[dict] = [
        {"section_id": "00_abstract"},
        {"section_id": "01_intro"},
    ]
    for i, tier in enumerate(tiers_per_section, start=2):
        sec: dict = {
            "section_id": f"{i:02d}_body_{i}",
            "argues_for_thesis_step": "S1",
            "argument_skeleton": {
                "claim":      "x", "steelman":   "x",
                "concession": "x", "so_what":    "x",
                "evidence_claim_keys": [],
            },
        }
        if tier is not None:
            sec["maturity_tier"] = tier
        sections.append(sec)
    return {"topic": "T", "sections": sections}


_THESIS_ONE_STEP = {"argument_steps": [{"step_id": "S1", "claim": "."}]}


def _import_validate_outline():
    """Load tools/validate_outline.py without going through subprocess
    (the unit tests above use the CLI; for these schema checks the
    in-process call is faster and gives clean violation strings)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("vo", VALIDATE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_validate_no_maturity_field_is_silent():
    """Outlines with no maturity_tier on any section must validate
    (the field is optional; outlines that don't use it pass)."""
    vo = _import_validate_outline()
    outline = _outline_with_maturity([None, None, None])
    issues = vo.validate_thesis_schema(outline, _THESIS_ONE_STEP)
    assert not any("maturity_tier" in m for m in issues)


def test_validate_maturity_unknown_value_flagged():
    vo = _import_validate_outline()
    outline = _outline_with_maturity(["mature", "tomorrow", "speculative"])
    issues = vo.validate_thesis_schema(outline, _THESIS_ONE_STEP)
    assert any("maturity_tier='tomorrow'" in m for m in issues)


def test_validate_maturity_single_tier_flagged_as_thin():
    """Using only one tier across the entire survey defeats the
    spectrum — must surface as a violation."""
    vo = _import_validate_outline()
    outline = _outline_with_maturity(["mature", "mature", "mature"])
    issues = vo.validate_thesis_schema(outline, _THESIS_ONE_STEP)
    assert any("maturity_tier coverage thin" in m for m in issues)


def test_validate_maturity_two_distinct_tiers_passes():
    vo = _import_validate_outline()
    outline = _outline_with_maturity(["mature", "frontier", None])
    issues = vo.validate_thesis_schema(outline, _THESIS_ONE_STEP)
    assert not any("maturity_tier" in m for m in issues)


def test_validate_maturity_full_spectrum_passes():
    vo = _import_validate_outline()
    outline = _outline_with_maturity(["mature", "frontier", "speculative"])
    issues = vo.validate_thesis_schema(outline, _THESIS_ONE_STEP)
    assert not any("maturity_tier" in m for m in issues)


def test_validate_tier_axis_maturity_unknown_value_flagged():
    """tier_axis.tiers[].maturity is the per-tier overlay used by the
    matrix figure. Same closed set; unknown values must be flagged."""
    vo = _import_validate_outline()
    outline = _outline_with_maturity([None, None, None])
    outline["tier_axis"] = {
        "name": "Generation",
        "tiers": [
            {"id": "T1", "label": "early",   "maturity": "mature"},
            {"id": "T2", "label": "current", "maturity": "tomorrow"},  # bad
            {"id": "T3", "label": "future",  "maturity": "speculative"},
        ],
        "feature_columns": ["f"],
        "cells": {},
    }
    issues = vo.validate_thesis_schema(outline, _THESIS_ONE_STEP)
    assert any("tier_axis.tiers[1].maturity='tomorrow'" in m for m in issues)


def test_validate_tier_axis_maturity_valid_values_silent():
    vo = _import_validate_outline()
    outline = _outline_with_maturity([None, None, None])
    outline["tier_axis"] = {
        "name": "Generation",
        "tiers": [
            {"id": "T1", "label": "a", "maturity": "mature"},
            {"id": "T2", "label": "b", "maturity": "frontier"},
        ],
        "feature_columns": ["f"],
        "cells": {},
    }
    issues = vo.validate_thesis_schema(outline, _THESIS_ONE_STEP)
    assert not any("maturity" in m and "T1" in m for m in issues)
    assert not any("maturity" in m and "T2" in m for m in issues)


# ---------------------------------------------------------------------------
# structural-template invariants (1, 4, 6) —
# shared-references/structural-template.md
# ---------------------------------------------------------------------------


def _well_formed_outline() -> dict:
    """Outline that satisfies the structural template (used as the positive
    fixture; perturbations below trip individual invariants)."""
    return {
        "sections": [
            {"id": "01_intro",        "section_type": "intro",
             "subsections": [{"id": s} for s in ("a", "b", "c")]},
            {"id": "02_background",   "section_type": "background",
             "subsections": [{"id": s} for s in ("a", "b", "c", "d")]},
            {"id": "03_architecture", "section_type": "body",
             "subsections": [{"id": s} for s in ("a", "b", "c")]},
            {"id": "04_systems",      "section_type": "body",
             "subsections": [
                 {"id": "general"},
                 {"id": "code"},
                 {"id": "matrix", "section_type": "cross_cutting_matrix"},
             ]},
            {"id": "05_eval",         "section_type": "body",
             "subsections": [{"id": s} for s in ("a", "b", "c")]},
            {"id": "06_problems",     "section_type": "open_problems",
             "items": [{"id": f"OP{i}", "paired_direction_id": f"FD{i}"}
                       for i in range(1, 7)]},
            {"id": "07_future",       "section_type": "future_directions",
             "items": [{"id": f"FD{i}"} for i in range(1, 7)]},
            {"id": "08_conclusion",   "section_type": "conclusion"},
        ]
    }


def test_validate_structural_template_passes_on_well_formed():
    vo = _import_validate_outline()
    issues = vo.validate_structural_template(_well_formed_outline())
    assert issues == [], f"expected zero violations, got {issues}"


def test_validate_structural_template_rejects_too_few_top_sections():
    vo = _import_validate_outline()
    outline = _well_formed_outline()
    # Drop everything except a single body section + open / future
    outline["sections"] = outline["sections"][3:6] + outline["sections"][6:7]
    issues = vo.validate_structural_template(outline)
    assert any("section_nesting" in i and "top-level" in i for i in issues)


def test_validate_structural_template_rejects_flat_outline():
    vo = _import_validate_outline()
    outline = _well_formed_outline()
    for sec in outline["sections"]:
        sec.pop("subsections", None)
    issues = vo.validate_structural_template(outline)
    assert any("≥ 3 subsections" in i for i in issues)


def test_validate_structural_template_rejects_missing_matrix_slot():
    vo = _import_validate_outline()
    outline = _well_formed_outline()
    for sec in outline["sections"]:
        for sub in sec.get("subsections", []):
            sub.pop("section_type", None)
    issues = vo.validate_structural_template(outline)
    assert any("cross_cutting_matrix" in i and "no" in i for i in issues)


def test_validate_structural_template_rejects_two_matrix_slots():
    vo = _import_validate_outline()
    outline = _well_formed_outline()
    # Add a second cross_cutting_matrix slot on a different body section
    outline["sections"][2]["subsections"].append(
        {"id": "extra_matrix", "section_type": "cross_cutting_matrix"}
    )
    issues = vo.validate_structural_template(outline)
    assert any("cross_cutting_matrix" in i and "exactly 1" in i for i in issues)


def test_validate_structural_template_rejects_unpaired_open_problems():
    vo = _import_validate_outline()
    outline = _well_formed_outline()
    for sec in outline["sections"]:
        if sec.get("section_type") == "open_problems":
            for item in sec["items"]:
                item.pop("paired_direction_id", None)
    issues = vo.validate_structural_template(outline)
    assert any("paired_direction_id" in i for i in issues)


def test_validate_structural_template_tolerates_count_off_by_one():
    """The benchmark survey runs 6 OP × 5 FD — strict equality is too rigid.
    The validator allows |Δ| ≤ 1."""
    vo = _import_validate_outline()
    outline = _well_formed_outline()
    for sec in outline["sections"]:
        if sec.get("section_type") == "future_directions":
            sec["items"].pop()  # 5 future vs 6 open
    issues = vo.validate_structural_template(outline)
    assert not any("counts differ" in i for i in issues)


def test_validate_structural_template_rejects_count_off_by_two():
    """A 2+ delta still trips the invariant."""
    vo = _import_validate_outline()
    outline = _well_formed_outline()
    for sec in outline["sections"]:
        if sec.get("section_type") == "future_directions":
            sec["items"].pop()
            sec["items"].pop()  # 4 future vs 6 open
    issues = vo.validate_structural_template(outline)
    assert any("counts differ" in i for i in issues)


def test_validate_structural_template_rejects_dangling_paired_direction_id():
    vo = _import_validate_outline()
    outline = _well_formed_outline()
    for sec in outline["sections"]:
        if sec.get("section_type") == "open_problems":
            sec["items"][0]["paired_direction_id"] = "FD_DOES_NOT_EXIST"
    issues = vo.validate_structural_template(outline)
    assert any("unknown future_directions ids" in i for i in issues)


def test_validate_structural_template_rejects_missing_open_or_future():
    vo = _import_validate_outline()
    outline = _well_formed_outline()
    outline["sections"] = [
        s for s in outline["sections"]
        if s.get("section_type") not in ("open_problems", "future_directions")
    ]
    issues = vo.validate_structural_template(outline)
    assert any("declares neither" in i for i in issues)


def test_validate_structural_template_count_window_is_5_to_8():
    vo = _import_validate_outline()
    outline = _well_formed_outline()
    for sec in outline["sections"]:
        if sec.get("section_type") == "open_problems":
            sec["items"] = [{"id": f"OP{i}", "paired_direction_id": f"FD{i}"}
                            for i in range(1, 4)]  # only 3
        if sec.get("section_type") == "future_directions":
            sec["items"] = [{"id": f"FD{i}"} for i in range(1, 4)]
    issues = vo.validate_structural_template(outline)
    assert any("must be 5–8" in i for i in issues)


# ---- CLI integration: --strict-template gates exit code ----


def _make_cli_run_dir(tmp_path: Path, outline: dict) -> Path:
    rd = tmp_path / "run"
    (rd / "4_outline").mkdir(parents=True)
    (rd / "1_search").mkdir(parents=True)
    (rd / "4_outline" / "outline.json").write_text(json.dumps(outline))
    (rd / "1_search" / "filtered.jsonl").write_text(
        json.dumps({"paper_id": "p1", "cite_key": "p1",
                    "title": "T", "year": 2024}) + "\n"
    )
    return rd


def test_cli_warns_on_structural_violation_without_strict_flag(tmp_path):
    """Default exit code is 0 — structural violations are advisory."""
    flat = {"sections": [{"id": "01", "section_type": "body"}]}
    rd = _make_cli_run_dir(tmp_path, flat)
    res = subprocess.run(
        [sys.executable, str(VALIDATE), str(rd), "--dry-run"],
        capture_output=True, text=True,
    )
    assert res.returncode == 0, res.stdout + res.stderr
    assert "structural-template violations" in res.stdout


def test_cli_fails_on_structural_violation_with_strict_template(tmp_path):
    flat = {"sections": [{"id": "01", "section_type": "body"}]}
    rd = _make_cli_run_dir(tmp_path, flat)
    res = subprocess.run(
        [sys.executable, str(VALIDATE), str(rd), "--dry-run", "--strict-template"],
        capture_output=True, text=True,
    )
    assert res.returncode == 1
    assert "structural-template violations" in res.stdout


def test_cli_passes_strict_template_on_well_formed(tmp_path):
    rd = _make_cli_run_dir(tmp_path, _well_formed_outline())
    res = subprocess.run(
        [sys.executable, str(VALIDATE), str(rd), "--dry-run", "--strict-template"],
        capture_output=True, text=True,
    )
    assert res.returncode == 0, res.stdout + res.stderr
    assert "structural-template:              OK" in res.stdout


def test_reference_outline_passes_strict_template(tmp_path):
    """Regression bar: the bundled reference outline (modelled on the
    benchmark survey 'From Copilots to Colleagues') must always satisfy
    --strict-template. If this test ever fails, either the reference
    asset has drifted from the contract, or the contract has tightened
    without updating the reference. Both are bugs."""
    ref = (REPO / "skills" / "shared-references"
           / "reference-assets" / "outline.example.json")
    assert ref.exists(), f"reference asset missing: {ref}"
    outline = json.loads(ref.read_text())
    rd = _make_cli_run_dir(tmp_path, outline)
    res = subprocess.run(
        [sys.executable, str(VALIDATE), str(rd), "--dry-run", "--strict-template"],
        capture_output=True, text=True,
    )
    # The reference declares argues_for_thesis_step on body sections but
    # ships without a thesis.json fixture; that surfaces as a thesis-
    # schema warning, not a structural-template violation. We assert on
    # the structural side only.
    assert "structural-template:              OK" in res.stdout, (
        f"reference outline must satisfy --strict-template; "
        f"stdout:\n{res.stdout}"
    )
