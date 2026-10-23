"""One deterministic dataset, three storage shapes.

Why one generator and not three: the whole claim of this repository is that **the same number
comes out of different engines**. That is only testable if the engines read the same rows. Three
generators would produce three datasets that happen to be similar, and agreement between them
would prove nothing.

The generator writes:

    data/landing/products/part-00000.parquet    flat table, read by Trino's Hive connector
                                                (a directory, because Hive `external_location`
                                                points at a directory, not at a file)
    data/landing/sales/dt=YYYY-MM-DD/*.parquet  partitioned, so pruning is real
    data/druid-ingest/{sales,products}.json     JSON lines for Druid batch ingestion
    quality/reports/expected.json               totals computed here, used as the cross-check

Money is integer minor units (cents). The same rule as the revenue platform, for the same reason:
a float residual is indistinguishable from a real discrepancy.

    python -m ingestion.generate
"""

from __future__ import annotations

import argparse
import json
import random
import shutil
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from tools.common.config import get_settings
from tools.common.logging_setup import get_logger, setup_logging

log = get_logger("ingestion")

SEED = 20261023
PRODUCTS = 5_000
DAYS = 30
EVENTS_PER_DAY = 2_000

CATEGORIES = ("books", "electronics", "garden", "kitchen", "office", "toys")
SUPPLIERS = ("northwind", "acme", "globex", "initech", "umbrella")
START = date(2026, 9, 24)

PRODUCT_SCHEMA = pa.schema(
    [
        pa.field("product_id", pa.int64(), nullable=False),
        pa.field("category", pa.string(), nullable=False),
        pa.field("supplier", pa.string(), nullable=False),
        pa.field("price_cents", pa.int64(), nullable=False),
        pa.field("stock_count", pa.int32(), nullable=False),
        pa.field("rating", pa.int8(), nullable=False),
    ]
)

SALE_SCHEMA = pa.schema(
    [
        pa.field("event_id", pa.int64(), nullable=False),
        pa.field("product_id", pa.int64(), nullable=False),
        pa.field("category", pa.string(), nullable=False),
        pa.field("dt", pa.string(), nullable=False),
        pa.field("amount_cents", pa.int64(), nullable=False),
        pa.field("quantity", pa.int32(), nullable=False),
    ]
)


@dataclass
class Dataset:
    products: list[dict]
    sales: list[dict]
    totals: dict


def build(seed: int = SEED, products: int = PRODUCTS, days: int = DAYS,
          events_per_day: int = EVENTS_PER_DAY) -> Dataset:
    """Every value is a pure function of an index and a seeded RNG.

    Deterministic by construction: the same seed gives byte-identical Parquet, so a number that
    changes between runs is a bug rather than sampling noise.
    """
    rng = random.Random(seed)

    catalogue = []
    for index in range(products):
        catalogue.append(
            {
                "product_id": index,
                "category": CATEGORIES[index % len(CATEGORIES)],
                "supplier": SUPPLIERS[index % len(SUPPLIERS)],
                "price_cents": 499 + (index % 500) * 37,
                "stock_count": (index * 7) % 250,
                "rating": 1 + (index % 5),
            }
        )

    sales = []
    event_id = 0
    for day_offset in range(days):
        day = START + timedelta(days=day_offset)
        for _ in range(events_per_day):
            product = catalogue[rng.randrange(products)]
            quantity = 1 + rng.randrange(4)
            sales.append(
                {
                    "event_id": event_id,
                    "product_id": product["product_id"],
                    "category": product["category"],
                    "dt": day.isoformat(),
                    "amount_cents": product["price_cents"] * quantity,
                    "quantity": quantity,
                }
            )
            event_id += 1

    by_category: dict[str, dict] = {}
    for product in catalogue:
        bucket = by_category.setdefault(product["category"], {"products": 0, "inventory_cents": 0})
        bucket["products"] += 1
        bucket["inventory_cents"] += product["price_cents"] * product["stock_count"]

    revenue_by_category: dict[str, int] = {}
    for sale in sales:
        revenue_by_category[sale["category"]] = revenue_by_category.get(sale["category"], 0) + sale["amount_cents"]

    # Revenue per supplier needs the join the federation parity check runs, so it is computed here
    # from the same rows. The expected value must not come from any engine being compared.
    supplier_of = {product["product_id"]: product["supplier"] for product in catalogue}
    revenue_by_supplier: dict[str, int] = {}
    inventory_by_supplier: dict[str, int] = {}
    for product in catalogue:
        inventory_by_supplier[product["supplier"]] = (
            inventory_by_supplier.get(product["supplier"], 0)
            + product["price_cents"] * product["stock_count"]
        )
    for sale in sales:
        supplier = supplier_of[sale["product_id"]]
        revenue_by_supplier[supplier] = revenue_by_supplier.get(supplier, 0) + sale["amount_cents"]

    top_inventory = max(by_category.items(), key=lambda item: item[1]["inventory_cents"])
    top_revenue = max(revenue_by_category.items(), key=lambda item: item[1])

    totals = {
        "products": len(catalogue),
        "sales": len(sales),
        "days": days,
        "days_list": [day.isoformat() for day in (START + timedelta(days=i) for i in range(days))],
        "products_by_category": {name: bucket["products"] for name, bucket in sorted(by_category.items())},
        "inventory_cents_by_category": {
            name: bucket["inventory_cents"] for name, bucket in sorted(by_category.items())
        },
        "revenue_cents_by_category": dict(sorted(revenue_by_category.items())),
        "revenue_cents_by_supplier": dict(sorted(revenue_by_supplier.items())),
        "inventory_cents_by_supplier": dict(sorted(inventory_by_supplier.items())),
        "inventory_cents_total": sum(bucket["inventory_cents"] for bucket in by_category.values()),
        "revenue_cents_total": sum(revenue_by_category.values()),
        # The two facts the cross-layer question depends on, computed here so the answer is known
        # before any engine is asked.
        "top_inventory_category": top_inventory[0],
        "top_inventory_cents": top_inventory[1]["inventory_cents"],
        "top_revenue_category": top_revenue[0],
        "top_revenue_cents": top_revenue[1],
    }

    return Dataset(products=catalogue, sales=sales, totals=totals)


def write_landing(dataset: Dataset, root: Path) -> dict:
    landing = root / "data" / "landing"
    if landing.exists():
        shutil.rmtree(landing)
    landing.mkdir(parents=True)

    products_dir = landing / "products"
    products_dir.mkdir()
    pq.write_table(pa.Table.from_pylist(dataset.products, schema=PRODUCT_SCHEMA), products_dir / "part-00000.parquet")

    # One file per day, so a date filter can skip files instead of scanning one blob.
    by_day: dict[str, list[dict]] = {}
    for sale in dataset.sales:
        by_day.setdefault(sale["dt"], []).append(sale)
    for day, rows in sorted(by_day.items()):
        target = landing / "sales" / f"dt={day}"
        target.mkdir(parents=True, exist_ok=True)
        pq.write_table(pa.Table.from_pylist(rows, schema=SALE_SCHEMA), target / "part-00000.parquet")

    # Druid ingests from the directory its container mounts (`/data/druid-ingest`). The first
    # version wrote to `data/druid`, which the container never saw: ingestion then read a file that
    # no step in this repository produced, and the manual copy that made it work was invisible.
    druid_dir = root / "data" / "druid-ingest"
    druid_dir.mkdir(parents=True, exist_ok=True)
    with (druid_dir / "sales.json").open("w", encoding="utf-8") as handle:
        for sale in dataset.sales:
            # Druid wants an ISO timestamp; `dt` alone is not a timestamp column.
            record = {**sale, "__time": f"{sale['dt']}T00:00:00.000Z"}
            handle.write(json.dumps(record) + "\n")

    first_day = dataset.totals["days_list"][0]
    with (druid_dir / "products.json").open("w", encoding="utf-8") as handle:
        for product in dataset.products:
            handle.write(json.dumps({**product, "__time": f"{first_day}T00:00:00.000Z"}) + "\n")

    return {
        "products_file": "data/landing/products",
        "sales_partitions": len(by_day),
        "druid_files": ["data/druid-ingest/sales.json", "data/druid-ingest/products.json"],
    }


def main(argv: list[str] | None = None) -> int:
    setup_logging()
    parser = argparse.ArgumentParser(description="Generate the shared dataset.")
    parser.add_argument("--seed", type=int, default=SEED)
    args = parser.parse_args(argv)

    settings = get_settings()
    dataset = build(args.seed)
    written = write_landing(dataset, settings.root)

    expected = {"seed": args.seed, **dataset.totals, **written}
    path = settings.reports / "expected.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(expected, indent=2), encoding="utf-8")

    print(json.dumps({key: expected[key] for key in (
        "products", "sales", "days", "inventory_cents_total", "revenue_cents_total",
        "top_inventory_category", "top_revenue_category",
    )}, indent=2))
    log.info("wrote %s and %s", written, path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
