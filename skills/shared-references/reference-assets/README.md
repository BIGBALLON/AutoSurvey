# Reference assets

Verbatim artefacts derived from the benchmark survey
(Deli Chen et al., *From Copilots to Colleagues: A Survey of
Autonomous Research Agents*, 45 pp, NeurIPS-style). The audit suite
in `tools/audit_writing.py` is calibrated against this paper, and
these files are the worked examples the writer agent fetches when
the structural-template prompt directs it to.

The files are **byte-stable** — tests pin specific properties of
each asset, so when you change a file here you must update the
matching test (and vice-versa).

| File | Used by | Pins |
|---|---|---|
| `outline.example.json` | `survey-outline/SKILL.md` Step 2 | The 8-section / 27-subsection skeleton, the `cross_cutting_matrix` slot at `04e_feature_matrix`, and the 6×5 paired open/future lists. `tests/test_validate_outline.py::test_reference_outline_passes_strict_template` asserts that `validate_outline.py --strict-template` returns OK on this file. |
| `cross_cutting_matrix.example.tex` | `survey-outline/SKILL.md` Step 2; `survey-write/SKILL.md` Step 3.6 | Full-page `table*` skeleton with booktabs, `17 systems × 6 dimensions`, and the column conventions (Autonomy / Architecture / Domain / Tools / Self-Improve / OS) the benchmark uses. `tools/scaffold_cross_cutting_matrix.py` generates a real survey's matrix in this exact shape. |
| `intro_contributions.example.tex` | `survey-write/SKILL.md` Step 3 | The 4-item `\begin{enumerate}` block where each `\item` opens with a 2–4-word `\textbf{Bold Lead.}` and ends with `(\S\,N)`. Anchors structural-template invariant 8. |
| `../conclusion-template.md` | `survey-write/SKILL.md` Step 6 | The 641-word verbatim conclusion (opener + 5 bold-lead findings + Call to Action). Anchors invariant 7. |

## Provenance

The benchmark PDF (Deli Chen et al., *From Copilots to Colleagues: A
Survey of Autonomous Research Agents*) is **not redistributed with this
repo**. To re-verify a quote, obtain the PDF and extract its text with
`pdftotext -layout auto_research_survey.pdf auto_research_survey.txt`,
then quote from that extraction.

When the contract in
`shared-references/structural-template.md` evolves, regenerate the
relevant asset from the same source paragraph in the benchmark PDF
rather than hand-editing — the goal is to keep the asset
*reproducibly* benchmark-derived, not "close to benchmark plus our
opinions".

## What these are not

These are **structural** examples — section shape, enumerate shape,
bold-lead shape, table layout. They are not **content** examples for
any other survey topic. The cite_keys inside them
(`richards2023autogpt`, `yang2024sweagent`, etc.) belong to the
benchmark survey's closed paper pool; if your survey is on a
different topic, its closed-set cite_keys will be different. Copy
the *shape*, not the cite_keys.

## Adding a new reference asset

1. Find the structural feature in the benchmark PDF that the new
   asset codifies.
2. Quote the relevant region verbatim (escape LaTeX commands as
   needed for round-trip; do not paraphrase).
3. Add an audit signal in `tools/audit_writing.py` that fires when
   surveys deviate from the asset's structure.
4. Add a test that asserts the asset itself passes the audit signal
   (the asset must be its own regression bar).
5. Wire the SKILL prompt to the asset by path, not by inlining.

That fifth step is the one that keeps the prompt budget tight; the
agent reading the SKILL fetches the file when it needs it instead of
loading the example into every prompt by default.
