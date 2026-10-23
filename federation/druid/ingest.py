"""Druid: the same events, aggregated by a different engine.

Druid is here because Wikimedia's JD names it next to Hive and Presto, and because it answers a
different question than Trino: sub-second aggregation over time-series events. The point is not
"we can run Druid" — it is **when Druid wins and when Trino wins**, measured on the same rows.

Ingestion is a batch `index_parallel` task reading a local file. Not Kafka: the streaming path
would add a broker for no extra evidence, and the claim being tested is the query latency, not the
transport.

    python -m federation.druid.ingest
    python -m federation.druid.query "SELECT ..."
"""

from __future__ import annotations

import json
import time

import requests

from tools.common.config import get_settings
from tools.common.logging_setup import get_logger, setup_logging

log = get_logger("druid")

DATASOURCE = "sales"
PRODUCTS_DATASOURCE = "products"
INGEST_DIR = "/data/druid-ingest"
INGEST_FILE = "sales.json"
PRODUCTS_FILE = "products.json"


def broker_ready(timeout: int = 60) -> bool:
    settings = get_settings()
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if requests.get(f"{settings.druid.broker_url}/status", timeout=5).status_code == 200:
                return True
        except requests.RequestException:
            pass
        time.sleep(2)
    return False


def task_spec(datasource: str = DATASOURCE, ingest_file: str = INGEST_FILE, schema: dict | None = None) -> dict:
    """Batch ingestion over one local JSON file.

    `__time` is an ISO timestamp the generator wrote from the same `dt` value the other engines
    filter on, so a "same day, same number" comparison is comparing the same rows rather than two
    dates that happen to look alike.
    """
    return {
        "type": "index_parallel",
        "spec": {
            "ioConfig": {
                "type": "index_parallel",
                "inputSource": {"type": "local", "baseDir": INGEST_DIR, "filter": ingest_file},
                "inputFormat": {"type": "json"},
                "appendToExisting": False,
            },
            "dataSchema": {
                "dataSource": datasource,
                "timestampSpec": {"column": "__time", "format": "iso"},
                "dimensionsSpec": {"dimensions": schema["dimensions"]},
                "metricsSpec": schema["metrics"],
                "granularitySpec": {
                    "type": "uniform",
                    "segmentGranularity": "DAY",
                    "queryGranularity": "DAY",
                    # Rollup is Druid's default and it collapses identical rows. With it on, the
                    # first ingestion produced 49,399 rows for 60,000 events, and COUNT(*) stopped
                    # being comparable with the other engines. Off, so "same rows" means the same
                    # rows. Recorded in ADR 0003 because this difference looks like a bug in the
                    # other engine.
                    "rollup": False,
                },
            },
            "tuningConfig": {
                "type": "index_parallel",
                "maxRowsInMemory": 100_000,
                # `dynamic`, not `hashed`: a hashed partitionsSpec requires guaranteed rollup, and
                # guaranteed rollup would collapse events that the other engines count separately.
                # The comparison has to be over the same rows.
                "forceGuaranteedRollup": False,
                "partitionsSpec": {"type": "dynamic", "maxRowsPerSegment": 500_000},
            },
        },
    }


SALES_SCHEMA = {
    "dimensions": [
        {"type": "string", "name": "category"},
        {"type": "string", "name": "dt"},
        {"type": "long", "name": "product_id"},
    ],
    "metrics": [
        {"type": "count", "name": "events"},
        {"type": "longSum", "name": "amount_cents", "fieldName": "amount_cents"},
        {"type": "longSum", "name": "quantity", "fieldName": "quantity"},
    ],
}

PRODUCTS_SCHEMA = {
    "dimensions": [
        {"type": "string", "name": "category"},
        {"type": "string", "name": "supplier"},
        {"type": "long", "name": "product_id"},
    ],
    "metrics": [
        {"type": "count", "name": "rows"},
        {"type": "longSum", "name": "price_cents", "fieldName": "price_cents"},
        {"type": "longSum", "name": "stock_count", "fieldName": "stock_count"},
    ],
}


def submit(spec: dict) -> dict:
    settings = get_settings()
    response = requests.post(
        f"{settings.druid.router_url}/druid/indexer/v1/task",
        json=spec,
        timeout=60,
    )
    if response.status_code != 200:
        raise RuntimeError(f"druid rejected the task ({response.status_code}): {response.text[:400]}")
    return response.json()


def wait_task(task_id: str, timeout: int = 600) -> dict:
    settings = get_settings()
    deadline = time.time() + timeout
    while time.time() < deadline:
        response = requests.get(
            f"{settings.druid.router_url}/druid/indexer/v1/task/{task_id}/status", timeout=30
        )
        payload = response.json()
        state = payload.get("status", {}).get("status")
        if state == "SUCCESS":
            return payload["status"]
        if state in {"FAILED", "KILLED"}:
            raise RuntimeError(f"druid task {task_id} {state}: {json.dumps(payload)[:600]}")
        time.sleep(3)
    raise RuntimeError(f"druid task {task_id} did not finish in {timeout}s")


def query(sql: str, timeout: int = 120) -> dict:
    """SQL over Druid's HTTP endpoint, returning rows and the server's own timing.

    `sqlTime` comes from Druid, not from the client clock, so the comparison with Trino is not
    distorted by Python's request overhead.
    """
    settings = get_settings()
    started = time.perf_counter()
    response = requests.post(
        f"{settings.druid.broker_url}/druid/v2/sql",
        json={"query": sql, "resultFormat": "array"},
        timeout=timeout,
    )
    elapsed = (time.perf_counter() - started) * 1000
    if response.status_code != 200:
        raise RuntimeError(f"druid query failed ({response.status_code}): {response.text[:400]}")
    rows = response.json()
    return {
        "sql": sql,
        "rows": rows,
        "row_count": len(rows),
        "client_elapsed_ms": round(elapsed, 2),
        "server_sql_time_ms": response.headers.get("X-Druid-SQL-Query-Time"),
    }


def wait_queryable(datasource: str, timeout: int = 120) -> int | None:
    """Poll until the broker can see the datasource.

    A task can report SUCCESS while the broker still answers `Object '<name>' not found`: the
    segment is published to the metadata store, then loaded by a historical, then announced to the
    broker. Querying immediately after SUCCESS is the mistake — the task succeeding is not the
    table being readable.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            result = query(f'SELECT COUNT(*) AS "n" FROM "{datasource}"')
            if result["rows"]:
                return int(result["rows"][0][0])
        except RuntimeError:
            pass
        time.sleep(3)
    return None


def load() -> dict:
    """Load both datasources. `products` exists so the join case has something to join to.

    Re-running is idempotent only because the input is deterministic: a batch task replaces the
    segments whose interval it covers, so the same file replaces the same rows. If the generated
    timestamps ever move, the old segments stay alive next to the new ones and a sum silently
    doubles. Druid's kill task reported SUCCESS here and the segments stayed queryable, so the
    reliable reset is a fresh container (`docker compose restart druid`), not a delete API. That is
    recorded in ADR 0003 instead of being papered over with a retry.
    """
    if not broker_ready():
        raise RuntimeError("druid broker is not answering on /status")

    results = {}
    for datasource, ingest_file, schema in (
        (DATASOURCE, INGEST_FILE, SALES_SCHEMA),
        (PRODUCTS_DATASOURCE, PRODUCTS_FILE, PRODUCTS_SCHEMA),
    ):
        started = time.perf_counter()
        task = submit(task_spec(datasource, ingest_file, schema))
        task_id = task["task"]
        status = wait_task(task_id)
        elapsed = time.perf_counter() - started

        rows_queryable = wait_queryable(datasource)
        results[datasource] = {
            "datasource": datasource,
            "task_id": task_id,
            "task_state": status.get("status"),
            "rows_ingested": status.get("rowCount"),
            "duration_ms": status.get("duration"),
            "wall_seconds": round(elapsed, 2),
            "rows_queryable": rows_queryable,
        }
    return results


def main(argv: list[str] | None = None) -> int:
    import argparse

    setup_logging()
    parser = argparse.ArgumentParser(description="Druid ingestion and queries.")
    parser.add_argument("--load", action="store_true")
    parser.add_argument("--sql", default="")
    parser.add_argument("--spec", action="store_true")
    parser.add_argument("--wait", action="store_true", help="wait until the broker answers, then exit")
    args = parser.parse_args(argv)

    if args.wait:
        ready = broker_ready(timeout=300)
        print("ready" if ready else "not ready")
        return 0 if ready else 1
    if args.spec:
        print(json.dumps(task_spec(), indent=2))
        return 0
    if args.load:
        result = load()
        settings = get_settings()
        path = settings.reports / "druid_load.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(json.dumps(result, indent=2))
        return 0
    if args.sql:
        print(json.dumps(query(args.sql), indent=2, default=str))
        return 0

    print(json.dumps({"broker_ready": broker_ready()}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

