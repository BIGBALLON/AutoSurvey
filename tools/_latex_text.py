"""tools/_latex_text.py — shared LaTeX-text helpers used by writing audits.

Three audit tools (audit_writing, prose_polish, reverse_outline) all need
the same primitive: "given a chunk of LaTeX, what does the *reader* see?".
Previously each tool carried its own private copy of the regex sets and
strippers, with subtle drift over time. This module is the single source
of truth.

What's here, and which callers use it:

* ``strip_leading_latex_commands(chunk)`` — recursively peel structural
  commands (\\label, \\subsection, \\textbf, \\emph, …) at the *head* of a
  paragraph, used to decide whether real prose follows. Callers:
  audit_writing.audit_narrative_pillars,
  prose_polish._first_paragraph_after.

* ``has_prose(paragraph)`` — true iff a paragraph still has real
  word-tokens after LaTeX commands and inline math are removed. Used to
  skip pure-LaTeX paragraphs (\\input, \\begin{figure}, \\includegraphics)
  that would otherwise be flagged as "weak topic sentence (0 words)".
  Callers: reverse_outline.extract_section_metadata.

* ``strip_latex_for_word_count(text)`` — three-pass strip that
    1. UNWRAPS typographic commands (\\textbf{X} → 'X', repeated for
       nesting),
    2. DROPS reference-style commands entirely (\\citep{X}, \\ref{X},
       \\label{X}, \\input{X}, …),
    3. DROPS any remaining LaTeX commands together with their args.
  Used to count visible words a reader would see. Caller:
  reverse_outline.is_weak.

These helpers are intentionally string-in / string-out so they remain
trivial to unit-test and swap. There is one regression test per helper
in tests/test_latex_text.py.
"""
from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Leading structural commands at the *start* of a paragraph
# ---------------------------------------------------------------------------

_LEADING_CMD_RE = re.compile(
    r"^\s*\\(?:label|subsection\*?|subsubsection\*?|paragraph\*?|"
    r"emph|textbf|textit|underline)\s*\*?\s*\{[^}]*\}\s*"
)


def strip_leading_latex_commands(chunk: str) -> str:
    """Repeatedly peel \\label / \\subsection / \\textbf / \\emph at the
    head of a paragraph. Returns the residue; an empty string means the
    paragraph carries no real prose at its head.
    """
    prev = None
    out = chunk
    while out != prev:
        prev = out
        out = _LEADING_CMD_RE.sub("", out, count=1).lstrip()
    return out


# ---------------------------------------------------------------------------
# "Does this paragraph contain any real prose?"
# ---------------------------------------------------------------------------

_ANY_LATEX_CMD_RE = re.compile(
    r"\\[a-zA-Z]+\*?(?:\[[^\]]*\])?(?:\{[^}]*\})*"
)
_INLINE_MATH_RE = re.compile(r"\$[^$]*\$")


def has_prose(paragraph: str, min_words: int = 3, min_word_len: int = 2) -> bool:
    """True iff the paragraph still contains ≥``min_words`` word-tokens of
    length ≥``min_word_len`` after LaTeX commands and inline math are
    stripped. Used to filter out pure-LaTeX paragraphs (\\input{...},
    \\begin{figure}…\\end{figure}, \\includegraphics[...]{...}).
    """
    cleaned = _ANY_LATEX_CMD_RE.sub(" ", paragraph)
    cleaned = _INLINE_MATH_RE.sub(" ", cleaned)
    cleaned = re.sub(r"[^A-Za-z]+", " ", cleaned)
    words = [w for w in cleaned.split() if len(w) >= min_word_len]
    return len(words) >= min_words


# ---------------------------------------------------------------------------
# "Strip LaTeX while preserving visible text" (for word-count audits)
# ---------------------------------------------------------------------------

_TEXT_BEARING_CMDS = (
    "textbf", "textit", "emph", "underline", "textsc", "texttt",
    "textrm", "textsf", "textmd", "textnormal", "uline", "uuline",
)
_TEXT_BEARING_RE = re.compile(
    r"\\(?:" + "|".join(_TEXT_BEARING_CMDS) + r")\*?\{([^}]*)\}"
)
_REFERENCE_CMDS_RE = re.compile(
    r"\\(?:cite[a-z]*|ref|autoref|eqref|pageref|label|input|include|"
    r"includegraphics|caption|footnote|bibitem|url|href)\*?"
    r"(?:\[[^\]]*\])?(?:\{[^}]*\})*"
)
_OTHER_LATEX_CMD_RE = re.compile(
    r"\\[a-zA-Z]+\*?(?:\[[^\]]*\])?(?:\{[^}]*\})*"
)


def strip_latex_for_word_count(text: str) -> str:
    """Three-pass strip:

      1. UNWRAP typographic commands recursively (\\textbf{X} → 'X', and
         \\textbf{\\emph{X}} → 'X' through the loop).
      2. DROP reference-style commands together with their arguments
         (\\citep{X}, \\ref{X}, \\label{X}, \\input{X}, \\includegraphics …).
      3. DROP any remaining LaTeX commands (with their args).

    Returns the resulting plain text suitable for word-count audits.
    """
    # 1. Unwrap typographic commands until fixed point (handles nesting)
    prev = None
    out = text
    while out != prev:
        prev = out
        out = _TEXT_BEARING_RE.sub(r"\1", out)
    # 2. Drop reference-style commands entirely
    out = _REFERENCE_CMDS_RE.sub(" ", out)
    # 3. Drop any remaining LaTeX commands
    out = _OTHER_LATEX_CMD_RE.sub(" ", out)
    return out
