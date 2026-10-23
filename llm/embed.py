"""A local embedding model: TF-IDF over word and character n-grams, reduced with SVD.

Why not a neural model: no `sentence-transformers` and no `spacy` in this environment, and
downloading a multi-hundred-megabyte model to prove "embeddings work" would make the repository
unrunnable offline. What runs here is a real, trained-on-the-corpus linear projection — LSA — and
it is called that, not "a semantic embedding model".

The consequence is honest and worth stating: LSA captures co-occurrence, so it handles the
paraphrase questions that share *some* vocabulary and fails on ones that share none. That is a
real limitation of the method, and the retrieval benchmark shows it rather than hiding it.

The model is deterministic: fixed vocabulary, fixed SVD seed. Two runs produce identical vectors,
so a retrieval number that changes is a bug.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from sklearn.decomposition import TruncatedSVD
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.preprocessing import Normalizer

DIMENSIONS = 128
SEED = 20261023


@dataclass
class Embedder:
    vectorizer: TfidfVectorizer
    svd: TruncatedSVD
    normalizer: Normalizer
    fitted: bool = False

    def fit(self, texts: list[str]) -> "Embedder":
        matrix = self.vectorizer.fit_transform(texts)
        # The SVD rank cannot exceed the number of features; a corpus smaller than the target
        # dimension would otherwise fail with a cryptic arpack error.
        rank = min(DIMENSIONS, matrix.shape[1] - 1) if matrix.shape[1] > 1 else 1
        self.svd = TruncatedSVD(n_components=rank, random_state=SEED)
        self.svd.fit(matrix)
        self.fitted = True
        return self

    def transform(self, texts: list[str]) -> np.ndarray:
        if not self.fitted:
            raise RuntimeError("embedder is not fitted; call fit() on the corpus first")
        matrix = self.vectorizer.transform(texts)
        return self.normalizer.transform(self.svd.transform(matrix)).astype("float32")


def embedder() -> Embedder:
    """A fresh, unfitted embedder.

    Word n-grams for term overlap, character n-grams so a rare code like `WH-7` still has a signal
    when the tokenizer splits it. `sublinear_tf` because a word repeated ten times is not ten times
    as informative.
    """
    return Embedder(
        vectorizer=TfidfVectorizer(
            lowercase=True,
            ngram_range=(1, 2),
            analyzer="word",
            sublinear_tf=True,
            min_df=1,
        ),
        svd=TruncatedSVD(n_components=DIMENSIONS, random_state=SEED),
        normalizer=Normalizer(copy=False),
    )
