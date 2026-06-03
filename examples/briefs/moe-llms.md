topic: Mixture-of-Experts in Large Language Models

How did sparsely-activated Mixture-of-Experts (MoE) go from a scaling
curiosity to the default architecture of frontier open models (Mixtral,
DeepSeek-V3, Qwen-MoE, OLMoE, Llama 4)? This survey synthesises the MoE
design space for autoregressive LLMs over 2021-2026 and argues a
contestable thesis about where the real wins now come from.

## Scope

**Include.** Sparsely-activated MoE applied to autoregressive
(decoder-only) language models, at pretraining, mid-training, or
inference time: routing strategies, expert granularity and sharing,
load-balancing mechanisms, training stability/precision, and the
inference-systems layer that makes MoE servable (expert parallelism,
all-to-all, capacity factors, offloading).

**Exclude.**
- Dense (non-sparse) language models, except as baselines.
- Encoder-only / encoder-decoder MoE (Switch-T5 as historical context only).
- Vision / multimodal-only MoE (V-MoE, vision adapters).
- Non-LLM MoE (recommenders, classical ensembles).

## Sources

Conference papers (NeurIPS, ICLR, ICML, ACL, EMNLP), arXiv preprints, lab
tech reports, and model cards from 2021 to 2026. Frontier-model MoE
details count when documented in a technical report or model card. Make
sure the search surfaces, by name, at least: Switch Transformer, GShard,
GLaM, BASE Layers, Hash Layers, Expert Choice routing, ST-MoE, Mixtral,
DeepSeek-MoE, DeepSeek-V3, Qwen-MoE / Qwen2-MoE, OLMoE, JetMoE, Llama 4,
DBRX, Grok-1, fine-grained / shared-expert designs, auxiliary-loss-free
balancing, FP8 MoE training, and expert-parallel serving.

**Recency mandate — the frontier must be current.** This survey must reflect
the state of the art as of its writing (target the trailing 12–18 months
through the run date). The named systems above are a **floor, not a ceiling**:
actively search for and include any newer MoE model, routing/balancing method,
or serving system, even when documented only in a tech report, model card, or
blog — frontier MoE details land there before indexed papers. Concretely:

- Run at least one **recency-targeted query per lever** (routing, granularity /
  balancing, serving), year-filtered to the last ~18 months and
  relevance-sorted (never date-sorted for discovery); add the newest systems to
  the anchor-coverage gate so a stale corpus fails loudly.
- Every lever should carry at least one **2025–2026 entry**; a quiet lever is
  itself a finding for the trajectory analysis.
- Prefer the latest canonical version of each line (e.g. Qwen3-MoE over
  Qwen2-MoE, DeepSeek-V3 over V2) and note what changed.

## Dimensions to compare

For every method, populate these dimensions; the cross-cutting matrix
draws its columns from them, normalised to a common base where possible.

1. **Routing strategy**: token-choice vs expert-choice vs hash vs BASE;
   top-k value; the one-sentence mechanism.
2. **Expert granularity & sharing**: number of experts, fine-grained
   splitting, shared/always-on experts, activation ratio (active / total
   parameters).
3. **Load balancing**: auxiliary loss, z-loss, bias-based /
   auxiliary-loss-free balancing, capacity factor and token dropping.
4. **Training stability & precision**: instabilities and fixes (router
   z-loss, jitter), precision (BF16 / FP8), and the reported budget.
5. **Compute & systems profile**: total vs active parameters, expert
   parallelism / all-to-all cost, serving memory, and offloading.
6. **Quality at fixed budget**: benchmark numbers (MMLU, GSM8K,
   HumanEval) and quality-per-active-FLOP versus a dense baseline, with
   exact numbers.
7. **Failure modes**: routing collapse, expert under-utilisation,
   load imbalance, training instability, and which the paper concedes vs
   fixes.

## Per-paper extraction

For each work, pull: method name and one-sentence routing mechanism; base
model(s); total / active parameter counts and expert count + top-k;
balancing scheme; precision; exact quality benchmarks (MMLU / GSM8K /
HumanEval); training/serving cost when reported; and the specific failure
mode it concedes or fixes.

## Output

A literature-review survey whose length follows the scope above — write
each section at full depth and let the total land where the material does
(do not pad or truncate to hit a page target; the reference benchmark is
~45 pp). Organise it around a contestable, falsifiable thesis the agent
proposes (candidate seeds: "MoE's gains now
come from routing/granularity and balancing, not from raw expert count";
"auxiliary-loss-free balancing has made the load-balance problem a solved,
commoditized layer"; "the binding constraint on MoE is serving systems,
not modelling"). Steelman the opposite before rejecting it.

Body sections must include: a cross-cutting comparison matrix (12-20
influential MoE systems x the dimensions above, normalised); a
production-recipes section reverse-engineering how frontier MoE models
(DeepSeek-V3, Mixtral, Qwen-MoE, OLMoE, Llama 4) stack routing +
granularity + balancing + precision + serving; an evaluation-methodology
discussion (quality-per-active-FLOP, contamination, MoE-specific
pitfalls); a timeline figure (Switch -> GShard -> GLaM -> Mixtral ->
DeepSeek-V3 -> Llama 4) and a convergence analysis; an Open Problems
section paired 1:1 with Future Directions; and a re-framing conclusion.
