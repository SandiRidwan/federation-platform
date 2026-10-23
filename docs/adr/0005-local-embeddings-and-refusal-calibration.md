# ADR 0005 — A local LSA embedding, a BM25 baseline, and thresholds calibrated off the test set

**Status:** accepted · 2026-10-23

## Context

The LLM data layer has to run with no API key, no account and no network. The claims worth making
are comparative: does vector retrieval beat BM25, by how much, and where does it lose?

Downloading a multi-hundred-megabyte neural model would make the repository unrunnable offline, and
would make the comparison depend on the model rather than on the retrieval idea.

## Decision

**Embedding: TF-IDF (word 1-2 grams, character n-grams) reduced with TruncatedSVD — LSA.** It is
trained on the corpus, deterministic (fixed seed, fixed vocabulary), and it is called LSA, not "a
semantic embedding model". The honest limitation is in the module: it captures co-occurrence, so it
handles paraphrases that share *some* vocabulary and fails on ones that share none.

**Baseline: BM25, written out rather than imported.** The comparison depends on knowing exactly
what it does: term-frequency saturation, length normalisation, IDF that rewards rare terms. A
library would hide the reason `WH-7` behaves differently from "arrival time".

**Chunking: fixed, recursive, semantic**, all three measured on the same corpus, because chunking
changes token cost and recall and pretending otherwise is how a retrieval benchmark turns into a
demo.

**Refusal: per-method thresholds, calibrated on held-out questions.**

## Why per-method, and why held out

The first version used one cosine threshold (0.15) for both methods and failed: BM25 scores are
unbounded (5.4 for a good hit, 0.02 for an off-topic question) and cosine similarities are not
(0.57 vs 0.47 on this corpus). One constant either refuses everything the vector side returns or
nothing. The first report showed exactly that: BM25 refused 5/5 unanswerable questions, the vector
side refused 0/5.

The fix is not a second magic number. It is a **separate calibration set** — 80 answerable and 10
unanswerable questions with different phrasings and different topics, written into the corpus by
the generator and never scored. The rule, stated before looking at the eval set: put the threshold
halfway between the worst answerable score and the best unanswerable score; if the distributions
overlap, say `separable: false` and use the midpoint of the means.

Calibration found a real gap for both methods (BM25: 4.83 vs 1.11; vector: 0.586 vs 0.528), so the
eval-set numbers mean something:

| Method | Answered (k=1) | Refused (of 5) | Threshold |
|---|---|---|---|
| BM25 | 160/240 (0.667) | 5/5 | 2.967 |
| Vector (LSA) | 160/240 (0.667) | 5/5 | 0.557 |

A threshold picked on the five eval questions would have produced a better-looking number and a
worthless one. The report carries `calibration_set_is_disjoint_from_eval_set` as a check, so the
shortcut cannot be taken later without failing `make verify`.

## Consequences

* On this corpus the vector method does **not** beat BM25 overall: both answer 160/240, both are
  perfect on lexical questions, and both answer 40/120 paraphrase questions. The retrieval
  benchmark shows where the methods differ (BM25 `recall@5` = 1.0 on fixed chunking, vector 0.5;
  fusion loses to BM25 on that strategy). That is reported, not smoothed.
* `recall@5` is judged against the generator's ground truth: the chunk must come from the document
  that states the fact **and** contain the fact. A hit on the right document with the wrong chunk
  does not count.
* Preference pairs + an offline logistic ranker are **not** RLHF. There is no policy, no rollout
  loop and no reward model training. The report has a `not_claiming_rlhf` check so the word cannot
  drift into the README later.
* `spacy` is not installed; NER is rule-based (regex + gazetteer) and scored against the
  generator's entity table, not against its own output.
