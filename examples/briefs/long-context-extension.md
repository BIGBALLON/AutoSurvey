topic: Long-Context Extension Methods for Pretrained Language Models

How do we take a 4k–8k-context model and stretch it to 128k, 1M, or
"effectively unbounded" tokens? Over 2023–2026 the answers split into a
handful of families — positional-encoding interpolation, sparse /
sliding-window attention, KV-cache compression or eviction,
retrieval-in-context, recurrent / state-space hybrids, and training-free
prompt-level tricks — that have been competing and, increasingly,
converging into stacked production recipes. This survey does not catalogue
them paper-by-paper; it argues a contestable thesis about *where the real
wins now come from*, and forces every method onto a single comparison
frame so the reader can see who actually pays for whom.

## Organizing frame (the spine of the survey)

Reduce every method to which of three root constraints it attacks:

- **(R1) Positional extrapolation failure** — out-of-distribution
  rotation angles / unseen positions beyond the trained length.
- **(R2) Attention compute O(n²)** — prefill FLOPs and decode latency
  that scale with sequence length.
- **(R3) KV-cache memory O(n)** — the cache that dominates GPU memory at
  long context.

The taxonomy of families must be *derived* from this decomposition, not
asserted. For every method state which root constraint(s) it targets,
which it ignores, and what it trades away (quality, generality, training
cost, or hardware assumptions). A method that "extends context" by
silently sacrificing one root cause to fix another is the single most
important thing this survey should expose.

## Scope

**Include.** Any technique that extends the usable context window of a
*pretrained* decoder-only language model, at:

- pretraining time (e.g. RoPE frequency interpolation baked into a
  long-context pretraining stage),
- mid-training time (continued pretraining for length extension; YaRN /
  LongRoPE / LongRoPE2 / Self-Extend / Dual Chunk Attention recipes),
- or inference time (KV-cache eviction such as H2O / SnapKV / PyramidKV,
  KV quantization, retrieval-in-context such as Landmark Attention /
  Focused Transformer, sparse attention at decode such as StreamingLLM /
  NSA / MoBA, and prompt-level tricks).

Also in scope: the **inference-systems layer** that makes long context
shippable — FlashAttention, PagedAttention, chunked prefill, prefix
caching, Ring / Ulysses sequence parallelism, MLA, FP8 / KV-int
quantization, CPU/NVMe KV offload. These are treated as first-class
methods, not background.

Also in scope: linear-attention / state-space models *only* when used as
a **post-hoc drop-in or hybrid** layered onto a pretrained Transformer
(Jamba, MiniMax-01 lightning attention, Zamba-style hybrids).

**Exclude.**
- Encoder-only and encoder–decoder models (BERT, T5).
- Vision and multimodal long-context (video tokens, high-resolution
  images, interleaved image+text).
- Linear-attention papers that *replace* softmax attention from scratch
  (Mamba, RetNet, RWKV) — except the post-hoc / hybrid use above.
- Pure RAG papers where long-context handling is delegated entirely to an
  external retriever and the LM context stays short. (Retrieval that
  injects or recalls KV *inside* the model's own context window is in
  scope; an external vector DB feeding short prompts is not.)

## Sources

Conference papers (NeurIPS, ICLR, ICML, ACL, EMNLP), arXiv preprints, lab
tech reports, model cards, and influential blog posts from 2022 to 2026.
Closed and frontier models count when their long-context behaviour is
documented in a technical report or model card. Make sure the search
surfaces, by name, at least these anchor works and systems:

- Positional: RoPE, Position Interpolation (PI), NTK-aware / dynamic-NTK
  scaling, YaRN, LongRoPE / LongRoPE2, Self-Extend, ALiBi, CLEX, PoSE,
  Dual Chunk Attention (DCA).
- Attention / sparsity: Longformer, BigBird, StreamingLLM (attention
  sinks), LM-Infinite, NSA, MoBA, Ring Attention.
- KV compression / eviction: H2O, SnapKV, PyramidKV, Scissorhands,
  FastGen, KVQuant, KIVI, MLA (DeepSeek), Infini-attention, Activation
  Beacon, landmark / Focused-Transformer retrieval-in-context.
- Systems: FlashAttention(-2/3), PagedAttention / vLLM, chunked prefill,
  prefix caching, Ulysses / sequence parallelism.
- Hybrids: Jamba, MiniMax-01, Zamba.
- Frontier models (claimed windows): Claude 3 / 3.5, Gemini 1.5 / 2.x
  (1M–2M), GPT-4-turbo / GPT-4.1, Llama 4 Scout (10M), Qwen2.5-1M / Qwen3,
  Kimi, DeepSeek-V3.
- Evaluation: needle-in-a-haystack (NIAH), RULER, ∞Bench, LongBench /
  LongBench v2, "Lost in the Middle".

**Recency mandate — the frontier must be current.** This survey must reflect
the state of the art as of its writing (target the trailing 12–18 months
through the run date). The named models and methods above are a **floor, not
a ceiling**: actively search for and fold in anything newer, even when it is
documented only in a tech report, model card, or lab blog — frontier
long-context capability routinely appears there months before any indexed
paper. Concretely:

- Run at least one **recency-targeted query per family** (year-filtered to the
  last ~18 months, relevance-sorted — never date-sorted for discovery), and
  add the newest systems to the anchor-coverage gate so a stale corpus fails
  loudly rather than silently.
- Every family should carry at least one **2025–2026 entry**; if a family has
  genuinely gone quiet, say so explicitly — a dated family is itself a finding
  for the trajectory analysis.
- Prefer the latest canonical version of each line (e.g. LongRoPE2 over
  LongRoPE, Qwen3 over Qwen2.5, Gemini 2.x over 1.5) and note what changed.

## Dimensions to compare

For every method, populate these dimensions. The cross-cutting matrix
draws its columns from them; where possible, **normalise cells to a
common base model and benchmark** (e.g. Llama-2/3-7B at 128k on RULER)
and explicitly mark non-comparable or missing cells rather than importing
each paper's self-reported best.

1. **Family & root cause**: which of R1/R2/R3 it attacks, and the
   one-sentence mechanism. Families: positional-encoding interpolation,
   sparse / sliding-window attention, KV-cache compression or eviction,
   retrieval-in-context, recurrent / state-space hybrids, inference-systems
   engineering, or training-free prompt-level tricks.
2. **Reach — claimed vs effective**: maximum window *claimed*; and the
   *effective context length* — the longest length where RULER / ∞Bench
   accuracy stays above threshold. Report the gap; treat the claimed
   number as a marketing figure until the effective number confirms it.
3. **Adaptation cost**: training-free, finetune-only, continued
   pretraining, or full pretraining required — with the budget (training
   tokens / GPU-hours) when reported.
4. **Compute & systems profile at decode time**: prefill cost, per-token
   decode cost, KV-cache memory, and the inference-systems layer it
   assumes or provides (FlashAttention, PagedAttention, chunked prefill,
   prefix caching, sequence parallelism, MLA, KV quantization, offload).
5. **Quality at long context**: perplexity / accuracy degradation
   relative to a short-context baseline, on which benchmarks, with exact
   numbers.
6. **Mechanism-level "why"**: the causal reason the method works *and* the
   causal reason its predecessor failed — e.g. OOD rotation angles in
   high-frequency RoPE dimensions, per-dimension wavelength treatment in
   YaRN, softmax attention-entropy growth with length, attention-sink
   tokens, U-shaped position bias. Extract the explanation, not just the
   name of the fix.
7. **Failure modes**: where it breaks (lost-in-the-middle, retrieval
   collapse, attention-sink dependence, repeating-token / NIAH-too-easy
   ablations, degradation under aggregation or multi-hop tracing) — and
   which failure the paper concedes versus fixes.
8. **Evaluation validity**: what the benchmark it reports on actually
   measures, why NIAH saturates and RULER / ∞Bench / LongBench-v2 were
   introduced, contamination risk, and whether the reported result is
   comparable or cherry-picked.
9. **Composability**: does it stack with other families, or does it
   assume exclusive control of the attention / KV path? Which production
   recipe (if any) ships it.

## Per-paper extraction

For each work, pull:

- Method name and one-sentence mechanism, tagged with the root cause
  (R1/R2/R3) it attacks.
- Base model(s) it was demonstrated on.
- Reported max context (claimed) and effective context length, with the
  exact benchmark numbers (RULER, NIAH, ∞Bench, LongBench) — the exact
  number, not a paraphrase.
- Adaptation budget: training tokens / GPU-hours, or "training-free".
- Inference cost (FLOPs or wall-clock per token at long context, KV-cache
  memory) when reported.
- Mechanism-level "why it works / why the predecessor failed".
- The specific long-context failure mode the paper concedes or fixes.
- Whether it composes with other techniques, and which shipped system (if
  any) uses it.

## Output

A literature-review-style survey whose length follows the scope above —
write each section at full depth and let the total land where the material
does (do not pad or truncate to hit a page target; the reference benchmark
is ~45 pp). Organise it around a **contestable, falsifiable thesis** the
agent proposes during `/survey-thesis`. Candidate seeds (pick and sharpen,
do not hedge):

- "Advertised context windows are a marketing number; effective context
  is bottlenecked by attention pattern and long-data scarcity, not by
  positional encoding, and it plateaus well below the claimed window."
- "Positional interpolation has plateaued; the remaining wins come from KV
  management and inference-systems engineering."
- "Training-free methods keep matching trained ones because the
  bottleneck is the attention pattern, not the parameters."

Whichever thesis is chosen, the survey must **steelman the opposite**
(e.g. that positional interpolation plus scale will close the gap) before
rejecting it.

Body sections must include:

- A **cross-cutting comparison matrix** (rows = the 12–20 most
  influential methods; columns = the dimensions above), normalised to a
  common base model / benchmark where possible.
- A **production-recipes** section that reverse-engineers how frontier
  systems *stack* techniques (e.g. GQA + RoPE-scaling + two-stage
  long-context training + synthetic needle data + chunked prefill +
  prefix caching), using Qwen2.5-1M, Llama 4 Scout, Gemini 2.x, Kimi, and
  DeepSeek (MLA / NSA) as case studies — not just isolated papers.
- An **evaluation-methodology** discussion that critiques the benchmarks
  themselves (NIAH saturation, RULER subtasks, ∞Bench, LongBench v2,
  contamination) rather than treating their numbers as ground truth.
- A **timeline figure** (4k → 200k → 1M → 10M, 2022–2026, named models)
  and an explicit **convergence analysis**: from pure positional-encoding
  extrapolation toward hybrid attention + KV / systems stacks.
- An **Open Problems** section paired 1:1 with **Future Directions**.
- A re-framing conclusion — not a bullet-list summary — that restates what
  the three-root-cause frame predicts about the next 12–24 months.
