"""Synapse SQL: the model translated to T-SQL, and executed on an equivalent engine.

Synapse has no free tier that runs on a laptop, so the honest split is:

    transpile   every model converts to the T-SQL dialect and parses back
    execute     the same logic runs on DuckDB, so the numbers are checked

The report calls this **compiled, executed on an equivalent engine** — never "ran on Synapse".
That distinction is the difference between a claim and a wish.

    python -m azure_layer.synapse.transpile
"""

from __future__ import annotations

import json
from pathlib import Path

import duckdb
import sqlglot

from tools.common.config import get_settings

REPORT = Path("quality/reports/azure_synapse.json")
DIALECT = "tsql"  # Synapse is T-SQL, and sqlglot's tsql target is the closest published dialect.

# The models an analyst would build over the lakehouse. Written once, in ANSI SQL, and then both
# transpiled and executed — so the two checks cannot disagree about what the query is.
MODELS = {
    "revenue_by_category": """
        SELECT category, SUM(amount_cents) AS revenue_cents, COUNT(*) AS events
        FROM sales
        GROUP BY category
        ORDER BY revenue_cents DESC
    """,
    "inventory_by_supplier": """
        SELECT supplier, SUM(price_cents * stock_count) AS inventory_cents, COUNT(*) AS products
        FROM products
        GROUP BY supplier
        ORDER BY inventory_cents DESC
    """,
    "top_categories": """
        SELECT category, revenue_cents
        FROM (
            SELECT category, SUM(amount_cents) AS revenue_cents,
                   ROW_NUMBER() OVER (ORDER BY SUM(amount_cents) DESC) AS rn
            FROM sales
            GROUP BY category
        ) AS ranked
        WHERE rn <= 3
        ORDER BY revenue_cents DESC
    """,
}


def transpile(sql: str) -> dict:
    try:
        statements = sqlglot.transpile(sql, read="duckdb", write=DIALECT)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "stage": "transpile", "error": f"{type(exc).__name__}: {exc}"}
    if not statements:
        return {"ok": False, "stage": "transpile", "error": "transpile produced no statements"}
    try:
        for statement in statements:
            sqlglot.parse_one(statement, read=DIALECT)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "stage": "parse-back", "error": f"{type(exc).__name__}: {exc}"}
    return {"ok": True, "statements": len(statements), "sample": statements[0][:220].replace("\n", " ")}


def _connect() -> duckdb.DuckDBPyConnection:
    """A DuckDB view over the same Parquet the other engines read.

    The point is not DuckDB. It is that the *logic* runs and produces numbers that can be compared
    with expected.json, so the transpiled SQL is not just syntactically valid.
    """
    settings = get_settings()
    connection = duckdb.connect()
    sales_glob = (settings.root / "data" / "landing" / "sales" / "**" / "*.parquet").as_posix()
    products_file = (settings.root / "data" / "landing" / "products" / "*.parquet").as_posix()
    connection.execute(
        f"CREATE VIEW sales AS SELECT * FROM read_parquet('{sales_glob}', hive_partitioning=true)"
    )
    connection.execute(
        f"CREATE VIEW products AS SELECT * FROM read_parquet('{products_file}')"
    )
    return connection


def execute(sql: str) -> list[list]:
    connection = _connect()
    try:
        return [list(row) for row in connection.execute(sql).fetchall()]
    finally:
        connection.close()


def main() -> int:
    settings = get_settings()
    expected = json.loads((settings.reports / "expected.json").read_text(encoding="utf-8"))

    results = {}
    for name, sql in MODELS.items():
        transpiled = transpile(sql)
        entry = {"transpile": transpiled, "executed": False}
        try:
            rows = execute(sql)
            entry["executed"] = True
            entry["rows"] = len(rows)
            entry["first_row"] = rows[0] if rows else None
        except Exception as exc:  # noqa: BLE001
            entry["error"] = f"{type(exc).__name__}: {exc}"
        results[name] = entry

    # Check the numbers that expected.json already knows, so "executed" is not the only claim.
    revenue = {row[0]: row[1] for row in (execute(MODELS["revenue_by_category"]) or [])}
    inventory = {row[0]: row[1] for row in (execute(MODELS["inventory_by_supplier"]) or [])}

    checks = {
        "every_model_transpiles": all(item["transpile"]["ok"] for item in results.values()),
        "every_model_executes": all(item["executed"] for item in results.values()),
        "revenue_matches_generator": revenue == expected["revenue_cents_by_category"],
        "inventory_matches_generator": inventory == expected["inventory_cents_by_supplier"],
    }

    report = {
        "target": "synapse",
        "dialect": DIALECT,
        "status": "compiled, executed on an equivalent engine (duckdb)",
        "models": results,
        "revenue_by_category": revenue,
        "inventory_by_supplier": inventory,
        "checks": checks,
        "result": "PASS" if all(checks.values()) else "FAIL",
    }

    path = settings.root / REPORT
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    for name, item in results.items():
        state = "transpiled" if item["transpile"]["ok"] else "TRANSPILE FAILED"
        executed = "executed" if item["executed"] else "EXECUTE FAILED"
        print(f"  {name:<24} {state:<18} {executed}  rows={item.get('rows')}")
    print()
    for name, ok in checks.items():
        print(f"  [{'OK  ' if ok else 'FAIL'}] {name}")
    print(f"RESULT: {report['result']}")
    return 0 if report["result"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
