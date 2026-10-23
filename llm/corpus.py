"""A corpus with answers that are known before any retrieval runs.

Every retrieval benchmark that uses a real corpus has the same problem: nobody knows what the
right answer is, so "recall" is judged by eye. This corpus is generated with the answer built in:

    each document states facts about exactly one entity
    each question names one entity and asks one fact
    the ground truth is the document that states it

Two question families exist on purpose, because the comparison between lexical and vector
retrieval is only meaningful if it can come out either way:

    lexical      the question reuses a rare token from the document ("WH-7", "X-1234")
                 -> BM25 should win: exact term match is what it is built for
    paraphrase   the question uses ordinary words and none of the document's vocabulary
                 ("how long until it arrives" vs "lead time of 17 days")
                 -> vectors should win: there is no term to match

If one method won every question, the corpus would be measuring nothing.

    python -m llm.corpus
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from tools.common.config import get_settings

DOCS = 120

WAREHOUSES = ("WH-1", "WH-7", "WH-12", "WH-33", "WH-48")
CATEGORIES = ("books", "electronics", "garden", "kitchen", "office", "toys")
SUPPLIERS = ("northwind", "acme", "globex", "initech", "umbrella")

# Paraphrase families: (question template, fact phrase used in the document).
# The question deliberately does **not** name the SKU. An earlier version did, which meant every
# "paraphrase" question contained the rarest token in the document — so BM25 scored 120/120 on the
# family that was supposed to test semantic matching. The question now identifies the item by
# category and supplier only, which is ambiguous across four documents, so recall@5 and recall@1
# diverge and the measurement says something.
PARAPHRASE_FAMILIES = [
    (
        "how long does the {category} item from {supplier} take to arrive once ordered",
        "lead time of {value} days",
    ),
    (
        "what does it cost to keep the {category} stock from {supplier} in storage for a year",
        "annual storage fee of {value} cents",
    ),
    (
        "how quickly can the {category} stock from {supplier} be restocked after a shortage",
        "restock interval of {value} hours",
    ),
]

# A second phrasing of the same facts, used ONLY to calibrate the refusal threshold. It must not
# overlap with the questions the threshold is then evaluated on: a threshold picked on the test
# questions is a threshold fitted to the test, and the resulting accuracy is meaningless.
CALIBRATION_EVERY = 6  # 20 of the 120 documents

# Out-of-corpus calibration questions, again different topics from the eval set.
CALIBRATION_UNANSWERABLE = (
    "who signs off on overtime for the loading dock",
    "what is the maximum pallet height in the yard",
    "which supplier audits are scheduled this quarter",
    "how much does a forklift licence renewal cost",
    "what temperature is the cold storage kept at",
    "who handles customs paperwork for imports",
    "what is the average parcel weight",
    "how often are fire drills run",
    "which bank finances the fleet",
    "what is the policy on returning damaged pallets",
)


@dataclass(frozen=True)
class Doc:
    doc_id: str
    text: str
    # The entities this document was built from. The extractor is scored against THIS, not against
    # a second pass of its own regex — otherwise the measurement proves only that the pattern
    # matches itself.
    entities: dict[str, list[str]]


@dataclass(frozen=True)
class Question:
    question_id: str
    text: str
    answer_doc: str
    family: str  # "lexical" or "paraphrase"
    fact: str


def build(docs: int = DOCS) -> tuple[list[Doc], list[Question], list[Question], list[Question]]:
    # No RNG: every value is derived from the document index, so two runs are identical by
    # construction rather than by seeding. That is what the determinism test relies on.

    corpus: list[Doc] = []
    questions: list[Question] = []
    unanswerable: list[Question] = []
    calibration: list[Question] = []

    for index in range(docs):
        sku = f"X-{1000 + index}"
        warehouse = WAREHOUSES[index % len(WAREHOUSES)]
        category = CATEGORIES[index % len(CATEGORIES)]
        supplier = SUPPLIERS[index % len(SUPPLIERS)]
        lead_time = 3 + (index * 7) % 40
        storage_fee = 1200 + (index * 37) % 9000
        restock_hours = 4 + (index * 3) % 60
        unit_price = 499 + (index * 13) % 4000

        family_index = index % len(PARAPHRASE_FAMILIES)
        template, fact_template = PARAPHRASE_FAMILIES[family_index]
        value = {"lead time of {value} days": lead_time,
                 "annual storage fee of {value} cents": storage_fee,
                 "restock interval of {value} hours": restock_hours}[fact_template]
        fact = fact_template.format(value=value)

        text = (
            f"Product {sku} is a {category} item supplied by {supplier}. "
            f"It is held in warehouse {warehouse} at a unit price of {unit_price} cents. "
            f"The item has a {fact}. "
            f"Replenishment for {sku} is handled by the {supplier} operations desk, "
            f"and every shipment into {warehouse} is logged against the {category} ledger."
        )
        corpus.append(
            Doc(
                doc_id=f"doc-{index:03d}",
                text=text,
                entities={
                    "sku": [sku],
                    "warehouse": [warehouse],
                    "supplier": [supplier],
                    "category": [category],
                },
            )
        )

        # Lexical question: reuses the rare SKU and the warehouse code.
        questions.append(
            Question(
                question_id=f"lex-{index:03d}",
                text=f"Which warehouse holds {sku}?",
                answer_doc=f"doc-{index:03d}",
                family="lexical",
                fact=warehouse,
            )
        )

        # Paraphrase question: identifies the item by category and supplier, never by SKU. It
        # shares the fact's meaning but not its words ("take to arrive" vs "lead time").
        questions.append(
            Question(
                question_id=f"par-{index:03d}",
                text=template.format(category=category, supplier=supplier),
                answer_doc=f"doc-{index:03d}",
                family="paraphrase",
                fact=fact,
            )
        )

        # Calibration phrasing: the same facts asked a third way, held out from every scored metric.
        if index % CALIBRATION_EVERY == 0:
            calibration.extend(
                [
                    Question(
                        question_id=f"cal-lex-{index:03d}",
                        text=f"in which depot is the item {sku} stored",
                        answer_doc=f"doc-{index:03d}",
                        family="calibration",
                        fact=warehouse,
                    ),
                    Question(
                        question_id=f"cal-lead-{index:03d}",
                        text=f"how many days before a {category} item from {supplier} arrives",
                        answer_doc=f"doc-{index:03d}",
                        family="calibration",
                        fact=f"lead time of {lead_time} days",
                    ),
                    Question(
                        question_id=f"cal-fee-{index:03d}",
                        text=f"what yearly fee applies to storing {category} stock from {supplier}",
                        answer_doc=f"doc-{index:03d}",
                        family="calibration",
                        fact=f"annual storage fee of {storage_fee} cents",
                    ),
                    Question(
                        question_id=f"cal-restock-{index:03d}",
                        text=f"how many hours does restocking {category} stock from {supplier} take",
                        answer_doc=f"doc-{index:03d}",
                        family="calibration",
                        fact=f"restock interval of {restock_hours} hours",
                    ),
                ]
            )

    # Questions the corpus cannot answer. The harness must refuse these, not return a near miss.
    for index, topic in enumerate(
        ("what is the refund policy for damaged goods",
         "which courier delivers to the northern depot",
         "what is the warranty period on seasonal stock",
         "how many staff work the night shift",
         "what is the carbon footprint of air freight")
    ):
        unanswerable.append(
            Question(
                question_id=f"un-{index:03d}",
                text=topic,
                answer_doc="",
                family="unanswerable",
                fact="",
            )
        )

    # Same idea, different topics: these calibrate the refusal threshold and are never scored.
    for index, topic in enumerate(CALIBRATION_UNANSWERABLE):
        calibration.append(
            Question(
                question_id=f"cal-un-{index:03d}",
                text=topic,
                answer_doc="",
                family="calibration",
                fact="",
            )
        )

    return corpus, questions, unanswerable, calibration


def main() -> int:
    settings = get_settings()
    corpus, questions, unanswerable, calibration = build()

    out = settings.root / "data" / "corpus"
    out.mkdir(parents=True, exist_ok=True)
    for doc in corpus:
        (out / f"{doc.doc_id}.txt").write_text(doc.text, encoding="utf-8")

    # Entities per document, so the NER measurement has ground truth that does not come from the
    # extractor being measured.
    (out / "entities.json").write_text(
        json.dumps({doc.doc_id: doc.entities for doc in corpus}, indent=2), encoding="utf-8"
    )

    payload = {
        "docs": len(corpus),
        "questions": len(questions),
        "unanswerable": len(unanswerable),
        "calibration": {
            "answerable": sum(1 for question in calibration if question.answer_doc),
            "unanswerable": sum(1 for question in calibration if not question.answer_doc),
        },
        "families": {
            "lexical": sum(1 for question in questions if question.family == "lexical"),
            "paraphrase": sum(1 for question in questions if question.family == "paraphrase"),
        },
    }
    (settings.reports / "corpus.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    (out / "questions.json").write_text(
        json.dumps(
            {
                "answerable": [question.__dict__ for question in questions],
                "unanswerable": [question.__dict__ for question in unanswerable],
                # Used to calibrate the refusal threshold. Never scored: if it were scored, the
                # threshold would be fitted to its own test set.
                "calibration": {
                    "answerable": [question.__dict__ for question in calibration if question.answer_doc],
                    "unanswerable": [question.__dict__ for question in calibration if not question.answer_doc],
                },
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    print(json.dumps(payload, indent=2))
    print(f"wrote {len(corpus)} docs to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
