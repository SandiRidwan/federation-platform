"""The eval harness: answerable questions that must be answered, unanswerable ones that must be refused.

Same shape as the agent eval in the platform, for the same reason: an evaluation that only tests
the happy path cannot tell a correct system from one that answers everything confidently.

    answerable    the retrieved chunk must contain the fact the question asks for
    unanswerable  nothing in the corpus covers it, and the harness must refuse

The refusal rule is a threshold on the top-1 retrieval score, and **both thresholds are in the
report**, along with the calibration questions they came from.

One threshold for both methods was the first attempt and it was wrong: a BM25 score of 5.4 and a
cosine of 0.57 are not on one scale, so a single constant either refuses everything the vector
retriever returns or nothing. The fix is per-method thresholds, calibrated on a **held-out set of
questions that is never scored** — the calibration phrasings in `llm.corpus`. Calibrating on the
eval questions would be fitting the test to itself, and the accuracy would mean nothing.

    python -m llm.eval.harness
"""

from __future__ import annotations

import json
from dataclasses import dataclass

import numpy as np

from llm.chunking import chunk_corpus
from llm.embed import embedder
from llm.index import BM25, VectorIndex
from tools.common.config import get_settings

REPORT = "quality/reports/llm_eval.json"
METHODS = ("bm25", "vector")


@dataclass
class Index:
    chunks: list
    bm25: BM25
    vectors: VectorIndex
    model: object

    @classmethod
    def build(cls, docs: list[dict], strategy: str = "recursive") -> "Index":
        chunks = chunk_corpus(docs, strategy)
        model = embedder().fit([chunk.text for chunk in chunks])
        embeddings = model.transform([chunk.text for chunk in chunks])
        return cls(chunks=chunks, bm25=BM25(chunks), vectors=VectorIndex.build(chunks, embeddings), model=model)


def load_corpus() -> list[dict]:
    settings = get_settings()
    corpus_dir = settings.root / "data" / "corpus"
    docs = []
    for path in sorted(corpus_dir.glob("doc-*.txt")):
        docs.append({"doc_id": path.stem, "text": path.read_text(encoding="utf-8")})
    return docs


def load_questions() -> tuple[list[dict], list[dict], dict]:
    settings = get_settings()
    payload = json.loads((settings.root / "data" / "corpus" / "questions.json").read_text(encoding="utf-8"))
    return payload["answerable"], payload["unanswerable"], payload["calibration"]


def retrieve(index: Index, question: str, method: str = "vector", k: int = 5) -> list[dict]:
    if method == "bm25":
        return [
            {"chunk": index.chunks[position], "score": score}
            for position, score in index.bm25.search(question, k)
        ]
    vector = index.model.transform([question])[0]
    return [
        {"chunk": index.chunks[position], "score": score}
        for position, score in index.vectors.search(vector, k)
    ]


def top_score(index: Index, question: str, method: str) -> float:
    hits = retrieve(index, question, method, k=1)
    return float(hits[0]["score"]) if hits else 0.0


def calibrate(index: Index, calibration: dict) -> dict:
    """Pick one threshold per method, from held-out questions only.

    The rule, stated before looking at the eval set: put the threshold halfway between the worst
    answerable score and the best unanswerable score. If the two distributions overlap there is no
    gap to split, so the rule falls back to the midpoint of the two means and the report says
    `separable: false` — a threshold fitted inside an overlap is a coin flip dressed up as a rule.
    """
    thresholds = {}
    for method in METHODS:
        positive = [top_score(index, question["text"], method) for question in calibration["answerable"]]
        negative = [top_score(index, question["text"], method) for question in calibration["unanswerable"]]
        worst_answerable = min(positive)
        best_unanswerable = max(negative)
        separable = worst_answerable > best_unanswerable
        if separable:
            threshold = (worst_answerable + best_unanswerable) / 2
        else:
            threshold = (float(np.mean(positive)) + float(np.mean(negative))) / 2
        thresholds[method] = {
            "threshold": round(threshold, 6),
            "calibration_answerable": len(positive),
            "calibration_unanswerable": len(negative),
            "worst_answerable_top_score": round(worst_answerable, 6),
            "best_unanswerable_top_score": round(best_unanswerable, 6),
            "separable": separable,
        }
    return thresholds


def main() -> int:
    settings = get_settings()
    docs = load_corpus()
    answerable, unanswerable, calibration_questions = load_questions()
    if not docs:
        print("no corpus; run `python -m llm.corpus` first")
        return 1

    index = Index.build(docs)
    calibration = calibrate(index, calibration_questions)

    per_method = {}
    for method in METHODS:
        threshold = calibration[method]["threshold"]
        answered = 0
        by_family = {"lexical": [0, 0], "paraphrase": [0, 0]}
        for question in answerable:
            hits = retrieve(index, question["text"], method, k=1)
            top = hits[0]["chunk"] if hits else None
            # The fact must be in the retrieved chunk. Not "the right document was somewhere in
            # the top five": at k=1 there is one answer and it either contains it or does not.
            correct = bool(top and question["fact"] and question["fact"] in top.text)
            answered += int(correct)
            by_family[question["family"]][0] += int(correct)
            by_family[question["family"]][1] += 1

        # Refusals: a question the corpus cannot answer must score below the calibrated threshold.
        refused = 0
        unanswerable_scores = []
        for question in unanswerable:
            score = top_score(index, question["text"], method)
            unanswerable_scores.append(round(score, 6))
            refused += int(score < threshold)

        per_method[method] = {
            "answered": answered,
            "answerable": len(answerable),
            "accuracy": round(answered / len(answerable), 4),
            "by_family": {
                family: {"correct": counts[0], "of": counts[1], "accuracy": round(counts[0] / max(1, counts[1]), 4)}
                for family, counts in by_family.items()
            },
            "refused": refused,
            "unanswerable": len(unanswerable),
            "refusal_accuracy": round(refused / len(unanswerable), 4),
            "refusal_threshold": threshold,
            "unanswerable_top_scores": unanswerable_scores,
        }

    calibration_texts = {
        question["text"] for question in calibration_questions["answerable"] + calibration_questions["unanswerable"]
    }
    eval_texts = {question["text"] for question in answerable + unanswerable}

    checks = {
        "bm25_answers_some": per_method["bm25"]["answered"] > 0,
        "vector_answers_some": per_method["vector"]["answered"] > 0,
        "both_refuse_unanswerable": all(
            per_method[method]["refused"] == len(unanswerable) for method in METHODS
        ),
        "refusal_threshold_is_documented": all(
            calibration[method]["threshold"] > 0 for method in METHODS
        ),
        # The point of the calibration split: the thresholds must not have been chosen on the
        # questions they are then scored against.
        "calibration_set_is_disjoint_from_eval_set": not (calibration_texts & eval_texts),
    }

    report = {
        "corpus_docs": len(docs),
        "chunks": len(index.chunks),
        "chunk_strategy": "recursive",
        "refusal_rule": (
            "per method: refuse when the top-1 score is below a threshold calibrated on held-out "
            "questions (halfway between the worst answerable and the best unanswerable score)"
        ),
        "calibration": calibration,
        "methods": per_method,
        "checks": checks,
        "result": "PASS" if all(checks.values()) else "FAIL",
        "note": (
            "Answerable accuracy is measured at k=1 against the fact the question asks for. "
            "Thresholds are calibrated on calibration questions that are never scored, and the "
            "calibration numbers are reported next to the eval numbers so both can be checked."
        ),
    }

    path = settings.root / REPORT
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    for method, item in per_method.items():
        print(
            f"  {method:<7} answered {item['answered']:>3}/{item['answerable']} "
            f"(lex {item['by_family']['lexical']['correct']}/{item['by_family']['lexical']['of']}, "
            f"par {item['by_family']['paraphrase']['correct']}/{item['by_family']['paraphrase']['of']})  "
            f"refused {item['refused']}/{item['unanswerable']}  "
            f"threshold {item['refusal_threshold']}"
        )
    print()
    for name, ok in checks.items():
        print(f"  [{'OK  ' if ok else 'FAIL'}] {name}")
    print(f"RESULT: {report['result']}")
    return 0 if report["result"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
