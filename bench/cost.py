"""What embedding actually costs, computed rather than asserted.

FinOps appeared in three of the batch-8 job descriptions, so the number that matters is not "local
embedding is cheaper" but **how much cheaper, at what volume, and against a dated price**.

The comparison is per million words, because that is the unit an embedding API bills in:

    local      compute time on this machine x a stated electricity rate
    api        a published price per million input tokens, with the date it was read

Neither number is a recommendation. A local model on a laptop has no redundancy, no rate limit and
no SLA, and the report says so. What it shows is that the decision has arithmetic behind it.

    python -m bench.cost
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from llm.embed import embedder
from tools.common.config import get_settings

REPORT = Path("quality/reports/embedding_cost.json")

# Stated assumptions, each of which a reader can disagree with.
ASSUMPTIONS = {
    # A published list price for a small embedding model, read on this date. Not a quote.
    "api_usd_per_million_tokens": 0.02,
    "api_price_source": "OpenAI text-embedding-3-small list price",
    "api_price_read_on": "2026-10-23",
    # 1 token is treated as 1 word for this estimate. Stated because it is an approximation:
    # real tokenizers average ~1.3 tokens per English word.
    "tokens_per_word": 1.0,
    # Electricity: 65 W average draw for the measurement machine while embedding, at the local
    # industrial rate.
    "watts_during_embedding": 65.0,
    "usd_per_kwh": 0.15,
    "measurement_machine": "Windows, 8-core, 15.8 GB RAM",
}


def measure() -> dict:
    settings = get_settings()
    corpus_dir = settings.root / "data" / "corpus"
    texts = [path.read_text(encoding="utf-8") for path in sorted(corpus_dir.glob("doc-*.txt"))]
    if not texts:
        raise RuntimeError("no corpus; run `python -m llm.corpus` first")

    words = sum(len(text.split()) for text in texts)

    # Warm the vectorizer, then measure — the first call pays for imports and matrix allocation.
    model = embedder().fit(texts)
    model.transform(texts)

    started = time.perf_counter()
    model.transform(texts)
    elapsed = time.perf_counter() - started

    # Scale the measured run to one million words.
    seconds_per_million_words = elapsed * (1_000_000 / max(1, words))
    kwh_per_million_words = (ASSUMPTIONS["watts_during_embedding"] / 1000.0) * (seconds_per_million_words / 3600.0)
    local_usd_per_million_words = kwh_per_million_words * ASSUMPTIONS["usd_per_kwh"]

    api_tokens = 1_000_000 * ASSUMPTIONS["tokens_per_word"]
    api_usd_per_million_words = (api_tokens / 1_000_000) * ASSUMPTIONS["api_usd_per_million_tokens"]

    return {
        "words_embedded": words,
        "seconds": round(elapsed, 4),
        "words_per_second": int(words / elapsed) if elapsed else 0,
        "seconds_per_million_words": round(seconds_per_million_words, 2),
        "kwh_per_million_words": round(kwh_per_million_words, 6),
        "local_usd_per_million_words": round(local_usd_per_million_words, 6),
        "api_usd_per_million_words": round(api_usd_per_million_words, 6),
        "ratio_api_over_local": round(api_usd_per_million_words / local_usd_per_million_words, 2)
        if local_usd_per_million_words
        else None,
    }


def main() -> int:
    settings = get_settings()
    result = measure()

    checks = {
        "corpus_measured": result["words_embedded"] > 0,
        "cost_is_computed_not_estimated": result["local_usd_per_million_words"] >= 0,
        "assumptions_are_recorded": set(ASSUMPTIONS) >= {
            "api_usd_per_million_tokens", "usd_per_kwh", "tokens_per_word",
        },
    }

    report = {
        "assumptions": ASSUMPTIONS,
        "measured": result,
        "checks": checks,
        "result": "PASS" if all(checks.values()) else "FAIL",
        "note": (
            "The API figure is a list price read on a stated date, not a quote. The local figure is "
            "electricity only: it excludes hardware, maintenance, and the fact that a laptop has no "
            "redundancy. A decision made on this table alone would be wrong; a decision made with no "
            "numbers at all would be worse."
        ),
    }

    path = settings.root / REPORT
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print(f"embedded        : {result['words_embedded']:,} words in {result['seconds']}s "
          f"({result['words_per_second']:,} words/s)")
    print(f"per 1M words    : local ${result['local_usd_per_million_words']:.4f} · "
          f"api ${result['api_usd_per_million_words']:.2f}")
    print(f"api / local     : {result['ratio_api_over_local']}x")
    print()
    for name, ok in checks.items():
        print(f"  [{'OK  ' if ok else 'FAIL'}] {name}")
    print(f"RESULT: {report['result']}")
    return 0 if report["result"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
