"""Two retrieval methods over the same chunks: BM25 and a FAISS vector index.

BM25 is written out rather than imported, because the whole comparison depends on knowing exactly
what it does: term frequency saturated by `k1`, normalised by document length with `b`, and an
inverse document frequency that rewards rare terms. That is why it should win on a query containing
`WH-7` and lose on a query that shares no vocabulary at all.

The vector side is FAISS. Two indexes are kept apart:

    IndexFlatIP      exact search, the ground truth for "what the vectors say"
    IndexIVFFlat     approximate search, nprobe tunable — the version you would ship

Reporting both is what stops "we use FAISS" from hiding the recall the approximation costs.

    python -m llm.index --bench
"""

from __future__ import annotations

import math
import re
import time
from collections import Counter
from dataclasses import dataclass

import faiss
import numpy as np

from llm.chunking import Chunk

# A real tokenizer, not `split()`.
#
# The first version used `text.lower().split()`, so a question ending in "?" produced the token
# `x-1001?` while the document produced `x-1001,` — neither matched the other, and BM25 scored
# 0.004 for every document on the questions that name a SKU. The bug was invisible until recall was
# measured: search still returned rows, they were just the wrong ones.
TOKEN = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")


def tokenize(text: str) -> list[str]:
    return TOKEN.findall(text.lower())


class BM25:
    """Okapi BM25 over word tokens, case-folded, punctuation stripped."""

    def __init__(self, chunks: list[Chunk], k1: float = 1.5, b: float = 0.75) -> None:
        self.chunks = chunks
        self.k1 = k1
        self.b = b
        self.documents = [tokenize(chunk.text) for chunk in chunks]
        self.lengths = np.array([len(document) for document in self.documents], dtype="float64")
        self.average_length = float(self.lengths.mean()) if len(self.lengths) else 0.0

        self.frequencies = [Counter(document) for document in self.documents]
        document_frequency: Counter = Counter()
        for frequency in self.frequencies:
            document_frequency.update(frequency.keys())

        total = len(self.documents)
        self.idf = {
            term: math.log(1 + (total - count + 0.5) / (count + 0.5))
            for term, count in document_frequency.items()
        }

    def scores(self, query: str) -> np.ndarray:
        terms = tokenize(query)
        scores = np.zeros(len(self.documents), dtype="float64")
        for index, frequency in enumerate(self.frequencies):
            length = self.lengths[index] or 1.0
            total = 0.0
            for term in terms:
                count = frequency.get(term, 0)
                if not count:
                    continue
                idf = self.idf.get(term, 0.0)
                length_norm = 1 - self.b + self.b * length / (self.average_length or 1.0)
                total += idf * (count * (self.k1 + 1)) / (count + self.k1 * length_norm)
            scores[index] = total
        return scores

    def search(self, query: str, k: int = 5) -> list[tuple[int, float]]:
        scores = self.scores(query)
        order = np.argsort(-scores)[:k]
        return [(int(index), float(scores[index])) for index in order]


@dataclass
class VectorIndex:
    embeddings: np.ndarray
    chunks: list[Chunk]
    exact: faiss.IndexFlatIP
    approximate: faiss.IndexIVFFlat | None

    @classmethod
    def build(cls, chunks: list[Chunk], embeddings: np.ndarray) -> "VectorIndex":
        matrix = np.ascontiguousarray(embeddings, dtype="float32")
        exact = faiss.IndexFlatIP(matrix.shape[1])
        exact.add(matrix)

        # IVF needs enough vectors to train its coarse quantizer. Below that, an approximate index
        # would be a lie about what was measured, so it is not built.
        approximate = None
        if matrix.shape[0] >= 64:
            quantizer = faiss.IndexFlatIP(matrix.shape[1])
            cells = max(1, int(math.sqrt(matrix.shape[0])))
            approximate = faiss.IndexIVFFlat(quantizer, matrix.shape[1], cells, faiss.METRIC_INNER_PRODUCT)
            approximate.train(matrix)
            approximate.add(matrix)
            approximate.nprobe = 4

        return cls(embeddings=matrix, chunks=chunks, exact=exact, approximate=approximate)

    def search(self, query_vector: np.ndarray, k: int = 5, approximate: bool = False) -> list[tuple[int, float]]:
        query = np.ascontiguousarray(query_vector.reshape(1, -1), dtype="float32")
        index = self.approximate if approximate and self.approximate is not None else self.exact
        scores, indices = index.search(query, k)
        return [(int(index_), float(score)) for index_, score in zip(indices[0], scores[0]) if index_ >= 0]


def timed_search(fn, *args, runs: int = 5, **kwargs) -> dict:
    timings = []
    result = None
    for index in range(runs):
        started = time.perf_counter()
        result = fn(*args, **kwargs)
        timings.append((time.perf_counter() - started) * 1000)
    warm = sorted(timings[1:]) or timings
    return {"p50_ms": round(float(np.median(warm)), 3), "cold_ms": round(timings[0], 3), "result": result}
