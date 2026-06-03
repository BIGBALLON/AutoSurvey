#!/usr/bin/env python3
"""
refine_brief.py — Stage 0 of the brief-driven AutoSurvey pipeline.

Validates a structured candidate JSON (extracted by the agent from a
free-form ``brief.md``), applies default style augmentation, writes the
canonical ``brief.parsed.json``, and prints a human-readable refinement
summary.

The structural extraction is performed by the agent (Claude Code while
interpreting ``skills/survey-run/SKILL.md``) before this tool runs; the
agent is responsible for producing the candidate JSON and passing the
path via ``--candidate``. This tool is a deterministic
validator + augmenter + display helper, with no external LLM dependency.

CLI::

    python3 refine_brief.py --brief <path>
                            --candidate <candidate.json>
                            --output <path>
                            [--interactive] [--auto-confirm]
                            [--verbose]

See ``skills/shared-references/brief-contract.md`` for the canonical
schema of the candidate JSON.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Tuple


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MIN_BRIEF_WORDS = 50
MIN_DIMENSIONS = 3

DEFAULT_SOURCE_CATEGORIES = [
    "arxiv",
    "semantic_scholar",
    "openalex",
    "tech_reports",
    "blogs",
]
DEFAULT_YEAR_RANGE = [2021, 2026]

FORWARD_LOOKING_RULE = (
    "forward-looking insight: identify trends, predict trajectories, "
    "surface gaps the field is heading toward"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def read_brief(path: str) -> str:
    """Read brief.md and enforce the minimum-content threshold.

    Raises:
        FileNotFoundError: if the path does not exist.
        ValueError: if the brief contains fewer than MIN_BRIEF_WORDS words.
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"brief file not found: {path}")
    text = p.read_text(encoding="utf-8")
    word_count = len(text.split())
    if word_count < MIN_BRIEF_WORDS:
        raise ValueError(
            f"brief is too short ({word_count} words). Minimum {MIN_BRIEF_WORDS} words. "
            f"See examples/briefs/ for reference."
        )
    return text


def is_cache_valid(brief_path: str, output_path: str) -> bool:
    """Return True if ``output_path`` exists and is newer than ``brief_path``."""
    bp = Path(brief_path)
    op = Path(output_path)
    if not op.exists() or not bp.exists():
        return False
    return op.stat().st_mtime >= bp.stat().st_mtime


def load_candidate(path: str) -> Dict[str, Any]:
    """Load a candidate JSON file produced by the agent.

    Raises:
        FileNotFoundError: if the candidate file is missing.
        json.JSONDecodeError: if the file is not valid JSON.
        ValueError: if the top-level value is not a JSON object.
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"candidate file not found: {path}")
    data = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("candidate JSON must be a top-level object.")
    return data


def _coerce_defaults(parsed: Dict[str, Any]) -> Dict[str, Any]:
    """Fill missing optional fields with empty / default values in-place."""
    parsed.setdefault("topic", "")
    parsed.setdefault("scope", {})
    if not isinstance(parsed["scope"], dict):
        parsed["scope"] = {}
    parsed["scope"].setdefault("include", [])
    parsed["scope"].setdefault("exclude", [])

    parsed.setdefault("sources", {})
    if not isinstance(parsed["sources"], dict):
        parsed["sources"] = {}
    sources = parsed["sources"]
    if not sources.get("categories"):
        sources["categories"] = list(DEFAULT_SOURCE_CATEGORIES)
    if not sources.get("year_range"):
        sources["year_range"] = list(DEFAULT_YEAR_RANGE)
    sources.setdefault("github_repos", [])
    sources.setdefault("model_cards", [])

    parsed.setdefault("dimensions", [])
    parsed.setdefault("style", [])

    parsed.setdefault("configuration", {})
    if not isinstance(parsed["configuration"], dict):
        parsed["configuration"] = {}
    cfg = parsed["configuration"]
    cfg.setdefault("trends_section", "include")

    parsed.setdefault("_uncertainties", [])
    return parsed


def validate_parsed(parsed: Dict[str, Any]) -> Tuple[bool, str]:
    """Validate the parsed brief structure.

    Returns:
        (True, "") if valid.
        (False, error_message) otherwise.
    """
    if not isinstance(parsed, dict):
        return False, "Error: candidate is not a JSON object."

    _coerce_defaults(parsed)

    topic = parsed.get("topic", "")
    if not isinstance(topic, str) or not topic.strip():
        return (
            False,
            "Error: could not identify a topic. Please add a line like "
            "'topic: <subject>' or a clear opening sentence.",
        )

    # Type-check scope
    scope = parsed["scope"]
    if not isinstance(scope.get("include"), list):
        return False, "Error: scope.include must be a list."
    if not isinstance(scope.get("exclude"), list):
        return False, "Error: scope.exclude must be a list."

    # Type-check sources
    sources = parsed["sources"]
    if not isinstance(sources.get("categories"), list):
        return False, "Error: sources.categories must be a list."
    yr = sources.get("year_range")
    if not (
        isinstance(yr, list)
        and len(yr) == 2
        and all(isinstance(x, int) for x in yr)
    ):
        return False, "Error: sources.year_range must be [start, end] integers."
    for url_field in ("github_repos", "model_cards"):
        if not isinstance(sources.get(url_field, []), list):
            return False, f"Error: sources.{url_field} must be a list."

    # Type-check dimensions
    dims = parsed["dimensions"]
    if not isinstance(dims, list):
        return False, "Error: dimensions must be a list."
    for d in dims:
        if not (isinstance(d, dict) and "name" in d and "description" in d):
            return (
                False,
                "Error: each dimension must be {name, description}.",
            )
    if len(dims) < MIN_DIMENSIONS:
        names = [d.get("name", "?") for d in dims]
        return (
            False,
            f"Only {len(dims)} dimensions detected: {names}. "
            f"A Survey needs >={MIN_DIMENSIONS} thematic axes.",
        )

    # Type-check style
    if not isinstance(parsed["style"], list):
        return False, "Error: style must be a list."

    # Type-check configuration
    cfg = parsed["configuration"]
    if cfg.get("trends_section") not in ("include", "skip"):
        return (
            False,
            "Error: configuration.trends_section must be 'include' or 'skip'.",
        )

    if not isinstance(parsed["_uncertainties"], list):
        return False, "Error: _uncertainties must be a list."

    return True, ""


def apply_default_style_augmentation(parsed: Dict[str, Any]) -> Dict[str, Any]:
    """Append the canonical forward-looking style rule unless opted out.

    Skip-conditions:
      - ``"no-forward-looking"`` already appears in ``parsed["style"]``.
      - ``configuration.trends_section == "skip"``.
    """
    style = parsed.setdefault("style", [])
    cfg = parsed.setdefault("configuration", {})
    if "no-forward-looking" in style:
        return parsed
    if cfg.get("trends_section") == "skip":
        return parsed
    if FORWARD_LOOKING_RULE not in style:
        style.append(FORWARD_LOOKING_RULE)
    return parsed


def format_display(
    parsed: Dict[str, Any], output_path: str, brief_path: str
) -> str:
    """Render the multi-line refinement summary string."""
    topic = parsed.get("topic", "")
    scope = parsed.get("scope", {})
    sources = parsed.get("sources", {})
    dims = parsed.get("dimensions", [])
    style = parsed.get("style", [])
    cfg = parsed.get("configuration", {})
    uncertainties = parsed.get("_uncertainties", [])

    include_n = len(scope.get("include", []))
    exclude_n = len(scope.get("exclude", []))

    categories = sources.get("categories", []) or []
    year_range = sources.get("year_range", DEFAULT_YEAR_RANGE)
    cats_csv = ", ".join(categories) if categories else "(none)"
    yr_start, yr_end = year_range[0], year_range[1]

    gh_repos = sources.get("github_repos", []) or []
    mc = sources.get("model_cards", []) or []
    url_total = len(gh_repos) + len(mc)

    forward_included = FORWARD_LOOKING_RULE in style
    fwd_str = "yes" if forward_included else "no"

    lines = []
    lines.append("Brief refined. Here's what I understood:")
    lines.append("")
    lines.append(f"  Topic:       {topic}")
    lines.append(
        f"  Scope:       include {include_n} rules / exclude {exclude_n} rules"
    )
    lines.append(
        f"  Sources:     {cats_csv} (year range: {yr_start}-{yr_end})"
    )
    if url_total > 0:
        lines.append(
            f"               + {len(gh_repos)} GitHub repos / "
            f"{len(mc)} model cards"
        )
    lines.append(f"  Dimensions:  {len(dims)} axes")
    for d in dims:
        name = d.get("name", "?")
        desc = d.get("description", "")
        lines.append(f"               - {name}: {desc}")
    lines.append(
        f"  Style:       {len(style)} discipline rules "
        f"(forward-looking auto-included: {fwd_str})"
    )
    lines.append(
        f"  Config:      trends_section={cfg.get('trends_section', 'include')}"
    )

    if uncertainties:
        lines.append("")
        lines.append("Uncertainties:")
        for u in uncertainties:
            lines.append(f"  - {u}")

    lines.append("")
    lines.append(f"Saved to: {output_path}")
    lines.append(
        f"To adjust: edit {output_path} directly, or modify {brief_path} "
        f"and re-run."
    )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


CANDIDATE_REQUIRED_MSG = (
    "Error: --candidate is required.\n\n"
    "This tool validates structured JSON the agent extracted from your brief.\n"
    "The orchestrator (skills/survey-run/SKILL.md) handles producing the candidate\n"
    "JSON and passes it via --candidate.\n\n"
    "If you're invoking this directly for testing, pass a hand-written candidate\n"
    "JSON conforming to skills/shared-references/brief-contract.md."
)


def _parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="refine_brief.py",
        description=(
            "Stage 0 of the brief-driven AutoSurvey pipeline. Validates a "
            "candidate JSON the agent extracted from a free-form brief.md, "
            "applies default style augmentation, writes "
            "brief.parsed.json, and prints a refinement summary. No external "
            "LLM dependency — structural extraction is the agent's job."
        ),
    )
    parser.add_argument(
        "--brief",
        required=True,
        help="Path to the free-form brief.md file.",
    )
    parser.add_argument(
        "--candidate",
        required=False,  # validated manually so we can show the long help text
        default=None,
        help=(
            "Path to the candidate JSON the agent produced from the brief. "
            "REQUIRED. See skills/shared-references/brief-contract.md for "
            "the schema."
        ),
    )
    parser.add_argument(
        "--output",
        required=True,
        help="Path to write brief.parsed.json.",
    )
    parser.add_argument(
        "--interactive",
        action="store_true",
        help="Pause and prompt before continuing the pipeline.",
    )
    parser.add_argument(
        "--auto-confirm",
        action="store_true",
        help=(
            "Default behaviour. Skip the interactive prompt entirely. "
            "Provided for explicitness."
        ),
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Verbose diagnostic output.",
    )
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = _parse_args(argv)

    brief_path = args.brief
    output_path = args.output
    candidate_path = args.candidate

    # 0. --candidate is REQUIRED. Validated manually so we can print a helpful
    #    multi-line message instead of argparse's terse usage error.
    if not candidate_path:
        print(CANDIDATE_REQUIRED_MSG, file=sys.stderr)
        return 1

    # 1. Existence check
    if not Path(brief_path).exists():
        print(f"Error: brief file not found: {brief_path}", file=sys.stderr)
        return 2

    # 2. Caching
    if is_cache_valid(brief_path, output_path):
        print(
            f"Cached: {output_path} is newer than {brief_path}; "
            f"skipping refinement."
        )
        return 0

    # 3. Read + min-content
    try:
        read_brief(brief_path)
    except FileNotFoundError:
        print(f"Error: brief file not found: {brief_path}", file=sys.stderr)
        return 2
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    # 4. Load the agent-produced candidate JSON
    try:
        parsed = load_candidate(candidate_path)
    except FileNotFoundError:
        print(
            f"Error: candidate file not found: {candidate_path}",
            file=sys.stderr,
        )
        return 2
    except json.JSONDecodeError as e:
        print(
            f"Error: could not parse candidate JSON: {e}",
            file=sys.stderr,
        )
        return 1
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    if args.verbose:
        print(
            f"Loaded candidate from {candidate_path} "
            f"({len(json.dumps(parsed))} bytes).",
            file=sys.stderr,
        )

    # 5. Validate
    ok, err = validate_parsed(parsed)
    if not ok:
        print(err, file=sys.stderr)
        return 1

    # 6. Default style augmentation
    apply_default_style_augmentation(parsed)

    # 7. Write output
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(parsed, f, indent=2, ensure_ascii=False)

    # 8. Display
    print(format_display(parsed, output_path, brief_path))

    # 9. Interactive gate
    if args.interactive:
        try:
            answer = input(
                f"Proceed? [Y/n] (or edit {output_path} first): "
            ).strip().lower()
        except EOFError:
            answer = ""
        if answer in ("n", "no"):
            print("Aborted by user. Pipeline will not continue.")
            return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
