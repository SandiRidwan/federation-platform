"""Unit tests for the parts that must be right without Docker.

Everything in this file runs with no container, no network and no Azurite. That is deliberate: the
parity checks in the repository need Trino and Druid, so if the logic underneath them were only
tested through those services, a broken parser would look like an infrastructure problem.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from azure_layer.tabular.model import Unsupported, translate
from federation.presto.dialect import transpile_check
from ingestion.generate import build
from llm.chunking import fixed, recursive
from llm.index import BM25, tokenize
from llm.labeling.kappa import cohen_kappa, synthetic_annotators
from llm.preference import _decoy, build_pairs

# ------------------------------------------------------------------ generator


def test_generation_is_deterministic() -> None:
    first = build()
    second = build()
    assert first.totals == second.totals
    assert first.products[:5] == second.products[:5]
    assert first.sales[:5] == second.sales[:5]


def test_money_is_integer_cents() -> None:
    dataset = build()
    assert all(isinstance(product["price_cents"], int) for product in dataset.products)
    assert all(isinstance(sale["amount_cents"], int) for sale in dataset.sales)


def test_supplier_revenue_is_derived_from_the_same_rows() -> None:
    """The join expected value must equal what the engines will compute, not a second opinion."""
    dataset = build()
    supplier_of = {product["product_id"]: product["supplier"] for product in dataset.products}
    recomputed: dict[str, int] = {}
    for sale in dataset.sales:
        supplier = supplier_of[sale["product_id"]]
        recomputed[supplier] = recomputed.get(supplier, 0) + sale["amount_cents"]
    assert recomputed == dataset.totals["revenue_cents_by_supplier"]


# ------------------------------------------------------------------ chunking


def test_fixed_chunking_covers_every_word() -> None:
    text = " ".join(f"word{i}" for i in range(100))
    chunks = fixed("doc", text, size=30)
    recovered = " ".join(chunk.text for chunk in chunks).split()
    assert recovered == text.split()


def test_recursive_chunking_respects_the_budget() -> None:
    text = ". ".join("this is a sentence with a handful of words" for _ in range(20)) + "."
    chunks = recursive("doc", text, max_words=20)
    assert chunks, "recursive chunking produced nothing"
    assert all(chunk.tokens <= 20 for chunk in chunks), [chunk.tokens for chunk in chunks]


def test_recursive_chunking_splits_an_oversized_sentence() -> None:
    """A single sentence longer than the budget must still be split, not dropped."""
    text = " ".join(f"w{i}" for i in range(120)) + "."
    chunks = recursive("doc", text, max_words=25)
    assert len(chunks) >= 4
    assert all(chunk.tokens <= 25 for chunk in chunks)


# ------------------------------------------------------------------ BM25


def test_tokenizer_strips_punctuation_but_keeps_hyphenated_codes() -> None:
    """The bug that made BM25 score 0.004 on every SKU question.

    `split()` produced `x-1001?` from a question and `x-1001,` from the document, so the tokens
    never matched. A hyphen inside a code is part of the code; punctuation at its edge is not.
    """
    assert tokenize("Which warehouse holds X-1001?") == ["which", "warehouse", "holds", "x-1001"]
    assert tokenize("Product X-1001 is held.") == ["product", "x-1001", "is", "held"]


def test_bm25_prefers_the_chunk_with_the_rare_term() -> None:
    from llm.chunking import Chunk

    chunks = [
        Chunk("a", "doc-a", "generic text about warehouses and stock", "fixed", 7),
        Chunk("b", "doc-b", "the item X-9999 sits in warehouse WH-42", "fixed", 8),
        Chunk("c", "doc-c", "another generic line about stock levels", "fixed", 6),
    ]
    bm25 = BM25(chunks)
    best = bm25.search("which warehouse holds X-9999?", k=1)
    assert chunks[best[0][0]].doc_id == "doc-b"


def test_bm25_scores_are_finite() -> None:
    from llm.chunking import Chunk

    bm25 = BM25([Chunk("a", "d", "alpha beta", "fixed", 2), Chunk("b", "d2", "gamma delta", "fixed", 2)])
    scores = bm25.scores("alpha")
    assert all(math.isfinite(value) for value in scores)


# ------------------------------------------------------------------ kappa


def test_kappa_is_one_when_annotators_agree_perfectly() -> None:
    first, second = synthetic_annotators(100, 0.0)
    assert cohen_kappa(first, second).kappa == 1.0


def test_kappa_falls_as_planted_disagreement_rises() -> None:
    values = [cohen_kappa(*synthetic_annotators(200, rate)).kappa for rate in (0.0, 0.1, 0.2, 0.4)]
    assert all(values[index] > values[index + 1] for index in range(len(values) - 1)), values


def test_observed_agreement_matches_the_planted_rate() -> None:
    for rate in (0.1, 0.3):
        first, second = synthetic_annotators(100, rate)
        result = cohen_kappa(first, second)
        assert abs(result.observed_agreement - (1 - rate)) < 1e-9


# ------------------------------------------------------------------ DAX


def test_dax_sum_becomes_sql() -> None:
    sql = translate("SUM(Sales[amount_cents])")
    assert "SUM(amount_cents)" in sql and "FROM sales" in sql


def test_dax_calculate_carries_the_filter() -> None:
    """The bug that made `Books Revenue` return the unfiltered total."""
    sql = translate('CALCULATE([Total Revenue], Sales[category] = "books")')
    assert "WHERE sales.category = 'books'" in sql


def test_dax_divide_guards_against_integer_division() -> None:
    assert "* 1.0 /" in translate("DIVIDE([Total Revenue], [Total Events])")


def test_dax_unknown_function_is_refused_by_name() -> None:
    with pytest.raises(Unsupported, match="cannot translate"):
        translate("RANKX(Sales, Sales[amount_cents])")


def test_dax_unknown_table_is_refused() -> None:
    with pytest.raises(Unsupported, match="unknown table"):
        translate("SUM(Orders[amount])")


# ------------------------------------------------------------------ Presto dialect


def test_presto_dialect_accepts_the_trino_query() -> None:
    result = transpile_check("SELECT category, sum(amount_cents) AS revenue FROM sales GROUP BY category")
    assert result["ok"], result


# ------------------------------------------------------------------ preference


def test_decoy_never_equals_the_fact() -> None:
    for fact in ("lead time of 3 days", "annual storage fee of 1237 cents", "restock interval of 4 hours"):
        assert _decoy(fact) != fact


def test_preference_pairs_are_not_identical() -> None:
    """The bug that made the ranker's accuracy meaningless: for paraphrase questions the decoy
    replaced a warehouse code those questions never mention, so chosen == rejected."""
    for pair in build_pairs():
        assert pair.chosen != pair.rejected, pair.question
        assert pair.fact, pair.question


# ------------------------------------------------------------------ embedding


def test_embedder_is_deterministic() -> None:
    from llm.embed import embedder

    texts = ["alpha beta gamma", "delta epsilon zeta", "alpha delta theta"]
    first = embedder().fit(texts).transform(texts)
    second = embedder().fit(texts).transform(texts)
    assert np.allclose(first, second)
