"""Shared pytest fixtures for the AutoSurvey test suite.

Provides:
    - ``sample_brief``: a representative parsed-brief dict
      (long-context extension methods).
    - ``survey_run_dir``: a fully populated ``~/.autosurvey/runs/<id>/``
      directory with the artifacts every audit / dashboard tool reads.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import pytest


# ---------------------------------------------------------------------------
# sample_brief
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_brief() -> Dict[str, Any]:
    """Return a parsed-brief dict modeled on the long-context-extension
    example."""
    return {
        "topic": "Long-Context Extension Methods for Pretrained Language Models",
        "scope": {
            "include": [
                "positional-encoding interpolation",
                "sparse and sliding-window attention used post-hoc",
                "KV-cache compression and eviction",
                "retrieval-in-context",
                "linear-attention drop-in replacements",
                "training-free prompt-level tricks",
            ],
            "exclude": [
                "encoder-only and encoder-decoder models",
                "vision and multimodal long-context",
                "from-scratch linear-attention architectures",
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
        },
        "dimensions": [
            "family",
            "reach",
            "adaptation_cost",
            "decode_compute",
            "long_quality",
            "failure_modes",
        ],
        "style": {
            "detail_driven": [
                "prefer concrete numbers (max context, RULER / NIAH scores, FLOPs) over vague claims",
                "name benchmarks and base models explicitly",
                "quote perplexity / accuracy deltas at long context",
            ],
            "forward_looking": [
                "highlight open problems and emerging directions",
                "flag context regimes that have not yet been explored",
                "note where conventional wisdom is being challenged",
            ],
            "extraction_hints": [
                "extract method, base model, max context, benchmark numbers, adaptation budget, decode cost, failure mode",
                "extract ablation tables when papers report them",
                "tag claims with the specific run or checkpoint they came from",
            ],
        },
        "configuration": {
            "max_papers": 80,
            "verification_strictness": "high",
        },
    }


# ---------------------------------------------------------------------------
# survey_run_dir — full run directory with all artifacts populated
# ---------------------------------------------------------------------------

import json


def _build_survey_run_dir(root: Path) -> Path:
    """Construct a minimal but schema-valid run directory.

    Layout::
        <root>/
          state.json                                   (phases dict)
          brief.md, brief.parsed.json
          1_search/filtered.jsonl                      3 papers
          1_search/claims_cache.jsonl                  3 records, ≥2 atomic_claims each
          1_search/cards.jsonl                         3 cards with _decision_summary
          2_thesis/thesis.json                         contestable thesis + 4 steps + 2 objections
          4_outline/outline.json                       3 sections + tier_axis
          5_paper/sections/00_abstract.tex             restates thesis
          5_paper/sections/01_introduction.tex         hook + Why-Now + Relationship + Contributions
          5_paper/sections/02_body.tex                 5 anchors (CLAIM/STEELMAN/EVIDENCE/CONCESSION/SO-WHAT)
          5_paper/sections/03_open_problems.tex        4 buckets (PROBLEM-STATEMENT/EXISTING-APPROACHES/LIMITATIONS/RESEARCH-DIRECTIONS)
          5_paper/sections/04_conclusion.tex           restates thesis
          6_verify/CITATION_VERIFY.json                hard_gate=PASS
    """
    run_dir = root / "survey-run-test"
    run_dir.mkdir(parents=True, exist_ok=True)

    # state.json
    (run_dir / "state.json").write_text(json.dumps({
        "run_id":       "survey-run-test",
        "brief_path":   str(run_dir / "brief.md"),
        "topic":        "Large Language Model Pretraining",
        "phases": {
            "drafting":  {"status": "completed",
                          "substeps": {"refine_brief":   {"status": "completed"},
                                       "search":         {"status": "completed"},
                                       "thesis":         {"status": "completed"},
                                       "outline_sketch": {"status": "completed"}}},
            "arguing":   {"status": "completed", "iterations": []},
            "polishing": {"status": "in_progress",
                          "substeps": {"review":     {"status": "pending"},
                                       "checkpoint": {"status": "pending"},
                                       "audits":     {"status": "pending"},
                                       "compile":    {"status": "pending"}}},
        },
    }, indent=2), encoding="utf-8")

    (run_dir / "brief.md").write_text(
        "Large Language Model pretraining survey targeting recipe-level detail.\n",
        encoding="utf-8",
    )
    (run_dir / "brief.parsed.json").write_text(json.dumps({
        "topic":      "Large Language Model Pretraining",
        "scope":      {"include": ["scaling laws", "data"], "exclude": ["RLHF"]},
        "sources":    {"categories": ["arxiv"], "year_range": [2021, 2026]},
        "dimensions": ["scaling_laws", "data", "architecture"],
    }, indent=2), encoding="utf-8")

    # 1_search/
    search_dir = run_dir / "1_search"
    search_dir.mkdir(parents=True, exist_ok=True)
    papers = [
        {"cite_key":  "kaplan2020scaling",
         "paper_id": "kaplan2020scaling",
         "title":    "Scaling Laws for Neural Language Models",
         "abstract": ("We study empirical scaling laws for language model "
                      "performance on the cross-entropy loss. The loss scales "
                      "as a power law with model size, dataset size, and the "
                      "amount of compute used for training, with some trends "
                      "spanning more than seven orders of magnitude."),
         "url":      "https://arxiv.org/abs/2001.08361",
         "arxiv_id": "2001.08361"},
        {"cite_key":  "hoffmann2022chinchilla",
         "paper_id": "hoffmann2022chinchilla",
         "title":    "Training Compute-Optimal Large Language Models",
         "abstract": ("We investigate the optimal model size and number of "
                      "tokens for training a transformer language model "
                      "under a given compute budget. We find that current "
                      "large language models are significantly undertrained, "
                      "and we train a 70B parameter model on 1.4 trillion tokens."),
         "url":      "https://arxiv.org/abs/2203.15556",
         "arxiv_id": "2203.15556"},
        {"cite_key":  "touvron2023llama",
         "paper_id": "touvron2023llama",
         "title":    "LLaMA: Open and Efficient Foundation Language Models",
         "abstract": ("We introduce LLaMA, a collection of foundation "
                      "language models ranging from 7B to 65B parameters. "
                      "We train our models on trillions of tokens using "
                      "publicly available datasets exclusively."),
         "url":      "https://arxiv.org/abs/2302.13971",
         "arxiv_id": "2302.13971"},
    ]
    (search_dir / "filtered.jsonl").write_text(
        "\n".join(json.dumps(p) for p in papers) + "\n",
        encoding="utf-8",
    )

    # claims_cache.jsonl
    claims = [
        {"cite_key": "kaplan2020scaling",
         "what_paper_argues": ("Loss scales as a power law in model size, "
                                "dataset size and compute, across seven orders of magnitude."),
         "atomic_claims": [
             {"claim_id":   "kaplan-1",
              "claim_type": "empirical",
              "anchor":     "scaling-laws",
              "quote":      "the loss scales as a power law with model size, "
                            "dataset size, and the amount of compute"},
             {"claim_id":   "kaplan-2",
              "claim_type": "empirical",
              "anchor":     "scaling-laws",
              "quote":      "with some trends spanning more than seven orders of magnitude"},
         ]},
        {"cite_key": "hoffmann2022chinchilla",
         "what_paper_argues": ("Models should be trained on far more tokens "
                                "than the Kaplan rule suggested."),
         "atomic_claims": [
             {"claim_id":   "chinchilla-1",
              "claim_type": "empirical",
              "anchor":     "compute-optimal",
              "quote":      "we train a 70B parameter model on 1.4 trillion tokens"},
             {"claim_id":   "chinchilla-2",
              "claim_type": "critique",
              "anchor":     "compute-optimal",
              "quote":      "current large language models are significantly undertrained"},
         ]},
        {"cite_key": "touvron2023llama",
         "what_paper_argues": ("Open foundation models trained on public data "
                                "can match closed equivalents."),
         "atomic_claims": [
             {"claim_id":   "llama-1",
              "claim_type": "methodological",
              "anchor":     "open-data",
              "quote":      "ranging from 7B to 65B parameters"},
             {"claim_id":   "llama-2",
              "claim_type": "methodological",
              "anchor":     "open-data",
              "quote":      "using publicly available datasets exclusively"},
         ]},
    ]
    (search_dir / "claims_cache.jsonl").write_text(
        "\n".join(json.dumps(c) for c in claims) + "\n",
        encoding="utf-8",
    )

    # cards.jsonl with _decision_summary
    cards = [
        {"cite_key": "kaplan2020scaling",
         "title":    "Scaling Laws for Neural Language Models",
         "_decision_summary": {
             "tier":               "T1",
             "one_line_role":      "Foundational scaling law",
             "key_capability":     "Power-law fit",
             "primary_limitation": "Token budget undercounted",
             "availability":       "open"}},
        {"cite_key": "hoffmann2022chinchilla",
         "title":    "Training Compute-Optimal Large Language Models",
         "_decision_summary": {
             "tier":               "T2",
             "one_line_role":      "Compute-optimal recipe",
             "key_capability":     "Token-rich training",
             "primary_limitation": "DM internal data",
             "availability":       "closed"}},
        {"cite_key": "touvron2023llama",
         "title":    "LLaMA Foundation Models",
         "_decision_summary": {
             "tier":               "T2",
             "one_line_role":      "Open foundation family",
             "key_capability":     "Public data only",
             "primary_limitation": "Smaller than peers",
             "availability":       "weights-only"}},
    ]
    (search_dir / "cards.jsonl").write_text(
        "\n".join(json.dumps(c) for c in cards) + "\n",
        encoding="utf-8",
    )

    # 2_thesis/thesis.json
    thesis_dir = run_dir / "2_thesis"
    thesis_dir.mkdir(parents=True, exist_ok=True)
    thesis_doc = {
        "thesis": ("Pretraining has consolidated around the compute-optimal "
                   "regime, but the dominant token-rich recipe remains "
                   "premature for long-context architectures."),
        "thesis_id_chosen": "B",
        "candidates": [
            {"id": "A", "thesis": "Scale alone determines pretraining outcomes."},
            {"id": "B", "thesis": ("Compute-optimal recipes consolidated, but "
                                    "remain premature for long-context.")},
            {"id": "C", "thesis": "Open data is sufficient for parity."},
        ],
        "argument_steps": [
            {"step_id": "S1", "claim": "Scaling laws set the stage."},
            {"step_id": "S2", "claim": "Chinchilla shifted the token budget."},
            {"step_id": "S3", "claim": "Open recipes followed Chinchilla."},
            {"step_id": "S4", "claim": "Long-context exposes the gap."},
        ],
        "anticipated_objections": [
            {"objection": "Loss curves still trend log-linear.",
             "rebuttal":  ("Yet downstream tasks plateau; loss is not the "
                            "right axis at extreme scale.")},
            {"objection": "Open data parity is achieved already.",
             "rebuttal":  ("Parity holds at 65B but the long-context regime "
                            "has not yet been tested at that scale.")},
        ],
    }
    (thesis_dir / "thesis.json").write_text(
        json.dumps(thesis_doc, indent=2), encoding="utf-8")

    # 4_outline/outline.json with tier_axis
    outline_dir = run_dir / "4_outline"
    outline_dir.mkdir(parents=True, exist_ok=True)
    outline_doc = {
        "topic": "Large Language Model Pretraining",
        "sections": [
            {"section_id": "02_scaling",
             "id":         "02_scaling",
             "title":      "Scaling Laws",
             "argues_for_thesis_step": "S1",
             "primary_papers": ["kaplan2020scaling", "hoffmann2022chinchilla"]},
            {"section_id": "03_open_recipes",
             "id":         "03_open_recipes",
             "title":      "Open Recipes",
             "argues_for_thesis_step": "S3",
             "primary_papers": ["touvron2023llama"]},
            {"section_id": "04_long_context",
             "id":         "04_long_context",
             "title":      "Long Context Frontier",
             "argues_for_thesis_step": "S4",
             "primary_papers": ["touvron2023llama"]},
        ],
        # Step S2 is intentionally bound below to give full coverage
        "tier_axis": {
            "name": "Pretraining Generation",
            "tiers": [
                {"id": "T1", "label": "Pre-Chinchilla",
                 "description": "Kaplan-era undertrained models"},
                {"id": "T2", "label": "Compute-Optimal",
                 "description": "Token-rich Chinchilla descendants"},
                {"id": "T3", "label": "Long-Context",
                 "description": "Stretched-context successors"},
            ],
            "feature_columns": ["Architecture", "Data Scale", "Compute"],
            "cells": {
                "T1": {"Architecture": ["Dense"], "Data Scale": ["300B tok"],
                        "Compute":      ["Modest"]},
                "T2": {"Architecture": ["Dense"], "Data Scale": ["1.4T tok"],
                        "Compute":      ["High"]},
                "T3": {"Architecture": ["Dense", "MoE"],
                        "Data Scale":   ["2T+ tok"],
                        "Compute":      ["Very high"]},
            },
            "key_insight": ("The token-budget axis matters more than parameter "
                             "count once compute exceeds 1e23 FLOPs."),
        },
    }
    # bind every argument step to ≥1 section so thesis_coherence passes
    outline_doc["sections"].insert(1, {
        "section_id": "02b_chinchilla",
        "id":         "02b_chinchilla",
        "title":      "Chinchilla Pivot",
        "argues_for_thesis_step": "S2",
        "primary_papers": ["hoffmann2022chinchilla"],
    })
    (outline_dir / "outline.json").write_text(
        json.dumps(outline_doc, indent=2), encoding="utf-8")

    # 5_paper/sections/*.tex
    sections_dir = run_dir / "5_paper" / "sections"
    sections_dir.mkdir(parents=True, exist_ok=True)

    (sections_dir / "00_abstract.tex").write_text(
        r"""\begin{abstract}
Pretraining has consolidated around the compute-optimal regime,
but the dominant token-rich recipe remains premature for long-context
architectures. We survey three generations: pre-Chinchilla scaling
laws \cite{kaplan2020scaling}, the compute-optimal pivot
\cite{hoffmann2022chinchilla}, and the open-recipe descendants
\cite{touvron2023llama} that continue the trajectory.
\end{abstract}
""", encoding="utf-8")

    (sections_dir / "01_introduction.tex").write_text(
        r"""\section{Introduction}
In 2020, the field witnessed a shift from architecture engineering to
power-law fitting --- a transition like the one that turned chemistry
into thermodynamics, where 70 billion parameters became less interesting
than the 1.4 trillion tokens you fed them \cite{kaplan2020scaling,hoffmann2022chinchilla}.

\subsection*{Why Now?}
The compute-optimal recipe has consolidated, but is now strained by
long-context demands.

\subsection*{Relationship to Existing Surveys}
Unlike prior surveys that catalogue architectures, we argue a thesis
about the token-budget axis.

\paragraph{Contributions.}
\begin{enumerate}
\item A unified token-budget account across three generations.
\item A decision-summary table comparing recipes.
\item A 5-anchor argument structure for each body section.
\item Identification of long-context as the breaking frontier.
\end{enumerate}
""", encoding="utf-8")

    (sections_dir / "02_body.tex").write_text(
        r"""\section{Scaling Laws and the Compute-Optimal Pivot}
% [CLAIM]
The compute-optimal recipe consolidated pretraining practice
\cite{hoffmann2022chinchilla}.

% [STEELMAN]
A reasonable counter-position holds that loss-axis power laws
\cite{kaplan2020scaling} remained valid and the Chinchilla finding was
merely a constant-factor correction.

% [EVIDENCE]
Yet at 70B parameters trained on 1.4 trillion tokens, downstream
benchmarks improved beyond what the Kaplan extrapolation predicted
\cite{hoffmann2022chinchilla}.

% [CONCESSION]
We grant that the Kaplan formulation still applies when the dataset
side of the trade-off is fixed --- a regime that returns under
multi-modal pretraining where image-token supply is bounded.

% [SO-WHAT]
The token-budget axis is therefore the load-bearing dimension; the
remainder of the survey reads pretraining history through it.
""", encoding="utf-8")

    (sections_dir / "03_open_problems.tex").write_text(
        r"""\section{Open Problems}
\subsection{Long-Context Token Budgets}
% [PROBLEM-STATEMENT]
Long-context models exhaust the public token supply far below
Chinchilla-optimal.
% [EXISTING-APPROACHES]
Synthetic-token augmentation \cite{touvron2023llama} and
repetition-aware schedules.
% [LIMITATIONS]
Both degrade calibration when sequence length exceeds 32k.
% [RESEARCH-DIRECTIONS]
Token-recycling curricula and lossy long-context fine-tuning regimes.

\subsection{Open-Data Parity at Scale}
% [PROBLEM-STATEMENT]
Whether open data alone supports the next regime is unsettled.
% [EXISTING-APPROACHES]
LLaMA-style purely-public corpora \cite{touvron2023llama}.
% [LIMITATIONS]
Public corpora plateau at roughly 2T tokens.
% [RESEARCH-DIRECTIONS]
Federated and domain-licensed expansion.
""", encoding="utf-8")

    (sections_dir / "04_conclusion.tex").write_text(
        r"""\section{Conclusion}
Pretraining has consolidated around the compute-optimal regime, but
the dominant token-rich recipe remains premature for long-context
architectures. The token-budget axis subsumes the parameter axis past
the 1e23 FLOPs threshold.
""", encoding="utf-8")

    # 6_verify/CITATION_VERIFY.json (hard_gate=PASS, all green)
    verify_dir = run_dir / "6_verify"
    verify_dir.mkdir(parents=True, exist_ok=True)
    (verify_dir / "CITATION_VERIFY.json").write_text(json.dumps({
        "assurance_level":   "polished",
        "hard_gate":         "PASS",
        "claim_audit":       "PASS",
        "numeric_grounding": "PASS",
        "trend_audit":       "NOT_APPLICABLE",
        "kill_argument":     "NOT_APPLICABLE",
        "phantom_keys":      [],
        "citations_audited": 5,
    }, indent=2), encoding="utf-8")

    return run_dir


@pytest.fixture
def survey_run_dir(tmp_path: Path) -> Path:
    """Function-scoped fresh run directory.

    Each test gets a clean copy under its own ``tmp_path`` so polluting
    tests cannot leak into siblings. See ``_build_survey_run_dir`` for layout.
    """
    return _build_survey_run_dir(tmp_path)

