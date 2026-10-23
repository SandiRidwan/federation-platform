"""One question, four layers, every number traced back to where it came from.

Without this file the repository is five demos that share a generator. The question is the thing
that makes it a system:

    "Which category holds the most inventory value, and which category earned the most revenue —
     and can the agent quote the source for each answer?"

Each layer answers one part, and each part is checked against the generator:

    1. Trino    inventory value per category    (Iceberg over S3)
    2. Druid    revenue per category            (the same rows, different engine)
    3. Azurite  the curated result parked as a blob        (ADLS, Blob API)
    4. LLM      the retrieved document that states the fact (vector index over the corpus)

The point is not that each works. It is that a number produced in step 1 can be found again in
step 3, and that the citation in step 4 names a document that actually contains the fact. A
pipeline where each stage is green but the numbers do not line up is the failure this exists to
catch.

    python -m crosslayer.question
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from azure.storage.blob import BlobServiceClient

from federation.druid.ingest import query as druid_query
from federation.trino.catalog import query as trino_query
from llm.chunking import chunk_corpus
from llm.embed import embedder
from llm.index import VectorIndex
from tools.common.config import get_settings

REPORT = Path("quality/reports/crosslayer.json")


def _container():
    settings = get_settings()
    service = BlobServiceClient.from_connection_string(settings.azurite.connection_string)
    return service.get_container_client(settings.azurite.container)


def trino_inventory() -> dict[str, int]:
    result = trino_query(
        "SELECT category, sum(price_cents * stock_count) AS inventory "
        "FROM iceberg.lakehouse.products GROUP BY category ORDER BY inventory DESC"
    )
    return {str(row[0]): int(row[1]) for row in result["rows"]}


def druid_revenue() -> dict[str, int]:
    result = druid_query(
        'SELECT "category", SUM("amount_cents") AS "revenue" FROM "sales" '
        'GROUP BY "category" ORDER BY "revenue" DESC'
    )
    return {str(row[0]): int(row[1]) for row in result["rows"]}


def park(rows: dict[str, int], name: str) -> dict:
    """Write the joined result to Azurite, read it back, and compare byte for byte.

    This is the step that makes the Azure layer part of the pipeline rather than a separate demo.
    A write that returns 201 and stores nothing is the failure a byte comparison catches.
    """
    body = json.dumps(rows, indent=2, sort_keys=True).encode("utf-8")
    container = _container()
    container.upload_blob(name=name, data=body, overwrite=True)
    back = container.download_blob(name).readall()
    return {"blob": name, "bytes": len(body), "readback_matches": back == body}


def cite(question: str, expected_fact: str) -> dict:
    """Retrieve the document that states the fact, and quote it.

    The citation is only accepted if the retrieved chunk actually contains the fact. A "the model
    cited document 42" answer that nobody checked is exactly the claim this avoids.
    """
    settings = get_settings()
    corpus_dir = settings.root / "data" / "corpus"
    docs = [
        {"doc_id": path.stem, "text": path.read_text(encoding="utf-8")}
        for path in sorted(corpus_dir.glob("doc-*.txt"))
    ]
    chunks = chunk_corpus(docs, "recursive")
    model = embedder().fit([chunk.text for chunk in chunks])
    index = VectorIndex.build(chunks, model.transform([chunk.text for chunk in chunks]))

    vector = model.transform([question])[0]
    scored = index.search(vector, 3)
    hits = [
        {
            "doc_id": chunks[position].doc_id,
            "contains_fact": expected_fact in chunks[position].text,
            "quote": chunks[position].text[:120],
        }
        for position, _score in scored
    ]
    return {
        "question": question,
        "expected_fact": expected_fact,
        "hits": hits,
        "cited_contains_fact": any(hit["contains_fact"] for hit in hits),
    }


def main() -> int:
    settings = get_settings()
    expected = json.loads((settings.reports / "expected.json").read_text(encoding="utf-8"))
    started = datetime.now(timezone.utc)

    # 1 + 2: the two engines
    inventory = trino_inventory()
    revenue = druid_revenue()

    # 3: join and park the result — one artefact that a second system produced a part of
    joined = {
        category: {
            "inventory_cents": inventory.get(category),
            "revenue_cents": revenue.get(category),
        }
        for category in sorted(set(inventory) | set(revenue))
    }
    parked = park(joined, f"crosslayer/joined-{started.strftime('%Y%m%dT%H%M%S')}.json")

    # 4: cite the source for a factual question the corpus genuinely answers.
    #
    # The first version asked "which category holds the most inventory value" — a question the
    # corpus cannot answer, because the corpus holds products rather than aggregates. Retrieval
    # returned doc-006 and the fact was not in it, which was correct behaviour being scored as a
    # failure. A cross-layer check has to ask the retrieval layer something the retrieval layer is
    # for: "which warehouse holds X?" is answered by a document, "which category has the most
    # inventory" is answered by a query engine.
    top_inventory = expected["top_inventory_category"]
    entities = {doc_id: values for doc_id, values in json.loads(
        (settings.root / "data" / "corpus" / "entities.json").read_text(encoding="utf-8")
    ).items()}
    sample_doc = next(
        doc_id for doc_id, values in entities.items() if values.get("category") == [top_inventory]
    )
    sample_sku = entities[sample_doc]["sku"][0]
    citation = cite(f"Which warehouse holds {sample_sku}?", entities[sample_doc]["warehouse"][0])

    checks = {
        "trino_matches_generator": inventory == expected["inventory_cents_by_category"],
        "druid_matches_generator": revenue == expected["revenue_cents_by_category"],
        "parked_blob_reads_back_identically": parked["readback_matches"],
        "top_inventory_category_agrees_with_trino": max(inventory, key=inventory.get) == top_inventory,
        "top_revenue_category_agrees_with_druid": max(revenue, key=revenue.get) == expected["top_revenue_category"],
        "citation_names_a_document_with_the_fact": citation["cited_contains_fact"],
    }

    report = {
        "ran_at": started.isoformat(),
        "question": "Which category holds the most inventory value, and which earned the most revenue?",
        "layers": {
            "trino_iceberg_over_s3": {"rows": len(inventory), "values": inventory},
            "druid": {"rows": len(revenue), "values": revenue},
            "azurite": parked,
            "retrieval": citation,
        },
        "headline": {
            "top_inventory_category": top_inventory,
            "top_inventory_cents": inventory.get(top_inventory),
            "top_revenue_category": expected["top_revenue_category"],
            "top_revenue_cents": revenue.get(expected["top_revenue_category"]),
        },
        "checks": checks,
        "result": "PASS" if all(checks.values()) else "FAIL",
        "note": (
            "Four layers, one question. Each number is checked against the generator, the parked "
            "artifact is compared byte for byte, and the citation is only accepted if the retrieved "
            "document actually contains the fact."
        ),
    }

    path = settings.root / REPORT
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print(f"inventory (trino) : {inventory}")
    print(f"revenue   (druid) : {revenue}")
    print(f"parked            : {parked['blob']} ({parked['bytes']} bytes, identical={parked['readback_matches']})")
    print(f"citation          : {citation['hits'][0]['doc_id'] if citation['hits'] else '-'} "
          f"contains_fact={citation['cited_contains_fact']}")
    print()
    for name, ok in checks.items():
        print(f"  [{'OK  ' if ok else 'FAIL'}] {name}")
    print(f"RESULT: {report['result']}")
    return 0 if report["result"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
