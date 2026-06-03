"""Unit tests for citation-graph snowballing (tools/snowball_citations.py).

The graph walk itself needs the network, so these tests pin the pure,
network-free core: id extraction across field conventions, co-citation /
coupling ranking, seed-identity dedup, and the CLI guards (missing seeds,
--dry-run makes no network calls). They also pin the additive
``referenced_works`` shortening in openalex_fetch._parse_work.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import openalex_fetch  # noqa: E402
import snowball_citations as sc  # noqa: E402


# ── id helpers ──────────────────────────────────────────────────────────────

def test_short_id_handles_url_plain_and_none():
    assert sc.short_id("https://openalex.org/W123") == "W123"
    assert sc.short_id("W456") == "W456"
    assert sc.short_id("https://openalex.org/W789/") == "W789"
    assert sc.short_id(None) is None
    assert sc.short_id("") is None


def test_extract_openalex_id_across_conventions():
    assert sc.extract_openalex_id({"openalex_id": "W1"}) == "W1"
    # the MoE run aliased paper_id->cite_key and kept the id here
    assert sc.extract_openalex_id({"openalex_paper_id": "W2"}) == "W2"
    assert sc.extract_openalex_id({"id": "https://openalex.org/W3"}) == "W3"
    assert sc.extract_openalex_id({"url": "https://openalex.org/W4"}) == "W4"
    assert sc.extract_openalex_id({"title": "no id here"}) is None


# ── ranking: bibliographic coupling + co-citation ───────────────────────────

def test_rank_candidates_counts_distinct_seed_overlap():
    seed_ids = {"S1", "S2", "S3"}
    neighbor_sets = {
        "S1": {"C1", "C2", "S2"},     # S2 is a seed -> must be excluded
        "S2": {"C1", "C3"},
        "S3": {"C1", "C2"},
    }
    ranked = sc.rank_candidates(seed_ids, neighbor_sets, min_overlap=2)
    d = dict(ranked)
    assert d["C1"] == 3          # linked by all three seeds
    assert d["C2"] == 2
    assert "C3" not in d         # only 1 seed -> below min_overlap
    assert "S2" not in d         # seeds never appear as candidates
    # ordering: highest overlap first
    assert ranked[0][0] == "C1"


def test_rank_candidates_citation_tiebreak_and_cap():
    seed_ids = {"S1", "S2"}
    neighbor_sets = {"S1": {"A", "B"}, "S2": {"A", "B"}}   # A,B both overlap 2
    ranked = sc.rank_candidates(
        seed_ids, neighbor_sets, meta_citations={"A": 5, "B": 99},
        min_overlap=2, max_candidates=1,
    )
    assert ranked == [("B", 2)]   # tie broken by citations, capped to 1


def test_seed_identity_set_collects_ids_dois_titles():
    seeds = [
        {"openalex_id": "W1", "doi": "https://doi.org/10.1/AbC", "title": "Switch Transformers!"},
        {"id": "https://openalex.org/W2", "title": "GShard"},
    ]
    ids, dois, titles = sc.seed_identity_set(seeds)
    assert ids == {"W1", "W2"}
    assert "10.1/abc" in dois
    assert "switch transformers" in titles


# ── openalex_fetch additive change ──────────────────────────────────────────

def test_parse_work_shortens_referenced_works():
    client = openalex_fetch.OpenAlexClient(email="t@example.com")
    rec = client._parse_work({
        "id": "https://openalex.org/W10",
        "display_name": "Seed",
        "referenced_works": [
            "https://openalex.org/W11", "https://openalex.org/W12", None,
        ],
    })
    assert rec["referenced_works"] == ["W11", "W12"]   # shortened, None dropped


# ── CLI guards (no network) ─────────────────────────────────────────────────

def test_cli_missing_seeds_file_returns_2(tmp_path: Path):
    rc = sc.main([str(tmp_path)])   # no 1_search/filtered.jsonl
    assert rc == 2


def test_cli_dry_run_makes_no_network_calls(tmp_path: Path, monkeypatch):
    search = tmp_path / "1_search"
    search.mkdir()
    (search / "filtered.jsonl").write_text(
        '{"openalex_id":"W1","title":"A","cited_by_count":50}\n'
        '{"openalex_id":"W2","title":"B","cited_by_count":10}\n'
    )
    # Any attempt to construct an OpenAlex client in dry-run is a bug.
    def _boom(*a, **k):
        raise AssertionError("dry-run must not touch the network")
    monkeypatch.setattr(openalex_fetch, "OpenAlexClient", _boom)
    rc = sc.main([str(tmp_path), "--dry-run"])
    assert rc == 0
