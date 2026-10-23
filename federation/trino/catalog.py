"""Trino: the same tables, queried by a real distributed engine.

What runs here is Trino itself, in a container, reading the Parquet files the generator wrote.
The catalog is Trino's **Hive connector with the filesystem metastore** (`hive.metastore=file`),
which needs no Hive Metastore service. That is stated precisely because the difference matters:
the table format and partition layout are Hive's, the metastore *service* is not running.

    python -m federation.trino.catalog --register          # register tables
    python -m federation.trino.catalog --sql "SELECT ..."  # run one statement
"""

from __future__ import annotations

import json
import time

import requests

from tools.common.config import get_settings
from tools.common.logging_setup import get_logger, setup_logging

log = get_logger("trino")

WAREHOUSE = "hive"
SCHEMA = "lakehouse"


def _headers() -> dict[str, str]:
    settings = get_settings()
    return {"X-Trino-User": settings.trino.user, "X-Trino-Catalog": WAREHOUSE, "X-Trino-Schema": SCHEMA}


def query(sql: str, timeout: int = 120) -> dict:
    """Run one statement and return columns + rows.

    Trino answers a statement with a nextUri chain rather than one body; following it is not
    optional, because the first response usually holds no data rows at all. Reading only the first
    response is how a working query looks like an empty result.
    """
    settings = get_settings()
    started = time.perf_counter()
    response = requests.post(
        f"{settings.trino.url}/v1/statement",
        data=sql.encode("utf-8"),
        headers=_headers(),
        timeout=timeout,
    )
    if response.status_code != 200:
        raise RuntimeError(f"trino rejected the statement ({response.status_code}): {response.text[:300]}")

    payload = response.json()
    columns: list[str] = []
    rows: list[list] = []

    while True:
        if payload.get("error"):
            error = payload["error"]
            raise RuntimeError(f"trino error: {error.get('message')} ({error.get('errorName')})")

        if payload.get("columns") and not columns:
            columns = [column["name"] for column in payload["columns"]]
        rows.extend(payload.get("data") or [])

        next_uri = payload.get("nextUri")
        if not next_uri:
            break
        response = requests.get(next_uri, headers=_headers(), timeout=timeout)
        payload = response.json()

    return {
        "sql": sql,
        "columns": columns,
        "rows": rows,
        "row_count": len(rows),
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
    }

def scalar(sql: str) -> object:
    result = query(sql)
    return result["rows"][0][0] if result["rows"] else None


def wait_ready(timeout: int = 180) -> bool:
    settings = get_settings()
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            response = requests.get(f"{settings.trino.url}/v1/info", timeout=5)
            if response.status_code == 200 and response.json().get("starting") is False:
                return True
        except requests.RequestException:
            pass
        time.sleep(3)
    return False


def register_tables() -> dict:
    """Create the schema and the two external tables over the generated Parquet.

    `external_location` points inside the container at the mounted landing directory. Partition
    columns are declared in `partitioned_by`, which is what lets Trino skip files instead of
    reading all of them.

    The scheme is `local://`, not `file://`, and that is not cosmetic. With `file://`, Trino 484
    routed the read through Hadoop's LocalFileSystem, which resolved the path under the JVM temp
    directory: every table was created successfully and every count came back 0. `local://` uses
    Trino's own local filesystem provider and reads the directory that is actually mounted.
    """
    statements = [
        f"CREATE SCHEMA IF NOT EXISTS {WAREHOUSE}.{SCHEMA} WITH (location = 'file:///data/warehouse/tables')",
        (
            f"CREATE TABLE IF NOT EXISTS {WAREHOUSE}.{SCHEMA}.products ("
            " product_id bigint, category varchar, supplier varchar,"
            " price_cents bigint, stock_count integer, rating tinyint"
            ") WITH (external_location = 'local:///data/landing/products', format = 'PARQUET')"
        ),
        (
            f"CREATE TABLE IF NOT EXISTS {WAREHOUSE}.{SCHEMA}.sales ("
            " event_id bigint, product_id bigint, category varchar,"
            " amount_cents bigint, quantity integer, dt varchar"
            ") WITH ("
            " external_location = 'local:///data/landing/sales', format = 'PARQUET',"
            " partitioned_by = ARRAY['dt'])"
        ),
        # After the tables exist: register the partitions. The file metastore does not discover
        # `dt=` directories on its own, so without this the table is created and reads zero rows —
        # a green DDL with no data, which is exactly the kind of success this repository refuses.
        f"CALL {WAREHOUSE}.system.sync_partition_metadata('{SCHEMA}', 'sales', 'ADD')",
    ]

    results = []
    for statement in statements:
        try:
            result = query(statement)
            results.append({"sql": statement.split("(")[0].strip(), "ok": True, "elapsed_ms": result["elapsed_ms"]})
        except RuntimeError as exc:
            results.append({"sql": statement.split("(")[0].strip(), "ok": False, "error": str(exc)[:200]})
    return {"statements": results}


def main(argv: list[str] | None = None) -> int:
    import argparse

    setup_logging()
    parser = argparse.ArgumentParser(description="Trino catalog and queries.")
    parser.add_argument("--register", action="store_true")
    parser.add_argument("--sql", default="")
    parser.add_argument("--wait", action="store_true")
    args = parser.parse_args(argv)

    if args.wait:
        print("ready" if wait_ready() else "not ready")
        return 0 if wait_ready(1) else 1
    if args.register:
        registered = register_tables()
        settings = get_settings()
        expected = json.loads((settings.reports / "expected.json").read_text(encoding="utf-8"))

        counts = {}
        errors = {}
        for table, key in (("sales", "sales"), ("products", "products")):
            try:
                counts[table] = scalar(f"SELECT count(*) FROM hive.lakehouse.{table}")
            except RuntimeError as exc:
                errors[table] = str(exc)[:200]

        checks = {
            "schema_and_tables_created": all(item["ok"] for item in registered["statements"]),
            "sales_row_count_matches_generator": counts.get("sales") == expected["sales"],
            "products_row_count_matches_generator": counts.get("products") == expected["products"],
        }
        report = {
            "catalog": f"{WAREHOUSE}.{SCHEMA}",
            "metastore": "file (hive.metastore=file); no metastore service is running",
            "registered": registered["statements"],
            "row_counts": counts,
            "errors": errors,
            "checks": checks,
            "result": "PASS" if all(checks.values()) else "FAIL",
            "note": (
                "The table format and partition layout are Hive's; the metastore is a directory on "
                "disk, not a Hive Metastore service. The row counts are compared with the "
                "generator, not with Trino's own answer from a second query."
            ),
        }
        path = settings.reports / "trino_catalog.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, indent=2), encoding="utf-8")

        for item in registered["statements"]:
            print(f"  [{'OK  ' if item['ok'] else 'FAIL'}] {item['sql']}")
        print(f"  row counts: {counts}")
        for name, ok in checks.items():
            print(f"  [{'OK  ' if ok else 'FAIL'}] {name}")
        print(f"RESULT: {report['result']}")
        return 0 if report["result"] == "PASS" else 1
    if args.sql:
        print(json.dumps(query(args.sql), indent=2, default=str))
        return 0

    print(json.dumps({"info": requests.get(f"{get_settings().trino.url}/v1/info", timeout=10).json()}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

