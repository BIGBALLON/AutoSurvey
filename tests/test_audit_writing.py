"""Tests for tools/audit_writing.py — writing-quality audit.

Covers the five audit areas through direct function calls (fast,
deterministic) plus a CLI smoke test for the submission gate.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import audit_writing as aw  # noqa: E402


def _read_sections(run_dir: Path) -> dict[str, str]:
    return aw._read_section_files(run_dir / "5_paper" / "sections")


# ---------------------------------------------------------------------------
# 1. argument_anchors (5 anchors per body section, in order)
# ---------------------------------------------------------------------------


def test_argument_anchors_clean_fixture(survey_run_dir):
    """Body sections in the fixture have all 5 anchors in canonical order."""
    sections = _read_sections(survey_run_dir)
    info = aw.audit_argument_anchors(sections)

    # 02_body is a body section; everything else is skipped (open_problems +
    # intro + conclusion + abstract). It should pass all 5.
    assert info["passing"] == info["total"], info
    assert info["score"] == 1.0
    assert "02_body" in info["per_section"]
    body_info = info["per_section"]["02_body"]
    assert body_info["ok"] is True
    assert body_info["anchors_found"] == ["CLAIM", "STEELMAN", "EVIDENCE",
                                           "CONCESSION", "SO-WHAT"]


def test_argument_anchors_missing_one_drops_score(survey_run_dir):
    """Removing the CONCESSION anchor must fail the body section."""
    body = survey_run_dir / "5_paper" / "sections" / "02_body.tex"
    text = body.read_text().replace("% [CONCESSION]", "% [REMOVED]")
    body.write_text(text)

    sections = _read_sections(survey_run_dir)
    info = aw.audit_argument_anchors(sections)
    assert info["score"] < 1.0
    body_info = info["per_section"]["02_body"]
    assert body_info["ok"] is False
    assert "CONCESSION" in body_info["issue"]


def test_argument_anchors_out_of_order_fails(survey_run_dir):
    """All 5 anchors present but reshuffled → out-of-order issue."""
    body = survey_run_dir / "5_paper" / "sections" / "02_body.tex"
    text = body.read_text()
    # Swap CLAIM and STEELMAN
    text = text.replace("% [CLAIM]", "__TMP_CLAIM__")
    text = text.replace("% [STEELMAN]", "% [CLAIM]")
    text = text.replace("__TMP_CLAIM__", "% [STEELMAN]")
    body.write_text(text)

    sections = _read_sections(survey_run_dir)
    info = aw.audit_argument_anchors(sections)
    body_info = info["per_section"]["02_body"]
    assert body_info["ok"] is False
    assert "out of order" in body_info["issue"]


# ---------------------------------------------------------------------------
# 2. open_problems (4 buckets per subsection)
# ---------------------------------------------------------------------------


def test_open_problems_clean_fixture(survey_run_dir):
    """The fixture's Open Problems section has 2 subsections, each with the
    full 4-bucket structure."""
    sections = _read_sections(survey_run_dir)
    info = aw.audit_open_problems(sections)
    assert info["present"] is True
    assert info["subsections_total"] == 2
    assert info["subsections_passing"] == 2
    assert info["score"] == 1.0


def test_open_problems_missing_bucket_lowers_score(survey_run_dir):
    """Removing one bucket from one subsection drops the score below 1."""
    op = survey_run_dir / "5_paper" / "sections" / "03_open_problems.tex"
    text = op.read_text().replace("% [LIMITATIONS]", "% [REMOVED]", 1)
    op.write_text(text)

    sections = _read_sections(survey_run_dir)
    info = aw.audit_open_problems(sections)
    assert info["subsections_passing"] == 1   # only one of the two passes
    assert info["score"] < 1.0


def test_open_problems_absent_section_is_neutral(tmp_path):
    """No Open Problems section at all → not_applicable, score=1.0."""
    (tmp_path / "5_paper" / "sections").mkdir(parents=True)
    (tmp_path / "5_paper" / "sections" / "01_intro.tex").write_text(
        r"\section{Intro}\nA brief and self-contained narrative.\n"
    )
    sections = aw._read_section_files(tmp_path / "5_paper" / "sections")
    info = aw.audit_open_problems(sections)
    assert info["present"] is False
    assert info["score"] == 1.0


# ---------------------------------------------------------------------------
# 3. narrative_pillars (Hook + Why-Now + Relationship + Contributions)
# ---------------------------------------------------------------------------


def test_narrative_pillars_clean_fixture(survey_run_dir):
    """Fixture intro carries all 4 pillars."""
    sections = _read_sections(survey_run_dir)
    info = aw.audit_narrative_pillars(sections)
    pillars = info["pillars"]
    assert pillars["hook"] is True
    assert pillars["why_now"] is True
    assert pillars["relationship"] is True
    assert pillars["contributions"] is True
    assert info["contrib_count"] >= 4
    assert info["score"] == 1.0


def test_narrative_pillars_strip_why_now(survey_run_dir):
    """Removing the Why-Now subsection drops pillars score to 0.75."""
    intro = survey_run_dir / "5_paper" / "sections" / "01_introduction.tex"
    text = intro.read_text().replace(r"\subsection*{Why Now?}",
                                      r"\subsection*{Random Aside}")
    intro.write_text(text)

    sections = _read_sections(survey_run_dir)
    info = aw.audit_narrative_pillars(sections)
    assert info["pillars"]["why_now"] is False
    assert info["score"] == 0.75


def test_detect_numbered_contributions_enumerate_block():
    """Style 1: classic \\begin{enumerate} ... ≥4 \\item ... \\end{enumerate}."""
    intro = (
        r"\section{Introduction}" "\n"
        r"\begin{enumerate}" "\n"
        r"\item First contribution about A." "\n"
        r"\item Second contribution about B." "\n"
        r"\item Third contribution about C." "\n"
        r"\item Fourth contribution about D." "\n"
        r"\end{enumerate}"
    )
    ok, n = aw._detect_numbered_contributions(intro)
    assert ok is True and n == 4


def test_detect_numbered_contributions_inline_textbf_markers():
    """Style 2: in-prose \\textbf{(1)} … \\textbf{(2)} … sequence."""
    intro = (
        r"\textbf{(1) First shift.} Lorem ipsum dolor." "\n\n"
        r"\textbf{(2) Second shift.} Sit amet consectetur." "\n\n"
        r"\textbf{(3) Third shift.} Adipiscing elit." "\n\n"
        r"\textbf{(4) Fourth shift.} Sed do eiusmod tempor."
    )
    ok, n = aw._detect_numbered_contributions(intro)
    assert ok is True and n == 4


def test_detect_numbered_contributions_inline_five_markers():
    """The real run uses 5 inline markers — must be detected as 5, not 4."""
    intro = "\n\n".join(
        rf"\textbf{{({i}) Shift number {i}.}} Body of contribution."
        for i in range(1, 6)
    )
    ok, n = aw._detect_numbered_contributions(intro)
    assert ok is True and n == 5


def test_detect_numbered_contributions_rejects_gapped_sequence():
    """If markers don't go 1, 2, 3, … in order they aren't a contributions
    list — they might be unrelated bold markers in different paragraphs."""
    intro = (
        r"\textbf{(1) First.} Body."  "\n\n"
        r"\textbf{(3) Third — note skipped 2.} Body."  "\n\n"
        r"\textbf{(4) Fourth.} Body."  "\n\n"
        r"\textbf{(7) Seventh.} Body."
    )
    ok, n = aw._detect_numbered_contributions(intro)
    # Longest 1-prefix is just '1' → run=1 < 4 → not contributions
    assert ok is False
    assert n == 0


def test_detect_numbered_contributions_three_inline_markers_too_few():
    intro = "\n\n".join(
        rf"\textbf{{({i}) Shift.}} Body."
        for i in range(1, 4)
    )
    ok, n = aw._detect_numbered_contributions(intro)
    assert ok is False and n == 0


def test_audit_narrative_pillars_inline_contributions_real_run_shape():
    """End-to-end: an Intro using inline \\textbf{(1)..\\textbf{(5)} markers
    must report contributions=True even though there's no \\begin{enumerate}."""
    sections = {"01_intro": (
        r"\section{Introduction}" "\n"
        r"\label{sec:intro}" "\n\n"
        + "\n\n".join(
            rf"\textbf{{({i}) Shift number {i}.}} Lorem ipsum dolor sit amet."
            for i in range(1, 6))
    )}
    info = aw.audit_narrative_pillars(sections)
    assert info["pillars"]["contributions"] is True
    assert info["contrib_count"] == 5


def test_narrative_pillars_finds_real_first_paragraph(tmp_path):
    """Regression test for pre-fix, audit_narrative_pillars
    matched only '\\section\\b' (not the closing brace), so the very
    first 'paragraph' was the closing-brace residue 'Introduction}\\n
    \\label{...}'. AND it filtered chunks via 'startswith(\"\\\\\")',
    dropping every paragraph that begins with \\subsection / \\textbf
    / \\emph. Together: hook detection always reported 'metaphor=False'
    even on intros that began with a clear 'from X to Y' phrase.
    """
    # Realistic Intro shape: section, label, subsection header, then
    # a true hook paragraph beginning with \emph. Pre-fix, the chunk
    # picked up was 'Introduction}\\n\\label{sec:intro}' — vacuous.
    sections = {"01_intro": (
        r"\section{Introduction}"        "\n"
        r"\label{sec:intro}"             "\n\n"
        r"\subsection{The 2024 Inflection}" "\n\n"
        r"\emph{Pretraining} moved from BF16 to FP8 across the open"
        r" frontier in 2024, with 65\% of new releases shipping"
        r" mixed-precision training."  "\n"
    )}
    info = aw.audit_narrative_pillars(sections)
    # The hook pillar requires year + number + metaphor; this fixture
    # satisfies all three (2024, 65%, 'from X to Y').
    assert info["pillars"]["hook"] is True, info


# ---------------------------------------------------------------------------
# 4. thesis_coherence
# ---------------------------------------------------------------------------


def test_thesis_coherence_clean_fixture(survey_run_dir):
    """All argument_steps S1..S4 are bound to ≥1 outline section in the
    fixture, and abstract+conclusion both restate the thesis."""
    sections = _read_sections(survey_run_dir)
    thesis_doc = json.loads(
        (survey_run_dir / "2_thesis" / "thesis.json").read_text())
    outline_doc = json.loads(
        (survey_run_dir / "4_outline" / "outline.json").read_text())

    info = aw.audit_thesis_coherence(sections, thesis_doc, outline_doc)
    assert info["applicable"] is True
    assert info["score"] == 1.0
    assert info["argument_step_coverage"] == "4/4"
    assert info["abstract_overlap_words"] >= 3
    assert info["conclusion_overlap_words"] >= 3


def test_thesis_coherence_uncovered_step(survey_run_dir):
    """Adding a phantom S99 step that no outline section binds → coverage gap."""
    p = survey_run_dir / "2_thesis" / "thesis.json"
    doc = json.loads(p.read_text())
    doc["argument_steps"].append({"step_id": "S99",
                                    "claim": "phantom unbound step"})
    p.write_text(json.dumps(doc, indent=2))

    sections = _read_sections(survey_run_dir)
    outline_doc = json.loads(
        (survey_run_dir / "4_outline" / "outline.json").read_text())
    info = aw.audit_thesis_coherence(sections, doc, outline_doc)
    assert info["score"] < 1.0
    assert any("S99" in i for i in info["issues"])


# ---------------------------------------------------------------------------
# 5. claim_grounding (numeric+cite sentences must overlap with claims/abstracts)
# ---------------------------------------------------------------------------


def test_claim_grounding_passes_for_grounded_sentence(survey_run_dir):
    """The body section's "70B / 1.4 trillion tokens" sentence cites
    chinchilla and the abstract carries the same numbers, so it should ground.
    """
    sections = _read_sections(survey_run_dir)
    filtered = [json.loads(l) for l in
                (survey_run_dir / "1_search" / "filtered.jsonl")
                .read_text().splitlines() if l.strip()]
    claims = [json.loads(l) for l in
              (survey_run_dir / "1_search" / "claims_cache.jsonl")
              .read_text().splitlines() if l.strip()]

    info = aw.audit_claim_grounding(sections, filtered, claims)
    assert info["numeric_cited_sentences"] >= 1
    # In the fixture, the body sentence about "70B parameters trained on 1.4
    # trillion tokens" should overlap the chinchilla atomic claim quote
    assert info["grounded"] >= 1
    assert info["score"] >= 0.5


# ---------------------------------------------------------------------------
# 5a. Grounding-overlap helpers
#
# The grounding match uses three layered tests so it tolerates LaTeX
# escapes and numeric formatting differences without producing false
# positives:
#   * stripped LaTeX + stop-words + ≥4 content-token overlap
#   * 2 fuzzy numeric matches alone (e.g. '15T' ↔ '15.6t')
#   * 1 fuzzy numeric + 2 content tokens
# ---------------------------------------------------------------------------


def test_ground_content_tokens_strips_latex_and_stopwords():
    s = (r"Llama-3-8B \citep{team2024llama} was trained on roughly 15T "
         r"tokens, $\sim$\,90$\times$ Chinchilla-optimal")
    toks = aw._ground_content_tokens(s)
    # Stop-words gone:
    assert "the" not in toks and "was" not in toks and "for" not in toks
    # LaTeX command stripped:
    assert "citep" not in toks and "sim" not in toks and "times" not in toks
    # Real content kept:
    assert "llama" in toks and "trained" in toks and "tokens" in toks
    assert "chinchilla" in toks and "optimal" in toks


def test_ground_numeric_tokens_keeps_units():
    s = "trained on 15.6T tokens with a 16K H100 cluster and 65% accuracy"
    nums = aw._ground_numeric_tokens(s)
    # Lower-cased and with unit suffix glued (the regex captures e.g. '15.6t')
    assert "15.6t" in nums
    assert "16k" in nums
    assert "65" in nums  # bare number — '%' is its own char, not an alpha suffix


def test_numeric_fuzzy_overlap_matches_within_30_percent_same_unit():
    # 15T body claim vs 15.6T abstract — same unit 't', within 4% → match
    assert aw._ground_numeric_fuzzy_overlap({"15t"}, {"15.6t"}) == 1
    # different unit → no match
    assert aw._ground_numeric_fuzzy_overlap({"15t"}, {"15b"}) == 0
    # >30% drift → no match
    assert aw._ground_numeric_fuzzy_overlap({"10t"}, {"20t"}) == 0
    # unit-less small numbers (1, 2, 3) must NOT match — too noisy
    assert aw._ground_numeric_fuzzy_overlap({"3"}, {"3"}) == 0
    # unit-less large numbers can match
    assert aw._ground_numeric_fuzzy_overlap({"100"}, {"100"}) == 1


def test_is_grounded_recognises_chinchilla_scale_match():
    """Regression: a naive word-overlap algorithm scored this real-world
    case as ungrounded — body says '15T tokens' for Llama-3-8B, abstract
    says '15.6t tokens', giving only 3 word overlaps under a threshold of
    6. The current algorithm uses fuzzy numeric matching, so 8b ↔ 8b and
    15t ↔ 15.6t both register and the sentence is grounded."""
    sentence = (r"Llama-3-8B \citep{team2024llama} was trained on roughly "
                r"15T tokens, $\sim$\,90$\times$ Chinchilla-optimal")
    abstracts = {"team2024llama": (
        "llama 3 (8b / 70b / 405b) is trained on 15.6t tokens with a "
        "custom scaling-law-driven mixture, gqa, 128k vocabulary, and "
        "a multi-stage pretraining schedule"
    )}
    assert aw._is_grounded(sentence, ["team2024llama"], abstracts, {}) is True


def test_is_grounded_rejects_unrelated_citation():
    """Negative control: if the cited paper's abstract has nothing to do
    with the sentence, the sentence is still ungrounded."""
    sentence = "the cat sat on the mat for 90% of the day \\citep{x2024}"
    abstracts = {"x2024": "this paper introduces a new optimiser based on "
                 "second-order moments of gradient updates"}
    assert aw._is_grounded(sentence, ["x2024"], abstracts, {}) is False


def test_is_grounded_uses_atomic_claim_quotes_when_available():
    """Even with a sparse abstract, atomic-claim quotes should provide
    enough overlap to ground a citing sentence."""
    sentence = (r"Chinchilla-optimal pretraining requires roughly 20 tokens "
                r"per parameter \citep{hoffmann2022}")
    abstracts = {"hoffmann2022": ""}  # missing
    quote_pool = {"hoffmann2022": (
        "compute-optimal training requires the number of training tokens "
        "to be roughly 20 times the number of model parameters"
    )}
    assert aw._is_grounded(sentence, ["hoffmann2022"], abstracts, quote_pool) is True


# ---------------------------------------------------------------------------
# 5c. Cards-as-grounding-source
#
# Bug observed: 70% of ungrounded sentences in real runs cited papers whose
# filtered.jsonl carries an empty abstract. The 1_search/cards/<key>.md
# files contain rich design-rationale + SOTA + lessons text — exactly the
# corpus a human reviewer would consult to verify a numeric claim. Loading
# those files into the grounding pool boosted real-run grounding from 0.57
# to 0.95 with no observed false-positives.
# ---------------------------------------------------------------------------


def test_load_card_text_strips_markdown_formatting(tmp_path):
    cards = tmp_path / "cards"
    cards.mkdir()
    (cards / "abc2024foo.md").write_text(
        "# Title\n\n"
        "- **cite_key:** `abc2024foo`\n"
        "- **year:** 2024\n\n"
        "## Insights\n"
        "- design_rationale: Trains 70B on 1.4T tokens at compute optimum.\n"
        "- sota_claim: Compute-optimal frontier 2022\n",
        encoding="utf-8",
    )
    out = aw._load_card_text(cards)
    assert "abc2024foo" in out
    body = out["abc2024foo"]
    # Markdown bullets / **bold** / `backticks` stripped:
    assert "**" not in body and "`" not in body
    # Substantive text survives:
    assert "design_rationale" in body
    assert "1.4t tokens" in body or "1.4t" in body


def test_load_card_text_returns_empty_when_dir_missing(tmp_path):
    assert aw._load_card_text(tmp_path / "nope") == {}


def test_audit_claim_grounding_uses_cards_when_abstracts_empty():
    """Real-run regression: if a cite_key has no abstract in filtered.jsonl
    but its card body covers the claim, the sentence must still ground."""
    sections = {"02_arch": (
        r"Multi-query attention \citep{shazeer2019transformer} "
        r"shares a single KV head across all 8 query heads, "
        r"cutting KV-cache size by a factor of h."
    )}
    filtered = [{"cite_key": "shazeer2019transformer", "abstract": ""}]
    cards = {"shazeer2019transformer": (
        "fast transformer decoding one write-head is all you need "
        "shazeer 2019 method introduces multi-query attention which "
        "shares a single key-value head across all 8 query heads "
        "yielding faster decoding with negligible quality loss"
    )}
    info = aw.audit_claim_grounding(sections, filtered, [], cards=cards)
    assert info["numeric_cited_sentences"] == 1
    assert info["grounded"] == 1
    assert info["score"] == 1.0


def test_audit_claim_grounding_cards_arg_is_optional():
    """Backward-compat: the `cards` keyword argument is optional; callers
    that don't have a cards directory (and the e2e suite) must keep
    working with positional / keyword-less invocations."""
    sections = {"02_arch": (
        r"Trained on 15T tokens \citep{x} with 8B params."
    )}
    filtered = [{"cite_key": "x", "abstract": "trained on 15.6t tokens with 8b params"}]
    info = aw.audit_claim_grounding(sections, filtered, [])
    assert info["score"] == 1.0


# ---------------------------------------------------------------------------
# 5d. _extract_sentences — sectioning-command splitting
#
# Bug observed on real run: '\section{Open Problems}\n\label{sec:open}\n\n
# We close with four open problems...' was returned as ONE sentence because
# the splitter only fired on '.!?'. Section headers carry no terminator, so
# the next prose paragraph silently merged with them. Downstream the
# grounding audit then reported the merged sentence as 'ungrounded' and
# leaked '\subsection{...}' prefixes into the report.
# ---------------------------------------------------------------------------


def test_extract_sentences_splits_at_section_command():
    text = (
        r"\section{Open Problems}"  "\n"
        r"\label{sec:open}"         "\n\n"
        r"We close with four open problems that define 2026."
    )
    sents = aw._extract_sentences(text)
    # The prose sentence must be standalone — no \section/\label residue
    prose = [s for s in sents if "open problems that define" in s]
    assert prose, sents
    assert "\\section" not in prose[0]
    assert "\\label" not in prose[0]


def test_extract_sentences_splits_at_subsection_command():
    text = (
        r"Each is grounded in a tension visible in our corpus."  "\n\n"
        r"\subsection{Data exhaustion}"  "\n\n"
        r"Muennighoff et al.\ \citep{m2023} estimate web-scale tokens "
        r"exhausted at 15--40T."
    )
    sents = aw._extract_sentences(text)
    # Both prose sentences are recovered separately
    has_first = any("Each is grounded" in s for s in sents)
    has_second = any("Muennighoff" in s and "\\subsection" not in s for s in sents)
    assert has_first and has_second, sents


def test_extract_sentences_splits_at_input_and_includegraphics():
    """Figure-include lines also lack a terminator and must not glue onto
    surrounding prose."""
    text = (
        r"Section text ends here without a period"  "\n"
        r"\input{figures/tables/foo.tex}"  "\n"
        r"Next sentence starts cleanly."
    )
    sents = aw._extract_sentences(text)
    assert any("Next sentence starts cleanly" in s for s in sents)
    # The \input directive must not survive in any returned sentence
    assert all("\\input{" not in s for s in sents)


def test_audit_claim_grounding_no_longer_emits_subsection_residue():
    """Real-run regression: the report should never list an 'ungrounded'
    sentence that begins with '\\subsection{...}' — that's metadata, not
    prose."""
    sections = {"12_open_problems": (
        r"\section{Open Problems}"  "\n"
        r"\label{sec:open}"         "\n\n"
        r"\subsection{Data exhaustion}"  "\n\n"
        r"Muennighoff et al.\ \citep{m2023} estimate exhaustion at 15--40T."
    )}
    filtered = [{"cite_key": "m2023",
                 "abstract": "completely unrelated abstract about cats"}]
    info = aw.audit_claim_grounding(sections, filtered, [])
    # The sentence is ungrounded (abstract about cats), but the example
    # we record must be the clean prose sentence — not the merged blob.
    assert info["numeric_cited_sentences"] == 1
    assert info["score"] < 1.0
    for ex in info["ungrounded_examples"]:
        assert "\\subsection{" not in ex
        assert "\\section{" not in ex
        assert "\\label{" not in ex


# ---------------------------------------------------------------------------
# 5b. _has_quantitative_numeric — false-positive guards
#
# Bug fixed in enumerator markers like "(1) Tokens-per-parameter",
# model-name fragments like "Llama-3-8B" / "Qwen3", and version codes like
# "DeepSeek-V3" used to be counted as quantitative numeric claims, dragging
# the grounding score below 0.5 even on well-cited drafts. The fix strips
# those artefacts before the numeric check; real quantitative claims
# ("15T tokens", "65% accuracy", "90×") remain detected.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("sentence", [
    r"\textbf{(1) Tokens-per-parameter has roughly tripled.} \citep{a2024}",
    r"(2) The next axis is data quality \citep{b2024}.",
    r"Recent releases include Qwen3 and DeepSeek-V3 \citep{c2024,d2024}.",
    r"Llama-3-8B is a strong baseline \citep{e2024}.",
    r"GPT-4 sets the bar \citep{f2023}.",
    # year-only sentences
    r"By 2025 every flagship release ships an MoE \citep{a2024}.",
    r"In 2024, the field had stabilised \citep{b2024}.",
    r"Between 2020 and 2025 the corpus tripled \citep{c2024}.",
    # name-then-release-ordinal
    r"OLMo 2 systematised the WSD pattern \citep{olmo2024olmo}.",
    r"GPT 4 outperforms GPT 3 on MMLU \citep{openai2023gpt4}.",
])
def test_has_quantitative_numeric_rejects_non_quantitative(sentence):
    assert aw._has_quantitative_numeric(sentence) is False, sentence


@pytest.mark.parametrize("sentence", [
    r"trained on 15T tokens \citep{a2024}",
    r"achieves 65% accuracy on MMLU \citep{b2024}",
    r"a 90$\times$ speedup over the baseline \citep{c2024}",
    r"with 7B parameters and 2T tokens \citep{d2024}",
    r"runs at 100GB/s on the cluster \citep{e2024}",
    # real claim that *also* contains a year or release ordinal —
    # the strippers must not hide the genuine numeric content
    r"In 2025, Llama-3 reached 65% accuracy on MMLU \citep{f2025}",
    r"OLMo 2 was trained on 6T tokens \citep{olmo2024olmo}",
])
def test_has_quantitative_numeric_accepts_real_claims(sentence):
    assert aw._has_quantitative_numeric(sentence) is True, sentence


def test_audit_claim_grounding_ignores_enumerator_only_sentences(tmp_path):
    """A section whose only 'numeric' content is enumerator markers like
    '(1)', '(2)', '(3)' — together with citations — must NOT contribute
    to the numeric_cited_sentences denominator."""
    sections = {"03_body": (
        r"\subsection{Five shifts}"
        "\n"
        r"\textbf{(1) Tokens-per-parameter has roughly tripled.} "
        r"This builds on \citep{chinchilla2022}."
        "\n"
        r"\textbf{(2) Mixture-of-experts has become the default.} "
        r"See \citep{mixtral2024,deepseek2024}."
    )}
    info = aw.audit_claim_grounding(sections, filtered=[], claims=[])
    assert info["numeric_cited_sentences"] == 0
    assert info["score"] == 1.0  # vacuously perfect — no claims to ground


# ---------------------------------------------------------------------------
# CLI: submission-gate exit codes
# ---------------------------------------------------------------------------


def _run_cli(run_dir: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    """Run audit_writing.py against the in-tree minimal fixture.

    The fixture is a 4-section paper that cannot satisfy the 8
    structural invariants by construction (no cross-cutting matrix,
    no annotated bibliography, etc.). Pass --no-strict-template so
    the structural_template gate does not mask the narrative /
    argument / claim signals these tests are actually exercising.
    Real /survey-run invocations MUST NOT pass this flag.
    """
    return subprocess.run(
        [sys.executable, str(ROOT / "tools" / "audit_writing.py"),
         str(run_dir), "--no-strict-template", *extra],
        capture_output=True, text=True,
    )


def test_cli_submission_gate_passes_clean_fixture(survey_run_dir):
    res = _run_cli(survey_run_dir, "--assurance", "submission")
    assert res.returncode == 0, (
        f"submission gate must pass clean fixture, got rc={res.returncode}\n"
        f"stdout:\n{res.stdout}"
    )


def test_cli_submission_gate_fails_on_broken_pillars(survey_run_dir):
    """Strip 3 of 4 pillars → narrative score 0.25 < 0.9 → submission FAIL."""
    intro = survey_run_dir / "5_paper" / "sections" / "01_introduction.tex"
    text = intro.read_text()
    # Strip Why-Now and Relationship
    text = text.replace(r"\subsection*{Why Now?}", "")
    text = text.replace(r"\subsection*{Relationship to Existing Surveys}", "")
    intro.write_text(text)

    res = _run_cli(survey_run_dir, "--assurance", "submission")
    assert res.returncode == 1
    assert "Submission gate FAIL" in res.stdout
    assert "narrative_pillars" in res.stdout


def test_cli_polished_does_not_block(survey_run_dir):
    """At assurance=polished, even a broken paper does not block."""
    body = survey_run_dir / "5_paper" / "sections" / "02_body.tex"
    body.write_text(r"\section{Body}Nothing useful here.")

    res = _run_cli(survey_run_dir, "--assurance", "polished")
    assert res.returncode == 0
    assert "gates not enforced" in res.stdout


def test_cli_submission_gate_includes_structural_template(survey_run_dir):
    """Without --no-strict-template, the survey-run fixture (which fails 8/8
    structural invariants by construction) must trip the submission
    gate on `structural_template`. This is the contract that closes
    the loop on the benchmark-derived structural invariants — without
    this gate, an 8/8-failing paper could still ship.
    """
    res = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "audit_writing.py"),
         str(survey_run_dir), "--assurance", "submission"],
        capture_output=True, text=True,
    )
    assert res.returncode == 1, (
        f"submission gate must FAIL when strict-template is on, got "
        f"rc={res.returncode}\nstdout:\n{res.stdout}"
    )
    assert "structural_template" in res.stdout
    assert "Submission gate FAIL" in res.stdout


# ---------------------------------------------------------------------------
# Threshold loading from benchmark-targets.json (SSOT calibration)
# ---------------------------------------------------------------------------


def test_module_defaults_match_repo_benchmark_targets():
    """Drift defence: every module-level threshold default must equal
    the same key in shared-references/benchmark-targets.json. If you
    change one without changing the other, this test catches it."""
    import json as _json
    targets_path = (ROOT / "skills" / "shared-references"
                    / "benchmark-targets.json")
    targets = _json.loads(targets_path.read_text())
    audit_th = targets["audit_thresholds"]
    for json_key, const_name in aw._THRESHOLD_KEYS:
        assert audit_th[json_key] == getattr(aw, const_name), (
            f"drift: benchmark-targets.json[{json_key}] "
            f"({audit_th[json_key]}) != audit_writing.{const_name} "
            f"({getattr(aw, const_name)})"
        )


def test_load_audit_thresholds_from_json_overrides_constants(tmp_path,
                                                              monkeypatch):
    """A custom benchmark-targets.json must overwrite the module-level
    constants, and a subsequent call to audit_structural_template must
    use the new threshold."""
    custom = tmp_path / "custom.json"
    custom.write_text(json.dumps({
        "audit_thresholds": {
            "citation_density_cap":  5.0,    # tighter than default 12.0
            "conclusion_min_words":  100,
            "conclusion_max_words":  200,
        }
    }))
    # Snapshot the originals so we can restore them after the test.
    original = {name: getattr(aw, name) for _, name in aw._THRESHOLD_KEYS}
    try:
        applied = aw._load_audit_thresholds_from_json(custom)
        assert applied is not None
        assert aw.CITATION_DENSITY_CAP == 5.0
        assert aw.CONCLUSION_MIN_WORDS == 100
        assert aw.CONCLUSION_MAX_WORDS == 200
        # Untouched keys keep their original values.
        assert aw.SENTENCE_CITATION_CAP == original["SENTENCE_CITATION_CAP"]
    finally:
        for name, value in original.items():
            setattr(aw, name, value)


def test_load_audit_thresholds_from_json_missing_file_returns_none(tmp_path):
    assert aw._load_audit_thresholds_from_json(tmp_path / "nope.json") is None


def test_load_audit_thresholds_from_json_malformed_returns_none(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{not json")
    assert aw._load_audit_thresholds_from_json(p) is None


def test_load_audit_thresholds_from_json_no_section_returns_none(tmp_path):
    p = tmp_path / "no-section.json"
    p.write_text(json.dumps({"benchmark": {"pages": 45}}))
    assert aw._load_audit_thresholds_from_json(p) is None


def test_cli_benchmark_targets_flag_overrides_thresholds(survey_run_dir, tmp_path):
    """--benchmark-targets <path> must propagate the custom thresholds
    into the audit gate. We construct a 'tight' targets file with a
    citation_density_cap so low (1.0/1Kw) that the survey-run fixture trips
    it on the citation_density invariant alone."""
    custom = tmp_path / "tight.json"
    custom.write_text(json.dumps({
        "audit_thresholds": {
            "citation_density_cap": 1.0,
        }
    }))
    res = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "audit_writing.py"),
         str(survey_run_dir), "--no-strict-template",
         "--assurance", "polished",
         "--benchmark-targets", str(custom),
         "--report", str(tmp_path / "report.json")],
        capture_output=True, text=True,
    )
    assert res.returncode == 0  # polished mode never blocks
    report = json.loads((tmp_path / "report.json").read_text())
    cd = report["structural_template"]["invariants"]["citation_density"]
    assert cd["ok"] is False
    assert "exceeds cap 1.0" in cd["issue"]


# ---------------------------------------------------------------------------
# 6. insight_anchors — non_obvious_findings ↔ % [INSIGHT] traceability
# ---------------------------------------------------------------------------


def test_insight_anchors_not_applicable_when_thesis_lacks_findings(survey_run_dir):
    """The default fixture has no non_obvious_findings field → audit
    must report applicable=False and score=1.0 (nothing to enforce)."""
    sections = _read_sections(survey_run_dir)
    thesis = json.loads(
        (survey_run_dir / "2_thesis" / "thesis.json").read_text())
    info = aw.audit_insight_anchors(sections, thesis)
    assert info["applicable"] is False
    assert info["score"] == 1.0
    assert info["findings_total"] == 0


def test_insight_anchors_passes_when_anchor_present(survey_run_dir):
    """Add a non_obvious_findings entry pointing at 02_body, then
    insert an `% [INSIGHT]` anchor in that section's .tex → score 1.0."""
    thesis_path = survey_run_dir / "2_thesis" / "thesis.json"
    thesis = json.loads(thesis_path.read_text())
    thesis["non_obvious_findings"] = [{
        "finding": "Token budget matters more than parameter count.",
        "section_id": "02_body",
    }]
    thesis_path.write_text(json.dumps(thesis, indent=2))

    body = survey_run_dir / "5_paper" / "sections" / "02_body.tex"
    body.write_text(body.read_text() + "\n% [INSIGHT] token-budget-dominates\n")

    sections = _read_sections(survey_run_dir)
    info = aw.audit_insight_anchors(sections, thesis)
    assert info["applicable"] is True
    assert info["score"] == 1.0
    assert info["findings_anchored"] == 1
    assert info["issues"] == []


def test_insight_anchors_flags_missing_anchor(survey_run_dir):
    """Declare a finding but never add the anchor → score 0 + actionable
    issue listing the section_id and a snippet of the finding text."""
    thesis_path = survey_run_dir / "2_thesis" / "thesis.json"
    thesis = json.loads(thesis_path.read_text())
    thesis["non_obvious_findings"] = [{
        "finding": "Token budget matters more than parameter count above 1e23 FLOPs.",
        "section_id": "02_body",
    }]
    thesis_path.write_text(json.dumps(thesis, indent=2))

    sections = _read_sections(survey_run_dir)
    info = aw.audit_insight_anchors(sections, thesis)
    assert info["applicable"] is True
    assert info["findings_total"] == 1
    assert info["findings_anchored"] == 0
    assert info["score"] == 0.0
    assert any("[INSIGHT]" in m for m in info["issues"])
    assert any("02_body" in m for m in info["issues"])


def test_insight_anchors_flags_unknown_section_id(survey_run_dir):
    """A finding referencing a section that doesn't exist must surface
    a clear error rather than silently passing."""
    thesis_path = survey_run_dir / "2_thesis" / "thesis.json"
    thesis = json.loads(thesis_path.read_text())
    thesis["non_obvious_findings"] = [{
        "finding": "x", "section_id": "99_nowhere",
    }]
    thesis_path.write_text(json.dumps(thesis, indent=2))

    sections = _read_sections(survey_run_dir)
    info = aw.audit_insight_anchors(sections, thesis)
    assert any("99_nowhere" in m and "not found" in m for m in info["issues"])


def test_insight_anchors_partial_score_when_one_of_two_anchored(survey_run_dir):
    """Two findings, one anchored → score = 0.5."""
    thesis_path = survey_run_dir / "2_thesis" / "thesis.json"
    thesis = json.loads(thesis_path.read_text())
    thesis["non_obvious_findings"] = [
        {"finding": "F1.", "section_id": "02_body"},
        {"finding": "F2.", "section_id": "03_open_problems"},
    ]
    thesis_path.write_text(json.dumps(thesis, indent=2))

    # Only anchor the first one.
    body = survey_run_dir / "5_paper" / "sections" / "02_body.tex"
    body.write_text(body.read_text() + "\n% [INSIGHT] f1\n")

    sections = _read_sections(survey_run_dir)
    info = aw.audit_insight_anchors(sections, thesis)
    assert info["findings_anchored"] == 1
    assert info["findings_total"] == 2
    assert info["score"] == 0.5


def test_insight_anchors_malformed_entries_are_flagged_per_index(survey_run_dir):
    """Missing finding-text or missing section_id must be flagged
    with the array index so the agent can fix the right entry."""
    thesis_path = survey_run_dir / "2_thesis" / "thesis.json"
    thesis = json.loads(thesis_path.read_text())
    thesis["non_obvious_findings"] = [
        {"section_id": "02_body"},                # missing finding
        {"finding": "F"},                            # missing section_id
        "not-an-object",                             # wrong type entirely
    ]
    thesis_path.write_text(json.dumps(thesis, indent=2))

    sections = _read_sections(survey_run_dir)
    info = aw.audit_insight_anchors(sections, thesis)
    issues = " ".join(info["issues"])
    assert "[0].finding" in issues
    assert "[1].section_id" in issues
    assert "[2]" in issues  # wrong-type entry


def test_cli_submission_gate_blocks_on_unanchored_finding(survey_run_dir):
    """End-to-end: a thesis with non_obvious_findings but no matching
    INSIGHT anchor must trip the submission gate."""
    thesis_path = survey_run_dir / "2_thesis" / "thesis.json"
    thesis = json.loads(thesis_path.read_text())
    thesis["non_obvious_findings"] = [{
        "finding": "Token budget dominates parameter count.",
        "section_id": "02_body",
    }]
    thesis_path.write_text(json.dumps(thesis, indent=2))

    res = _run_cli(survey_run_dir, "--assurance", "submission")
    assert res.returncode == 1
    assert "Submission gate FAIL" in res.stdout
    assert "insight_anchors" in res.stdout


# ---------------------------------------------------------------------------
# 7. structural_template (benchmark-derived invariants;
#    see shared-references/structural-template.md and benchmark-targets.json)
# ---------------------------------------------------------------------------


def _make_strong_template_run(tmp_path: Path) -> Path:
    """A run dir that satisfies all 8 structural-template invariants.

    Used as the positive fixture; individual negative tests perturb one
    artefact at a time so the failing invariant is unambiguous.
    """
    run_dir = tmp_path / "strong_run"
    sections = run_dir / "5_paper" / "sections"
    sections.mkdir(parents=True)
    (run_dir / "4_outline").mkdir(parents=True)
    bib = run_dir / "5_paper" / "references.bib"

    # Body sentence with exactly 1 \citep — repeating it gives benchmark-grade
    # density (~ 8 cites per ~50 words = 160 / 1Kw raw, but split across many
    # sentences keeps per-sentence cap honoured). For density we need the
    # ratio (cites / body_words) * 1000 ≤ 12. So roughly 1 cite per 80 words.
    body_paragraph = (
        "The dominant pattern is X, established in early work \\citep{smith2024}. "
        "Subsequent extensions explored variants of the same idea, refining "
        "the experimental setup and reporting modest improvements that have "
        "since been replicated across several follow-up studies in the area. "
        "Several authors have argued that the underlying mechanism is more "
        "subtle, and that the apparent gains arise from a combination of "
        "data scale and improved evaluation rather than from any single "
        "algorithmic change. "
    )

    section_specs = [
        ("00_abstract", 0, ""),
        ("01_intro", 4, "Introduction"),
        ("02_background", 4, "Background"),
        ("03_architecture", 3, "Architecture Patterns"),
        ("04_systems", 3, "Key Systems"),
        ("05_eval", 3, "Evaluation"),
        ("06_problems", 0, "Open Problems"),
        ("07_future", 0, "Future Directions"),
        ("08_conclusion", 0, "Conclusion"),
    ]

    contributions_block = (
        "\\begin{enumerate}\n"
        "\\item \\textbf{Comprehensive Taxonomy.} We propose an L1-L5 "
        "hierarchy of autonomy levels (\\S\\,2).\n"
        "\\item \\textbf{Architecture Analysis.} We identify four "
        "patterns with comparative trade-offs (\\S\\,3).\n"
        "\\item \\textbf{System Comparison.} We analyse 17 systems "
        "across six dimensions (\\S\\,4).\n"
        "\\item \\textbf{Open Problems.} We name six challenges and "
        "concrete directions (\\S\\,6).\n"
        "\\end{enumerate}\n"
    )

    for sec_id, n_subs, title in section_specs:
        path = sections / f"{sec_id}.tex"
        if sec_id == "00_abstract":
            path.write_text("\\begin{abstract}\nShort abstract.\n\\end{abstract}\n")
            continue
        chunks = [f"\\section{{{title}}}\n", body_paragraph]
        if sec_id == "01_intro":
            # Append the contributions enumerate block (invariant 8).
            chunks.append(contributions_block)
            for i in range(n_subs):
                chunks += [f"\\subsection{{Sub {i+1}}}\n", body_paragraph]
            path.write_text("\n".join(chunks))
            continue
        if sec_id == "02_background":
            chunks += [
                "\\subsection{Required Capabilities}\n", body_paragraph,
                "\\subsection{Relationship to existing surveys}\n",
                "Wang et al. (2024) and Xi et al. (2023) and "
                "Park and Choi (2024) provide adjacent coverage. ",
                "\\subsection{Adjacent Fields}\n", body_paragraph,
                "\\subsection{Definitions}\n", body_paragraph,
            ]
        elif sec_id == "04_systems":
            chunks += [
                "\\subsection{General-Purpose Agents}\n", body_paragraph,
                "\\subsection{Code-Focused Agents}\n", body_paragraph,
                "\\subsection{Feature Comparison Matrix}\n",
                "\\begin{table}[t]\\centering\n"
                "\\begin{tabular}{lcccccc}\n"
                "System & D1 & D2 & D3 & D4 & D5 & D6 \\\\\n"
                "A & y & n & y & n & y & y \\\\\n"
                "B & y & y & y & n & y & n \\\\\n"
                "\\end{tabular}\\end{table}\n",
            ]
        elif sec_id == "08_conclusion":
            # Re-frame paragraph hand-crafted to land in [400..700] words and
            # use no bullet markers.
            reframe = (
                "This survey re-frames the field around a single axis: the "
                "transition from assistive to autonomous research agents. "
                "Three cross-cutting findings deserve emphasis. First, the "
                "binding constraint is not raw capability but persistent "
                "knowledge accumulation across sessions. Second, the four "
                "architectural patterns we surveyed converge in practice on "
                "hybrid arrangements, suggesting that taxonomy purity is "
                "less important than tool-augmentation discipline. Third, "
                "evaluation infrastructure has matured faster than "
                "benchmarks for novelty, leaving open the question of how to "
                "score genuine intellectual contribution. The thesis would "
                "be invalidated by a single counter-example: a system that "
                "operated for a year without human intervention and "
                "produced a publishable result. We have not yet seen one. "
            )
            chunks = ["\\section{Conclusion}\n", reframe * 4]
        else:
            for i in range(n_subs):
                chunks += [f"\\subsection{{Sub {i+1}}}\n", body_paragraph]
        path.write_text("\n".join(chunks))

    outline = {
        "sections": [
            {"id": "01_intro", "section_type": "intro"},
            {"id": "02_background", "section_type": "background"},
            {"id": "04_systems", "section_type": "body",
             "subsections": [{"section_type": "cross_cutting_matrix"}]},
            {"id": "06_problems", "section_type": "open_problems",
             "items": [{"id": f"OP{i}", "paired_direction_id": f"FD{i}"}
                       for i in range(1, 7)]},
            {"id": "07_future", "section_type": "future_directions",
             "items": [{"id": f"FD{i}"} for i in range(1, 7)]},
        ],
    }
    (run_dir / "4_outline" / "outline.json").write_text(json.dumps(outline))

    bib.write_text(
        "@article{smith2024, title={X}, author={Smith}, year={2024},\n"
        "  annote={Establishes the X result.}}\n\n"
        "@article{wang2024, title={W}, author={Wang}, year={2024},\n"
        "  annote={Adjacent survey of W.}}\n\n"
        "@article{xi2023, title={V}, author={Xi}, year={2023},\n"
        "  annote={Adjacent survey of V.}}\n"
    )
    return run_dir


def test_structural_template_passes_on_well_formed_run(tmp_path):
    run_dir = _make_strong_template_run(tmp_path)
    sections = aw._read_section_files(run_dir / "5_paper" / "sections")
    outline_doc = json.loads(
        (run_dir / "4_outline" / "outline.json").read_text()
    )
    result = aw.audit_structural_template(
        sections, run_dir / "5_paper" / "references.bib", outline_doc,
    )
    failing = {k: v for k, v in result["invariants"].items() if not v["ok"]}
    assert not failing, (
        f"all invariants should pass on the strong fixture; failing: {failing}"
    )
    assert result["score"] == 1.0


def test_structural_template_emits_exactly_eight_invariants(tmp_path):
    """Lock the invariant count: audit emits exactly the 8 invariants
    documented in skills/shared-references/structural-template.md and
    advertised in README.md / AGENT.md. Adding or removing an
    invariant requires updating the structural-template.md doc and the
    README/AGENT.md counts in the same change."""
    run_dir = _make_strong_template_run(tmp_path)
    sections = aw._read_section_files(run_dir / "5_paper" / "sections")
    outline_doc = json.loads(
        (run_dir / "4_outline" / "outline.json").read_text()
    )
    result = aw.audit_structural_template(
        sections, run_dir / "5_paper" / "references.bib", outline_doc,
    )
    expected = {
        "section_nesting",
        "citation_density",
        "annotated_bibliography",
        "cross_cutting_matrix",
        "related_surveys_subsection",
        "open_problems_pairing",
        "conclusion_reframe",
        "contributions_section_refs",
    }
    assert set(result["invariants"]) == expected, (
        f"invariant set drifted from the documented 8.\n"
        f"  added:   {set(result['invariants']) - expected}\n"
        f"  removed: {expected - set(result['invariants'])}"
    )


def test_structural_template_flags_high_citation_density(tmp_path):
    """30 \\citep calls in one short paragraph drive density above the cap."""
    run_dir = _make_strong_template_run(tmp_path)
    bad = run_dir / "5_paper" / "sections" / "01_intro.tex"
    bad.write_text(
        "\\section{intro}\nShort prose. " +
        " ".join([f"\\citep{{ref{i}}}" for i in range(30)])
    )
    sections = aw._read_section_files(run_dir / "5_paper" / "sections")
    outline = json.loads(
        (run_dir / "4_outline" / "outline.json").read_text()
    )
    result = aw.audit_structural_template(
        sections, run_dir / "5_paper" / "references.bib", outline,
    )
    inv = result["invariants"]["citation_density"]
    assert not inv["ok"]
    assert "exceeds cap" in inv["issue"] or "sentence" in inv["issue"]


def test_structural_template_flags_unannotated_bib(tmp_path):
    run_dir = _make_strong_template_run(tmp_path)
    (run_dir / "5_paper" / "references.bib").write_text(
        "@article{x, title={Y}, year={2024}}\n"
    )
    sections = aw._read_section_files(run_dir / "5_paper" / "sections")
    outline = json.loads(
        (run_dir / "4_outline" / "outline.json").read_text()
    )
    result = aw.audit_structural_template(
        sections, run_dir / "5_paper" / "references.bib", outline,
    )
    inv = result["invariants"]["annotated_bibliography"]
    assert not inv["ok"]
    assert "annotated" in inv["issue"]


def test_structural_template_flags_missing_cross_cutting_matrix(tmp_path):
    run_dir = _make_strong_template_run(tmp_path)
    outline_path = run_dir / "4_outline" / "outline.json"
    doc = json.loads(outline_path.read_text())
    for sec in doc["sections"]:
        sec.pop("subsections", None)
    outline_path.write_text(json.dumps(doc))
    sections = aw._read_section_files(run_dir / "5_paper" / "sections")
    result = aw.audit_structural_template(
        sections, run_dir / "5_paper" / "references.bib", doc,
    )
    inv = result["invariants"]["cross_cutting_matrix"]
    assert not inv["ok"]
    assert "cross_cutting_matrix" in inv["issue"]


def test_structural_template_flags_unpaired_open_problems(tmp_path):
    run_dir = _make_strong_template_run(tmp_path)
    outline_path = run_dir / "4_outline" / "outline.json"
    doc = json.loads(outline_path.read_text())
    for sec in doc["sections"]:
        if sec.get("section_type") == "open_problems":
            for item in sec["items"]:
                item.pop("paired_direction_id", None)
    outline_path.write_text(json.dumps(doc))
    sections = aw._read_section_files(run_dir / "5_paper" / "sections")
    result = aw.audit_structural_template(
        sections, run_dir / "5_paper" / "references.bib", doc,
    )
    inv = result["invariants"]["open_problems_pairing"]
    assert not inv["ok"]
    assert "paired" in inv["issue"]


def test_structural_template_flags_missing_relationship_subsection(tmp_path):
    run_dir = _make_strong_template_run(tmp_path)
    bg = run_dir / "5_paper" / "sections" / "02_background.tex"
    bg.write_text(re.sub(
        r"\\subsection\{Relationship[^}]*\}.*?(?=\\subsection|\Z)",
        "",
        bg.read_text(),
        count=1,
        flags=re.DOTALL,
    ))
    sections = aw._read_section_files(run_dir / "5_paper" / "sections")
    outline = json.loads(
        (run_dir / "4_outline" / "outline.json").read_text()
    )
    result = aw.audit_structural_template(
        sections, run_dir / "5_paper" / "references.bib", outline,
    )
    inv = result["invariants"]["related_surveys_subsection"]
    assert not inv["ok"]


def test_structural_template_accepts_paragraph_form_relationship(tmp_path):
    """The benchmark survey uses an inline `\\paragraph{Relationship to
    Existing Surveys.}` block at the end of §1, not a `\\subsection{}`.
    Both forms must satisfy invariant 5."""
    run_dir = _make_strong_template_run(tmp_path)
    bg = run_dir / "5_paper" / "sections" / "02_background.tex"
    # Replace the \subsection variant with a \paragraph variant
    new_text = re.sub(
        r"\\subsection\{Relationship[^}]*\}.*?(?=\\subsection|\Z)",
        ("\\\\paragraph{Relationship to Existing Surveys.} "
         "Wang et al. (2024) survey LLM agents broadly. "
         "Xi et al. (2023) survey general-domain agents. "
         "Park and Choi (2024) cover code-focused systems. "),
        bg.read_text(),
        count=1,
        flags=re.DOTALL,
    )
    bg.write_text(new_text)

    sections = aw._read_section_files(run_dir / "5_paper" / "sections")
    outline = json.loads(
        (run_dir / "4_outline" / "outline.json").read_text()
    )
    result = aw.audit_structural_template(
        sections, run_dir / "5_paper" / "references.bib", outline,
    )
    inv = result["invariants"]["related_surveys_subsection"]
    assert inv["ok"], (
        f"\\paragraph form should satisfy invariant 5; got {inv}"
    )


def test_structural_template_flags_contributions_without_section_refs(tmp_path):
    """Contributions enumeration with no (§N) cross-refs trips inv 8."""
    run_dir = _make_strong_template_run(tmp_path)
    intro = run_dir / "5_paper" / "sections" / "01_intro.tex"
    # Strip the (\S\,N) tail from every contribution.
    text = intro.read_text()
    text = re.sub(r"\(\\S\\,\d+\)\.", ".", text)
    intro.write_text(text)
    sections = aw._read_section_files(run_dir / "5_paper" / "sections")
    outline = json.loads(
        (run_dir / "4_outline" / "outline.json").read_text()
    )
    result = aw.audit_structural_template(
        sections, run_dir / "5_paper" / "references.bib", outline,
    )
    inv = result["invariants"]["contributions_section_refs"]
    assert not inv["ok"]
    assert "0/4" in inv["value"] or "0/" in inv["value"]


def test_structural_template_accepts_75pct_contribution_refs(tmp_path):
    """3/4 items with (§N) is exactly the 75% threshold — must still pass."""
    run_dir = _make_strong_template_run(tmp_path)
    intro = run_dir / "5_paper" / "sections" / "01_intro.tex"
    text = intro.read_text()
    # Strip the (§N) from the *last* item only ⇒ 3/4 = 75%
    new_text = re.sub(
        r"(.*)\(\\S\\,6\)\.",
        r"\1.",
        text,
        count=1,
        flags=re.DOTALL,
    )
    assert new_text != text, "fixture-prep regex must match"
    intro.write_text(new_text)

    sections = aw._read_section_files(run_dir / "5_paper" / "sections")
    outline = json.loads(
        (run_dir / "4_outline" / "outline.json").read_text()
    )
    result = aw.audit_structural_template(
        sections, run_dir / "5_paper" / "references.bib", outline,
    )
    inv = result["invariants"]["contributions_section_refs"]
    assert inv["ok"], inv


def test_structural_template_accepts_alternate_section_ref_styles(tmp_path):
    """The audit must accept (§N), (Section N), and (\\S\\,N) interchangeably."""
    run_dir = _make_strong_template_run(tmp_path)
    intro = run_dir / "5_paper" / "sections" / "01_intro.tex"
    intro.write_text(
        "\\section{Introduction}\n"
        "Survey makes four contributions:\n"
        "\\begin{enumerate}\n"
        "\\item \\textbf{Lead 1.} Body (§2).\n"
        "\\item \\textbf{Lead 2.} Body (Section 3).\n"
        "\\item \\textbf{Lead 3.} Body (\\S\\,4).\n"
        "\\item \\textbf{Lead 4.} Body (Sec.\\ 6).\n"
        "\\end{enumerate}\n"
    )
    sections = aw._read_section_files(run_dir / "5_paper" / "sections")
    outline = json.loads(
        (run_dir / "4_outline" / "outline.json").read_text()
    )
    result = aw.audit_structural_template(
        sections, run_dir / "5_paper" / "references.bib", outline,
    )
    inv = result["invariants"]["contributions_section_refs"]
    assert inv["ok"], inv
    assert "4/4" in inv["value"]


def test_structural_template_reproduces_benchmark_gap_on_baseline_shape(tmp_path):
    """Smoke-test: a run shaped like the LLM-pretraining baseline (flat 13
    top-level sections, no subsections, unannotated bib, no matrix) trips
    every invariant — this is the regression bar for the benchmark gap."""
    run_dir = tmp_path / "flat_run"
    sections = run_dir / "5_paper" / "sections"
    sections.mkdir(parents=True)
    (run_dir / "4_outline").mkdir(parents=True)

    # 13 top-level sections, none with subsections, all flat.
    for i in range(13):
        (sections / f"{i:02d}_section.tex").write_text(
            f"\\section{{Section {i}}}\nProse without subsections. "
            f"\\citep{{a{i}}} \\citep{{b{i}}} \\citep{{c{i}}} \\citep{{d{i}}} "
            f"\\citep{{e{i}}}\n"
        )
    (sections / "13_conclusion.tex").write_text(
        "\\section{Conclusion}\nA conclusion that is way too long: "
        + "we summarise each section in turn, repeating the same claims. " * 80
    )

    (run_dir / "5_paper" / "references.bib").write_text(
        "@article{a0, title={X}, year={2024}}\n"
    )
    (run_dir / "4_outline" / "outline.json").write_text(json.dumps({
        "sections": []  # no open_problems / future_directions / matrix slot
    }))

    secs = aw._read_section_files(sections)
    outline = json.loads(
        (run_dir / "4_outline" / "outline.json").read_text()
    )
    result = aw.audit_structural_template(
        secs, run_dir / "5_paper" / "references.bib", outline,
    )
    failing = {k: v for k, v in result["invariants"].items() if not v["ok"]}
    # Every invariant should fail on this shape — that's the regression bar.
    assert len(failing) == result["total"], (
        f"expected all {result['total']} invariants to fail; passing: "
        f"{[k for k,v in result['invariants'].items() if v['ok']]}"
    )


# ---------------------------------------------------------------------------
# Auxiliary section-files (\subsection-only fragments) and section_nesting
# ---------------------------------------------------------------------------


def test_section_nesting_counts_explicit_section_commands(tmp_path):
    """An auxiliary .tex fragment that emits only \\subsection (e.g. an
    evaluation-methodology block \\input{}d by another section) must NOT
    count as a top-level section. Inv 1 looks for explicit \\section
    commands; if any are present, the file count fallback is suppressed."""
    run_dir = tmp_path / "aux_run"
    sections = run_dir / "5_paper" / "sections"
    sections.mkdir(parents=True)
    (run_dir / "4_outline").mkdir(parents=True)

    # 8 normal section files, each with \section + 3 \subsection.
    for i in range(8):
        (sections / f"{i:02d}_chapter.tex").write_text(
            f"\\section{{Chapter {i}}}\n"
            f"\\subsection{{Sub A}}\n\\subsection{{Sub B}}\n\\subsection{{Sub C}}\n"
            f"Prose. \\citep{{p{i}}}\n"
        )
    # 1 auxiliary file: \subsection-only, no \section. This MUST NOT bump
    # top_count from 8 to 9.
    (sections / "06b_evaluation.tex").write_text(
        "\\subsection{Evaluation methodology}\n"
        "Auxiliary block input by another section. No top-level header.\n"
    )

    (run_dir / "5_paper" / "references.bib").write_text(
        "@article{p0, title={X}, year={2024},\n"
        "  annote={Bench reference.}}\n"
    )
    (run_dir / "4_outline" / "outline.json").write_text(json.dumps({
        "sections": []
    }))

    secs = aw._read_section_files(sections)
    outline = json.loads(
        (run_dir / "4_outline" / "outline.json").read_text()
    )
    result = aw.audit_structural_template(
        secs, run_dir / "5_paper" / "references.bib", outline,
    )
    inv = result["invariants"]["section_nesting"]
    # 8 explicit \section commands across 9 files → top=8, in [6..9].
    assert "top=8" in inv["value"], inv
    assert inv["ok"], inv


def test_section_nesting_falls_back_to_file_count_when_no_section_cmds(tmp_path):
    """Very early drafts may \\input section bodies from main.tex without
    emitting \\section in the section files themselves. Inv 1 falls back
    to one-section-per-file (excluding abstract) in that case."""
    run_dir = tmp_path / "draft_run"
    sections = run_dir / "5_paper" / "sections"
    sections.mkdir(parents=True)
    (run_dir / "4_outline").mkdir(parents=True)

    (sections / "00_abstract.tex").write_text("Abstract prose without macros.\n")
    for i in range(7):
        (sections / f"{i+1:02d}_chapter.tex").write_text(
            f"Prose without explicit \\\\section. \\subsection{{Sub A}}\n"
            f"\\subsection{{Sub B}}\n\\subsection{{Sub C}}\n"
            f"\\citep{{p{i}}}\n"
        )

    (run_dir / "5_paper" / "references.bib").write_text(
        "@article{p0, title={X}, year={2024}, annote={Ref.}}\n"
    )
    (run_dir / "4_outline" / "outline.json").write_text(json.dumps({
        "sections": []
    }))

    secs = aw._read_section_files(sections)
    outline = json.loads(
        (run_dir / "4_outline" / "outline.json").read_text()
    )
    result = aw.audit_structural_template(
        secs, run_dir / "5_paper" / "references.bib", outline,
    )
    inv = result["invariants"]["section_nesting"]
    # 7 chapter files (abstract excluded), no \section commands → fallback
    # gives top=7, in [6..9].
    assert "top=7" in inv["value"], inv


def test_argument_anchors_skip_aux_subsection_only_files(tmp_path):
    """Auxiliary .tex fragments that emit only \\subsection are not
    standalone chapters and must not be argument-anchor-checked in
    isolation; their anchors belong to whichever \\section file
    \\input{}s them."""
    sections = {
        "01_intro": "\\section{Introduction}\nProse.",
        "02_body": (
            "\\section{Body}\n"
            "% [CLAIM] \nA.\n% [STEELMAN] \nB.\n% [EVIDENCE] \nC.\n"
            "% [CONCESSION] \nD.\n% [SO-WHAT] \nE.\n"
        ),
        # Aux file with NO \section — only a subsection. Should be skipped
        # by audit_argument_anchors so its missing 5-anchor structure does
        # not penalise the score.
        "02b_aux": (
            "\\subsection{Evaluation methodology}\n"
            "Auxiliary block. No anchors here.\n"
        ),
    }
    info = aw.audit_argument_anchors(sections)
    # 02b_aux must NOT appear as a body section.
    assert "02b_aux" not in info["per_section"]
    assert "02_body" in info["per_section"]
    assert info["per_section"]["02_body"]["ok"] is True
    assert info["score"] == 1.0


# ---------------------------------------------------------------------------
# cross_cutting_matrix invariant: aux tables must be load-bearing
# (referenced from prose), not capped by count.
# ---------------------------------------------------------------------------


def test_cross_cutting_matrix_passes_when_aux_tables_are_referenced(tmp_path):
    """A run with the matrix declared and 5 aux tables, every one of them
    referenced from prose via \\ref{}, must pass invariant 4. There is no
    count cap — only the load-bearing requirement."""
    run_dir = tmp_path / "loadbearing_run"
    sections = run_dir / "5_paper" / "sections"
    sections.mkdir(parents=True)
    (run_dir / "4_outline").mkdir(parents=True)

    matrix_block = (
        "\\begin{table}[t]\\centering\n"
        "\\begin{tabular}{ll}A & B \\\\ \\end{tabular}\n"
        "\\caption{Cross-cutting matrix.}\n"
        "\\label{tab:cross-cutting-matrix}\n"
        "\\end{table}\n"
    )

    aux_tables_tex = "".join(
        "\\begin{table}[t]\\centering\n"
        "\\begin{tabular}{ll}A & B \\\\ \\end{tabular}\n"
        f"\\caption{{Aux table {i}.}}\n"
        f"\\label{{tab:aux{i}}}\n"
        "\\end{table}\n"
        for i in range(1, 6)
    )

    (sections / "01_body.tex").write_text(
        "\\section{Body}\n"
        + matrix_block
        + aux_tables_tex
        + "Tables \\ref{tab:cross-cutting-matrix}, \\ref{tab:aux1}, "
          "\\ref{tab:aux2}, \\ref{tab:aux3}, \\autoref{tab:aux4}, "
          "\\ref{tab:aux5} are all load-bearing.\n"
    )

    (run_dir / "5_paper" / "references.bib").write_text(
        "@article{a, title={X}, year={2024}, annote={ref.}}\n"
    )
    (run_dir / "4_outline" / "outline.json").write_text(json.dumps({
        "sections": [{"id": "01_body", "section_type": "body",
                      "subsections": [
                          {"section_type": "cross_cutting_matrix"}
                      ]}]
    }))

    secs = aw._read_section_files(sections)
    outline = json.loads(
        (run_dir / "4_outline" / "outline.json").read_text()
    )
    result = aw.audit_structural_template(
        secs, run_dir / "5_paper" / "references.bib", outline,
    )
    inv = result["invariants"]["cross_cutting_matrix"]
    assert inv["ok"], inv
    assert "aux_tables=5" in inv["value"]
    assert "0 unreferenced" in inv["value"]


def test_cross_cutting_matrix_flags_unreferenced_aux_table(tmp_path):
    """An aux table with a \\label{} that no sentence \\ref{}'s is
    decoration; invariant 4 must fail with the offending label name."""
    run_dir = tmp_path / "decorative_run"
    sections = run_dir / "5_paper" / "sections"
    sections.mkdir(parents=True)
    (run_dir / "4_outline").mkdir(parents=True)

    matrix_block = (
        "\\begin{table}[t]\\centering\n"
        "\\begin{tabular}{ll}A & B \\\\ \\end{tabular}\n"
        "\\caption{Cross-cutting matrix.}\n"
        "\\label{tab:cross-cutting-matrix}\n"
        "\\end{table}\n"
    )
    decorative_block = (
        "\\begin{table}[t]\\centering\n"
        "\\begin{tabular}{ll}A & B \\\\ \\end{tabular}\n"
        "\\caption{Decorative aux table.}\n"
        "\\label{tab:decorative}\n"
        "\\end{table}\n"
    )

    (sections / "01_body.tex").write_text(
        "\\section{Body}\n"
        + matrix_block
        + decorative_block
        + "Only the matrix is referenced: \\ref{tab:cross-cutting-matrix}. "
          "The decorative table sits unused.\n"
    )

    (run_dir / "5_paper" / "references.bib").write_text(
        "@article{a, title={X}, year={2024}, annote={ref.}}\n"
    )
    (run_dir / "4_outline" / "outline.json").write_text(json.dumps({
        "sections": [{"id": "01_body", "section_type": "body",
                      "subsections": [
                          {"section_type": "cross_cutting_matrix"}
                      ]}]
    }))

    secs = aw._read_section_files(sections)
    outline = json.loads(
        (run_dir / "4_outline" / "outline.json").read_text()
    )
    result = aw.audit_structural_template(
        secs, run_dir / "5_paper" / "references.bib", outline,
    )
    inv = result["invariants"]["cross_cutting_matrix"]
    assert not inv["ok"], inv
    assert "tab:decorative" in inv["issue"]


def test_cross_cutting_matrix_resolves_input_for_table_labels(tmp_path):
    """Aux tables packaged as a fragment .tex file that the section
    \\input{}s must be visible to the audit. The label lives in the
    fragment; the \\ref{} lives in the section prose."""
    run_dir = tmp_path / "input_run"
    sections = run_dir / "5_paper" / "sections"
    sections.mkdir(parents=True)
    figures = run_dir / "5_paper" / "figures"
    figures.mkdir(parents=True)
    (run_dir / "4_outline").mkdir(parents=True)

    (figures / "aux_recipe.tex").write_text(
        "\\begin{table}[t]\\centering\n"
        "\\begin{tabular}{ll}Recipe & Adoption \\\\ \\end{tabular}\n"
        "\\caption{Recipe adoption.}\n"
        "\\label{tab:recipe}\n"
        "\\end{table}\n"
    )
    matrix_block = (
        "\\begin{table}[t]\\centering\n"
        "\\begin{tabular}{ll}A & B \\\\ \\end{tabular}\n"
        "\\caption{Cross-cutting matrix.}\n"
        "\\label{tab:cross-cutting-matrix}\n"
        "\\end{table}\n"
    )
    (sections / "01_body.tex").write_text(
        "\\section{Body}\n"
        + matrix_block
        + "\\input{figures/aux_recipe}\n"
        + "Both \\ref{tab:cross-cutting-matrix} and \\ref{tab:recipe} are "
          "used in the prose.\n"
    )

    (run_dir / "5_paper" / "references.bib").write_text(
        "@article{a, title={X}, year={2024}, annote={ref.}}\n"
    )
    (run_dir / "4_outline" / "outline.json").write_text(json.dumps({
        "sections": [{"id": "01_body", "section_type": "body",
                      "subsections": [
                          {"section_type": "cross_cutting_matrix"}
                      ]}]
    }))

    secs = aw._read_section_files(sections)
    outline = json.loads(
        (run_dir / "4_outline" / "outline.json").read_text()
    )
    result = aw.audit_structural_template(
        secs, run_dir / "5_paper" / "references.bib", outline,
    )
    inv = result["invariants"]["cross_cutting_matrix"]
    assert inv["ok"], inv
    assert "aux_tables=1" in inv["value"]
