"""Chunking, three ways, measured by how many tokens each produces.

Why this is not decoration: chunk size decides both retrieval quality and embedding cost. A
strategy that doubles the token count for no recall gain is a cost, and the cost is countable.

    fixed       N words per chunk, no regard for sentence boundaries
    recursive   paragraph -> sentence -> word, so a chunk rarely ends mid-sentence
    semantic    consecutive sentences grouped while they stay similar to the group centroid

The semantic strategy needs an embedding, so it imports one. That dependency is real and is
reported: semantic chunking costs an embedding pass over every sentence before it can chunk.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import numpy as np

from llm.embed import embedder

SENTENCE = re.compile(r"(?<=[.!?])\s+")


@dataclass(frozen=True)
class Chunk:
    chunk_id: str
    doc_id: str
    text: str
    strategy: str
    tokens: int


def _tokens(text: str) -> int:
    """Whitespace tokens. Not a real tokenizer, and labelled as such in the report."""
    return len(text.split())


def fixed(doc_id: str, text: str, size: int = 40) -> list[Chunk]:
    words = text.split()
    chunks = []
    for index in range(0, len(words), size):
        body = " ".join(words[index : index + size])
        chunks.append(Chunk(f"{doc_id}-f{index // size}", doc_id, body, "fixed", _tokens(body)))
    return chunks


def recursive(doc_id: str, text: str, max_words: int = 60) -> list[Chunk]:
    """Split on paragraph, then sentence, then word — never mid-sentence if a sentence fits."""
    chunks: list[Chunk] = []
    current: list[str] = []
    current_words = 0

    def flush() -> None:
        nonlocal current, current_words
        if current:
            body = " ".join(current).strip()
            chunks.append(Chunk(f"{doc_id}-r{len(chunks)}", doc_id, body, "recursive", _tokens(body)))
            current, current_words = [], 0

    for paragraph in text.split("\n\n"):
        for sentence in SENTENCE.split(paragraph.strip()):
            words = sentence.split()
            if current_words + len(words) > max_words and current:
                flush()
            if len(words) > max_words:
                # A single sentence longer than the budget still has to be split somewhere.
                for index in range(0, len(words), max_words):
                    body = " ".join(words[index : index + max_words])
                    chunks.append(Chunk(f"{doc_id}-r{len(chunks)}", doc_id, body, "recursive", _tokens(body)))
                continue
            current.extend(words)
            current_words += len(words)
    flush()
    return chunks


def semantic(doc_id: str, text: str, threshold: float = 0.55) -> list[Chunk]:
    """Group consecutive sentences while the group stays coherent.

    Coherence is cosine similarity between a sentence and the running centroid of its group. A
    sentence that drifts below the threshold starts a new group. The threshold is a parameter, not
    a truth: it is reported so a reader can see what produced the chunk count.
    """
    sentences = [sentence.strip() for sentence in SENTENCE.split(text) if sentence.strip()]
    if not sentences:
        return []

    # The embedder is fitted on this document's own sentences, not on the corpus: semantic chunking
    # happens before any corpus index exists, so there is nothing to fit on yet. Fitting per
    # document is cheap and keeps the strategy self-contained — the alternative was a global
    # two-pass pipeline that made chunking depend on indexing, which is backwards.
    model = embedder().fit(sentences)
    vectors = model.transform(sentences)

    groups: list[list[int]] = [[0]]
    centroid = vectors[0].copy()
    for index in range(1, len(sentences)):
        similarity = float(np.dot(vectors[index], centroid) / (np.linalg.norm(centroid) or 1.0))
        if similarity < threshold:
            groups.append([index])
            centroid = vectors[index].copy()
        else:
            groups[-1].append(index)
            centroid = centroid + vectors[index]

    chunks = []
    for group_index, group in enumerate(groups):
        body = " ".join(sentences[index] for index in group)
        chunks.append(Chunk(f"{doc_id}-s{group_index}", doc_id, body, "semantic", _tokens(body)))
    return chunks


STRATEGIES = {"fixed": fixed, "recursive": recursive, "semantic": semantic}


def chunk_corpus(docs: list[dict], strategy: str) -> list[Chunk]:
    chunker = STRATEGIES[strategy]
    chunks: list[Chunk] = []
    for doc in docs:
        chunks.extend(chunker(doc["doc_id"], doc["text"]))
    return chunks
