"""Tests for tools/verify_papers.py — 3-layer paper-existence verifier.

All HTTP traffic is monkeypatched at the ``http_get`` boundary (the
single point all three layers share), so this entire suite must run
with the network unplugged. Any accidental real-network call will
manifest as a long timeout — we additionally guard by patching
``urllib.request.urlopen`` to ``RuntimeError`` so a stray real call
fails loudly instead of hanging.
"""

from __future__ import annotations

import json
import subprocess
import sys
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import verify_papers as vp  # noqa: E402


# ---------------------------------------------------------------------------
# Network kill-switch
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _no_real_network(monkeypatch):
    """Replace the low-level urlopen with a screamer so any test that
    forgets to stub http_get fails loudly instead of hitting the network."""
    def _scream(*args, **kwargs):
        raise RuntimeError(
            "verify_papers test attempted real urlopen — stub http_get instead"
        )
    monkeypatch.setattr(urllib.request, "urlopen", _scream)


# ---------------------------------------------------------------------------
# Pure-function helpers (no I/O)
# ---------------------------------------------------------------------------


def test_normalize_arxiv_strips_version_suffix():
    """`2001.08361v3` → ('2001.08361', 'v3') so cache keys collapse versions."""
    base, ver = vp.normalize_arxiv_id("2001.08361v3")
    assert base == "2001.08361"
    assert ver == "v3"

    base2, ver2 = vp.normalize_arxiv_id("2001.08361")
    assert base2 == "2001.08361"
    assert ver2 in ("", None)

    base3, _ = vp.normalize_arxiv_id("  2001.08361v1  ")
    assert base3 == "2001.08361"


def test_cache_key_priority_arxiv_over_doi_over_title():
    """Spec: arxiv > doi > title-hash."""
    p_full = vp.PaperInput(id="p", arxiv_id="2001.08361",
                            doi="10.1/foo", title="Anything")
    assert vp.cache_key_for(p_full).startswith("arxiv:")

    p_doi = vp.PaperInput(id="p", doi="10.1/foo", title="Anything")
    assert vp.cache_key_for(p_doi).startswith("doi:")

    p_title = vp.PaperInput(id="p", title="Some title")
    assert vp.cache_key_for(p_title).startswith("title:")

    p_empty = vp.PaperInput(id="p")
    assert vp.cache_key_for(p_empty) is None


def test_compute_verdict_pass_no_warnings():
    """All verified, no pending → PASS, hallucination_rate=0, empty warnings."""
    results = [
        vp.PaperResult(id="p1", status="verified", method="arxiv"),
        vp.PaperResult(id="p2", status="verified", method="crossref"),
    ]
    verdict, summary = vp.compute_verdict(results, threshold=0.2)
    assert verdict == "PASS"
    assert summary["hallucination_rate"] == 0.0
    assert summary["pending_rate"] == 0.0
    assert summary["warnings"] == []


def test_compute_verdict_warn_high_hallucination():
    """unverified ratio > threshold → WARN with high_hallucination_rate."""
    results = [
        vp.PaperResult(id="p1", status="verified", method="arxiv"),
        vp.PaperResult(id="p2", status="unverified",
                        reason="no_arxiv_no_doi_no_s2_match"),
        vp.PaperResult(id="p3", status="unverified",
                        reason="no_arxiv_no_doi_no_s2_match"),
    ]
    verdict, summary = vp.compute_verdict(results, threshold=0.2)
    assert verdict == "WARN"
    assert "high_hallucination_rate" in summary["warnings"]
    assert summary["hallucination_rate"] > 0.5


def test_compute_verdict_blocked_on_empty_input():
    verdict, summary = vp.compute_verdict([], threshold=0.2)
    assert verdict == "BLOCKED"


# ---------------------------------------------------------------------------
# 3-layer fallback via http_get monkeypatch
# ---------------------------------------------------------------------------


def _arxiv_atom_for(arxiv_id: str) -> str:
    """Minimal arXiv API atom feed entry that matches the success rule
    (`<id>http://arxiv.org/abs/{base}`)."""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<feed xmlns="http://www.w3.org/2005/Atom">'
        f'<entry><id>http://arxiv.org/abs/{arxiv_id}v1</id>'
        '<title>Stub Paper</title></entry></feed>'
    )


def test_arxiv_layer_verifies_via_http_get_stub(monkeypatch):
    calls: list[str] = []

    def fake_http_get(url, headers=None, timeout=10):
        calls.append(url)
        # arXiv API
        return 200, _arxiv_atom_for("2001.08361")

    monkeypatch.setattr(vp, "http_get", fake_http_get)
    out = vp.verify_arxiv_batch(["2001.08361"], batch_size=40)
    assert out == {"2001.08361": "verified"}
    assert any("export.arxiv.org" in u for u in calls)


def test_arxiv_layer_returns_unverified_on_404(monkeypatch):
    monkeypatch.setattr(vp, "http_get", lambda *a, **k: (404, ""))
    # arXiv tolerates 404 by returning unverified for batch entries
    out = vp.verify_arxiv_batch(["9999.99999"], batch_size=40)
    assert out["9999.99999"] in {"unverified", "verify_pending"}


def test_doi_layer_verified_and_unverified(monkeypatch):
    monkeypatch.setattr(vp, "http_get", lambda *a, **k: (200, '{"message":{}}'))
    assert vp.verify_doi("10.1/x", "test@x.local") == "verified"
    monkeypatch.setattr(vp, "http_get", lambda *a, **k: (404, ""))
    assert vp.verify_doi("10.1/x", "test@x.local") == "unverified"


# ---------------------------------------------------------------------------
# Orchestration: verify_papers() with cache short-circuit
# ---------------------------------------------------------------------------


def test_verify_papers_uses_cache_and_skips_network(monkeypatch):
    """If a paper is in cache, http_get must NOT be called for it."""
    call_count = {"n": 0}

    def fail_http_get(*a, **k):
        call_count["n"] += 1
        # Should not be reached for the cached paper.
        return 200, _arxiv_atom_for("2001.08361")

    monkeypatch.setattr(vp, "http_get", fail_http_get)

    paper = vp.PaperInput(id="p1", arxiv_id="2001.08361")
    cache = {
        vp.cache_key_for(paper): {
            "status":     "verified",
            "method":     "arxiv",
            "confidence": "high",
        }
    }
    results = vp.verify_papers(
        [paper], arxiv_batch_size=40, fuzzy_threshold=0.6,
        user_email="t@x.local", cache=cache,
    )
    assert len(results) == 1
    assert results[0].status == "verified"
    assert results[0].method == "arxiv"
    assert call_count["n"] == 0, "cache should have short-circuited the network"


# ---------------------------------------------------------------------------
# CLI smoke
# ---------------------------------------------------------------------------


def test_cli_help_exits_zero():
    """argparse --help must work even if the rest of the file is byte-perfect."""
    res = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "verify_papers.py"), "--help"],
        capture_output=True, text=True,
    )
    assert res.returncode == 0
    assert "--input" in res.stdout
    assert "--arxiv-ids" in res.stdout
    assert "--cache-scope" in res.stdout


def test_cli_arxiv_ids_with_no_cache_and_stubbed_http(tmp_path, monkeypatch):
    """End-to-end: --arxiv-ids 2001.08361 --no-cache → PASS verdict via stub."""
    # We can't easily monkeypatch a subprocess, so instead we drive verify_papers
    # in-process and assert the same output the CLI produces.
    monkeypatch.setattr(vp, "http_get",
                        lambda *a, **k: (200, _arxiv_atom_for("2001.08361")))
    papers = [vp.PaperInput(id="arxiv-0", arxiv_id="2001.08361")]
    results = vp.verify_papers(
        papers, arxiv_batch_size=40, fuzzy_threshold=0.6,
        user_email="t@x.local", cache=None,
    )
    verdict, summary = vp.compute_verdict(results, threshold=0.2)
    assert verdict == "PASS"
    assert all(r.status == "verified" for r in results)


# ---------------------------------------------------------------------------
# Cache directory: visibility + self-shielding
# ---------------------------------------------------------------------------


def test_save_cache_self_shields_new_dir_with_gitignore(tmp_path, capsys):
    """Regression: project-scope cache silently created `.autosurvey/cache/`
    in cwd. save_cache must (a) print a one-line announcement on first
    creation, (b) drop a `.gitignore` so the dir is self-shielded
    regardless of the surrounding repo's gitignore."""
    cache_path = tmp_path / "cache" / "verify_papers.json"
    assert not cache_path.parent.exists()

    vp.save_cache(cache_path, {"foo": {"ts": 1, "status": "verified"}})

    # Cache file written
    assert cache_path.exists()
    payload = json.loads(cache_path.read_text())
    assert payload["foo"]["status"] == "verified"

    # Self-shielding .gitignore was dropped
    gi = cache_path.parent / ".gitignore"
    assert gi.exists()
    assert "*" in gi.read_text()

    # Announcement printed to stderr
    captured = capsys.readouterr()
    assert "cache dir created" in captured.err
    assert "self-shielded" in captured.err


def test_save_cache_silent_on_existing_dir(tmp_path, capsys):
    """Subsequent saves into an existing cache dir must NOT re-print the
    announcement (idempotent UX)."""
    cache_path = tmp_path / "cache" / "verify_papers.json"
    cache_path.parent.mkdir()  # pre-existing — simulate 2nd run

    vp.save_cache(cache_path, {"foo": {"ts": 1, "status": "verified"}})

    captured = capsys.readouterr()
    assert "cache dir created" not in captured.err
    # Pre-existing dir should not get an auto-gitignore (we don't
    # touch user-managed directories).
    assert not (cache_path.parent / ".gitignore").exists()


def test_save_cache_does_not_overwrite_existing_gitignore(tmp_path, capsys):
    """If the cache parent is brand-new but a .gitignore appeared
    between mkdir and the gitignore write (race / user pre-staged it),
    we must not clobber it."""
    cache_dir = tmp_path / "cache"
    # Force the "dir is new" branch: dir does NOT exist before the call.
    # We then race-prepare a .gitignore inside a parent that DOES exist
    # so that monkeypatching .exists() is unnecessary — we test the
    # post-create idempotency by calling save_cache twice.
    vp.save_cache(cache_dir / "verify_papers.json",
                  {"a": {"ts": 1, "status": "verified"}})
    gi = cache_dir / ".gitignore"
    custom = "# user-managed\nverify_papers.json\n"
    gi.write_text(custom)
    capsys.readouterr()  # discard first announcement

    # Second call — dir already exists, .gitignore already exists
    vp.save_cache(cache_dir / "verify_papers.json",
                  {"a": {"ts": 1, "status": "verified"},
                   "b": {"ts": 2, "status": "verified"}})

    assert gi.read_text() == custom  # untouched
    captured = capsys.readouterr()
    assert "cache dir created" not in captured.err  # no spam
