# Brief Contract — `brief.parsed.json` Schema

Canonical structure of the JSON that `refine_brief.py` emits from the
free-form `brief.md`. Every downstream stage reads this file.

## Top-level schema

```json
{
  "topic": "string",
  "scope": {
    "include": ["string", ...],
    "exclude": ["string", ...]
  },
  "sources": {
    "categories": ["arxiv" | "semantic_scholar" | "openalex" | "acl_anthology" |
                    "pubmed" | "tech_reports" | "blogs" |
                    "model_cards" | "github_readmes" | "websites", ...],
    "year_range": [int, int],
    "github_repos": ["url", ...], // optional
    "model_cards": ["url", ...] // optional
  },
  "dimensions": [
    {"name": "string", "description": "string"},
    ...
  ],
  "style": ["string", ...],
  "configuration": {
    "trends_section": "include" | "skip"
  },
  "_uncertainties": ["string", ...] // optional; LLM-flagged low-confidence inferences
}
```

## Field semantics

| Field | Required | Notes |
|---|---|---|
| `topic` | yes | One-line subject; refine_brief fails if absent |
| `scope.include` | yes | May be empty `[]`; pipeline interprets as "include everything topic-relevant" |
| `scope.exclude` | yes | Drives the LLM-as-filter at survey-search step |
| `sources.categories` | yes | Defaults to `["arxiv","semantic_scholar","openalex","tech_reports","blogs"]` if not specified in brief |
| `sources.year_range` | yes | Defaults to `[current_year - 5, current_year]` |
| `sources.{github_repos,model_cards}` | no | Empty `[]` allowed |
| `dimensions` | yes | Length 3–12; refine_brief fails or proposes additions if <3 |
| `style` | yes | At least one bullet; auto-includes synthetic forward-looking rule unless brief opts out |
| `configuration.trends_section` | no | Default `"include"`; `"skip"` opts out of the Trends & Trajectories body section and the auto-appended forward-looking style rule |
| `_uncertainties` | no | Surfaces LLM's low-confidence guesses for user review |

## Validation rules (refine_brief.py)

1. `topic` non-empty string.
2. `scope.include` and `scope.exclude` are arrays (may be empty).
3. `sources.categories` non-empty array of valid enum values.
4. `sources.year_range` is `[int, int]` with start ≤ end and end ≤ current_year + 1.
5. `sources.{github_repos,model_cards}` if present, each entry is a well-formed URL.
6. `dimensions` is array of length 3–12; each has `name` (non-empty str) and `description` (str).
7. `style` is non-empty array of strings.
8. `configuration.trends_section` defaults to `"include"`; only `"include"` or `"skip"` are valid values.

## Default Style augmentation

If `brief.style` does not contain `no-forward-looking` and
`configuration.trends_section != "skip"`, the parser appends:

> `"forward-looking insight: identify trends, predict trajectories, surface gaps the field is heading toward"`

This is stored as a regular `style` entry; consumers do not distinguish.

## Compatibility

Consumers ignore unknown fields rather than fail; removing or renaming
a field is a breaking change.

## See also

- `skills/shared-references/claims-contract.md` — the thesis-driven
  `cards.jsonl` / `claims_cache.jsonl` schemas.
