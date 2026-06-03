"""Tests for arxiv_fetch resilience: Retry-After handling + graceful 429 degrade.

These lock in the fix for a real failure observed in a run log, where a
sustained arXiv 429 raised an uncaught traceback and nearly aborted the whole
search step. The fetcher must now (a) honour the server's Retry-After header
and (b) degrade `search` to an empty `[]` on stdout (exit 0) instead of
crashing, so the pipeline can fall back to the other sources.
"""

from __future__ import annotations

import importlib.util
import urllib.error
from email.message import Message
from pathlib import Path

import pytest

_TOOLS = Path(__file__).resolve().parent.parent / "tools"
_spec = importlib.util.spec_from_file_location("arxiv_fetch", _TOOLS / "arxiv_fetch.py")
arxiv_fetch = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(arxiv_fetch)


_ATOM_ONE_ENTRY = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>http://arxiv.org/abs/2301.07041v1</id>
    <title>A Test Paper</title>
    <summary>An abstract.</summary>
    <published>2023-01-17T00:00:00Z</published>
    <updated>2023-01-17T00:00:00Z</updated>
    <author><name>Ada Lovelace</name></author>
  </entry>
</feed>"""


class _FakeResp:
    def __init__(self, data: bytes):
        self._data = data

    def read(self) -> bytes:
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _http_error(code: int, retry_after: str | None = None) -> urllib.error.HTTPError:
    hdrs = Message()
    if retry_after is not None:
        hdrs["Retry-After"] = retry_after
    return urllib.error.HTTPError("http://x", code, "throttled", hdrs, None)


def test_retry_after_is_honoured(monkeypatch):
    slept: list[float] = []
    monkeypatch.setattr(arxiv_fetch.time, "sleep", lambda s: slept.append(s))

    calls = {"n": 0}

    def fake_urlopen(req, timeout=30):
        calls["n"] += 1
        if calls["n"] == 1:
            raise _http_error(429, retry_after="7")
        return _FakeResp(_ATOM_ONE_ENTRY)

    monkeypatch.setattr(arxiv_fetch.urllib.request, "urlopen", fake_urlopen)

    results = arxiv_fetch.search("anything")
    assert len(results) == 1
    assert results[0]["title"] == "A Test Paper"
    # The single backoff used the server-advertised 7s, not the default 3s.
    assert slept == [7.0]


def test_retry_after_capped(monkeypatch):
    slept: list[float] = []
    monkeypatch.setattr(arxiv_fetch.time, "sleep", lambda s: slept.append(s))

    def fake_urlopen(req, timeout=30):
        if not slept:
            raise _http_error(503, retry_after="9999")
        return _FakeResp(_ATOM_ONE_ENTRY)

    monkeypatch.setattr(arxiv_fetch.urllib.request, "urlopen", fake_urlopen)

    arxiv_fetch.search("anything")
    assert slept == [arxiv_fetch._MAX_BACKOFF]


def test_sustained_429_degrades_gracefully(monkeypatch, capsys):
    monkeypatch.setattr(arxiv_fetch.time, "sleep", lambda s: None)

    def always_429(req, timeout=30):
        raise _http_error(429)

    monkeypatch.setattr(arxiv_fetch.urllib.request, "urlopen", always_429)

    rc = arxiv_fetch.main(["search", "history of conversational ai"])
    captured = capsys.readouterr()

    assert rc == 0  # must not abort the pipeline step
    assert captured.out.strip() == "[]"  # valid empty JSON for downstream parsing
    assert "rate-limited" in captured.err  # cause surfaced to the operator


def test_no_retry_after_falls_back_to_backoff(monkeypatch):
    slept: list[float] = []
    monkeypatch.setattr(arxiv_fetch.time, "sleep", lambda s: slept.append(s))

    def fake_urlopen(req, timeout=30):
        if not slept:
            raise _http_error(429)  # no Retry-After header
        return _FakeResp(_ATOM_ONE_ENTRY)

    monkeypatch.setattr(arxiv_fetch.urllib.request, "urlopen", fake_urlopen)

    arxiv_fetch.search("anything")
    assert slept == [3.0]  # default exponential base for attempt 0


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
