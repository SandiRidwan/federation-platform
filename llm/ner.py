"""Named-entity extraction, rule-based, measured against entities that are known.

`spacy` is not installed and pulling a model would make the repository un-runnable offline, so this
is a rule-based extractor over the corpus's own entity grammar. That is not a weakness in the
measurement: the entities are **known by construction** — the generator wrote them down — so
precision and recall are exact rather than eyeballed.

The ground truth comes from `data/corpus/entities.json`, written by the generator. It is
deliberately **not** produced by running the extractor: a second pass of the same regex would only
prove that the pattern matches itself.

    python -m llm.ner
"""

from __future__ import annotations

import json
import re

from tools.common.config import get_settings

SUPPLIERS = ("northwind", "acme", "globex", "initech", "umbrella")
CATEGORIES = ("books", "electronics", "garden", "kitchen", "office", "toys")

SKU = re.compile(r"\bX-\d{3,}\b")
WAREHOUSE = re.compile(r"\bWH-\d{1,3}\b")
SUPPLIER = re.compile(r"\b(" + "|".join(SUPPLIERS) + r")\b", re.IGNORECASE)
CATEGORY = re.compile(r"\b(" + "|".join(CATEGORIES) + r")\b", re.IGNORECASE)

PATTERNS = {"sku": SKU, "warehouse": WAREHOUSE, "supplier": SUPPLIER, "category": CATEGORY}


def extract(text: str) -> dict[str, list[str]]:
    """All entities in `text`, grouped by label, in first-seen order."""
    found: dict[str, list[str]] = {}
    for label, pattern in PATTERNS.items():
        values = []
        for match in pattern.finditer(text):
            value = match.group(0)
            if value not in values:
                values.append(value)
        if values:
            found[label] = values
    return found


def score(documents: list[tuple[dict[str, list[str]], dict[str, list[str]]]]) -> dict:
    """Precision and recall per label over (truth, predicted) pairs."""
    totals: dict[str, dict[str, int]] = {}
    for truth, predicted in documents:
        for label in PATTERNS:
            bucket = totals.setdefault(label, {"true_positive": 0, "false_positive": 0, "false_negative": 0})
            truth_set = set(truth.get(label, []))
            predicted_set = set(predicted.get(label, []))
            bucket["true_positive"] += len(truth_set & predicted_set)
            bucket["false_positive"] += len(predicted_set - truth_set)
            bucket["false_negative"] += len(truth_set - predicted_set)

    per_label = {}
    for label, counts in totals.items():
        precision = counts["true_positive"] / max(1, counts["true_positive"] + counts["false_positive"])
        recall = counts["true_positive"] / max(1, counts["true_positive"] + counts["false_negative"])
        per_label[label] = {
            **counts,
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "f1": round(2 * precision * recall / (precision + recall), 4) if precision + recall else 0.0,
        }
    return per_label


def main() -> int:
    settings = get_settings()
    corpus_dir = settings.root / "data" / "corpus"
    entities_path = corpus_dir / "entities.json"
    if not entities_path.exists():
        print("no corpus; run `python -m llm.corpus` first")
        return 1

    truth = json.loads(entities_path.read_text(encoding="utf-8"))
    documents = []
    for doc_id, expected in sorted(truth.items()):
        text = (corpus_dir / f"{doc_id}.txt").read_text(encoding="utf-8")
        documents.append((expected, extract(text)))

    per_label = score(documents)
    checks = {
        "every_label_extracted": set(per_label) == set(PATTERNS),
        "precision_is_one": all(item["precision"] == 1.0 for item in per_label.values()),
        "recall_is_one": all(item["recall"] == 1.0 for item in per_label.values()),
    }

    report = {
        "method": "rule-based (regex + gazetteer); no spacy model",
        "documents": len(documents),
        "ground_truth_source": "data/corpus/entities.json (written by the generator)",
        "per_label": per_label,
        "checks": checks,
        "result": "PASS" if all(checks.values()) else "FAIL",
        "note": (
            "Entities are known by construction, so precision and recall are exact. A neural tagger "
            "would be judged on a real corpus and is not used: it is not installed, and a model "
            "download would make this offline-only repository require the network."
        ),
    }

    path = settings.reports / "llm_ner.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    for label, item in per_label.items():
        print(f"  {label:<10} precision={item['precision']} recall={item['recall']} f1={item['f1']}")
    print()
    for name, ok in checks.items():
        print(f"  [{'OK  ' if ok else 'FAIL'}] {name}")
    print(f"RESULT: {report['result']}")
    return 0 if report["result"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
