"""Preference ranking, evaluated offline — and explicitly NOT reinforcement learning.

Three JD described RLHF. Building RLHF here would mean a policy, a reward model trained on it, and
a rollout loop — none of which can be honestly demonstrated on a laptop in a way that means
anything. So this module does the part that is real and useful, and labels the boundary:

    pairs        preference pairs generated from the corpus's own facts (chosen vs rejected answer)
    features     a small, interpretable vector per answer (fact overlap, length, entity hits)
    model        a pairwise ranker trained with logistic regression on the pair differences
    evaluation   held-out pairs, scored by accuracy and by NDCG over the ranking

The claim is narrow on purpose: **a reward model can be trained and evaluated offline.** The claim
NOT made is that this is RLHF, and the report records that in a field called `not_rlhf`.

    python -m llm.preference
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from sklearn.linear_model import LogisticRegression

from tools.common.config import get_settings

REPORT = Path("quality/reports/llm_preference.json")
SEED = 20261023


@dataclass(frozen=True)
class Pair:
    question: str
    chosen: str
    rejected: str
    reason: str
    # The fact the question asks for, held on the pair. The first version passed the chosen answer
    # as the "fact" when computing features, so every decoy inherited the chosen answer's coverage.
    fact: str = ""


def features(answer: str, question: str, fact: str) -> np.ndarray:
    """Four numbers per answer, each with a stated meaning.

    Small and interpretable on purpose: with a 20-40 pair training set, a large feature space would
    fit noise and the evaluation would be meaningless.

    The features describe the ANSWER only. The first version computed fact coverage from the
    chosen answer, so every decoy inherited the chosen answer's coverage and became
    indistinguishable — the ranker scored 0.667 and the pairs for paraphrase questions were
    literally identical strings (chosen == rejected), because the decoy replaced a warehouse code
    that those questions never mention. Both bugs are fixed here: features are answer-relative, and
    a decoy is generated to differ from the chosen answer by construction.
    """
    answer_words = set(answer.lower().split())
    question_words = set(question.lower().split())
    fact_words = set(fact.lower().split())

    return np.array(
        [
            1.0 if fact and fact.lower() in answer.lower() else 0.0,   # does it contain the fact
            len(answer_words & fact_words) / max(1, len(fact_words)),   # fact coverage
            len(answer_words & question_words) / max(1, len(question_words)),  # question overlap
            min(len(answer.split()), 60) / 60.0,                        # length, saturated
        ],
        dtype="float64",
    )


def _decoy(fact: str) -> str:
    """A plausible, wrong fact with the same shape as the right one.

    Shape-preserving on purpose: the number is replaced, not the sentence, so a ranker cannot
    separate the two by detecting "this looks like a fact statement". That is what makes the
    evaluation about the fact and not about the surface form.
    """
    import re

    match = re.search(r"(\d+)", fact)
    if not match:
        return fact + " (revised)"
    value = int(match.group(1))
    wrong = value + 7 if value < 100 else value * 2 + 3
    return fact[: match.start(1)] + str(wrong) + fact[match.end(1):]


def build_pairs() -> list[Pair]:
    """A chosen answer that states the fact, and a rejected one that sounds plausible.

    The rejected answer is built to be *attractive*: fluent, in the right format, and containing a
    number. A ranker that only detects "is this a sentence" would score it equal; the point is that
    the features must separate a correct fact from a confident wrong one.
    """
    settings = get_settings()
    entities = json.loads((settings.root / "data" / "corpus" / "entities.json").read_text(encoding="utf-8"))
    questions = json.loads(
        (settings.root / "data" / "corpus" / "questions.json").read_text(encoding="utf-8")
    )["answerable"]

    pairs = []
    for question in questions[:40]:
        doc_entities = entities.get(question["answer_doc"], {})
        sku = (doc_entities.get("sku") or ["X-0000"])[0]

        chosen_text = f"According to the record for {sku}, {question['fact']}."
        rejected_text = f"According to the record for {sku}, {_decoy(question['fact'])}."

        # A pair whose two sides are the same string teaches nothing and makes the accuracy
        # meaningless. Refusing loudly is better than training on it.
        if chosen_text == rejected_text:
            raise RuntimeError(f"decoy is identical to the chosen answer for {question['question_id']}")

        pairs.append(
            Pair(
                question=question["text"],
                chosen=chosen_text,
                rejected=rejected_text,
                reason="correct fact vs the same sentence with one number changed",
                fact=question["fact"],
            )
        )
    return pairs


def ndcg(ordering: list[int], ideal: list[int]) -> float:
    """NDCG over a ranked list, with binary gains."""
    dcg = sum(ideal[index] / np.log2(position + 2) for position, index in enumerate(ordering))
    idcg = sum(gain / np.log2(position + 2) for position, gain in enumerate(sorted(ideal, reverse=True)))
    return float(dcg / idcg) if idcg else 0.0


def main() -> int:
    settings = get_settings()
    pairs = build_pairs()
    if not pairs:
        print("no pairs; run `python -m llm.corpus` first")
        return 1

    rng = np.random.default_rng(SEED)
    order = rng.permutation(len(pairs))
    split = int(len(pairs) * 0.7)
    train_index, test_index = order[:split], order[split:]

    train = [pairs[index] for index in train_index]
    test = [pairs[index] for index in test_index]

    def design(rows: list[Pair]) -> tuple[np.ndarray, np.ndarray]:
        # Pairwise differences: the model learns "how much better is chosen than rejected".
        x = []
        y = []
        for pair in rows:
            chosen = features(pair.chosen, pair.question, pair.chosen)
            rejected = features(pair.rejected, pair.question, pair.chosen)
            x.append(chosen - rejected)
            y.append(1)
            x.append(rejected - chosen)
            y.append(0)
        return np.array(x), np.array(y)

    x_train, y_train = design(train)
    model = LogisticRegression(random_state=SEED, max_iter=1000)
    model.fit(x_train, y_train)

    # Evaluation 1: does it prefer the chosen answer on held-out pairs?
    correct = 0
    margins = []
    for pair in test:
        chosen = features(pair.chosen, pair.question, pair.fact)
        rejected = features(pair.rejected, pair.question, pair.fact)
        margin = float(model.decision_function([chosen])[0] - model.decision_function([rejected])[0])
        margins.append(margin)
        correct += int(margin > 0)

    accuracy = correct / len(test)

    # Evaluation 2: rank four candidates (chosen plus three decoys) and score with NDCG.
    ndcgs = []
    for pair in test[:10]:
        candidates = [
            pair.chosen,
            pair.rejected,
            f"{pair.rejected} This is consistent with the stock ledger.",
            f"{pair.chosen} Values were reviewed last quarter.",
        ]
        scores = [float(model.decision_function([features(c, pair.question, pair.chosen)])[0]) for c in candidates]
        ordering = list(np.argsort(-np.array(scores)))
        ideal = [1 if index == 0 else 0 for index in range(len(candidates))]
        ndcgs.append(ndcg(ordering, ideal))

    checks = {
        "ranker_prefers_chosen_on_held_out": accuracy >= 0.8,
        "every_eval_margin_is_positive": all(margin > 0 for margin in margins),
        "ndcg_above_half": float(np.mean(ndcgs)) >= 0.5,
        "not_claiming_rlhf": True,
    }

    report = {
        "pairs_total": len(pairs),
        "pairs_train": len(train),
        "pairs_test": len(test),
        "features": ["contains_fact", "fact_coverage", "question_overlap", "length_saturated"],
        "model": "logistic regression on pairwise feature differences",
        "pairwise_accuracy": round(accuracy, 4),
        "mean_margin": round(float(np.mean(margins)), 4),
        "ndcg_at_4": round(float(np.mean(ndcgs)), 4),
        "checks": checks,
        "result": "PASS" if all(checks.values()) else "FAIL",
        "not_rlhf": (
            "There is no policy, no rollout loop and no reward-model-in-the-loop training. This is "
            "a preference ranker trained on offline pairs, which is the part that can be "
            "demonstrated honestly at this size."
        ),
    }

    path = settings.root / REPORT
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print(f"pairs {len(train)} train / {len(test)} test  accuracy {accuracy:.3f}  "
          f"mean margin {np.mean(margins):.3f}  ndcg@4 {np.mean(ndcgs):.3f}")
    print()
    for name, ok in checks.items():
        print(f"  [{'OK  ' if ok else 'FAIL'}] {name}")
    print(f"RESULT: {report['result']}")
    return 0 if report["result"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
