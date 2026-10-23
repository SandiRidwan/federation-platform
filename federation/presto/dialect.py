"""Presto, as a dialect rather than a second cluster.

Presto's SQL lineage lives on in Trino — same dialect, same semantics for everything this
repository asks. Standing up a second Presto cluster would cost a gigabyte and prove nothing new:
the interesting question is whether **the query text is portable**, not whether a second process
starts.

So this module does two things and keeps them apart:

    transpile_check()  the Presto dialect accepts the query and it parses back
    run_on_trino()     the query actually executes, on Trino

`presto` is therefore reported as **compiled, executed on Trino** — never as "Presto ran".
"""

from __future__ import annotations

import sqlglot

from federation.trino.catalog import query as trino_query

DIALECT = "presto"


def transpile_check(sql: str) -> dict:
    """Translate to the Presto dialect and re-parse the result.

    Re-parsing is the part that catches something real: sqlglot can emit a statement it cannot
    read back when a construct has no equivalent, and that is exactly the case that would fail on
    a real Presto cluster.
    """
    try:
        statements = sqlglot.transpile(sql, read="trino", write=DIALECT)
    except Exception as exc:  # noqa: BLE001 - the exception type is library-specific
        return {"ok": False, "stage": "transpile", "error": f"{type(exc).__name__}: {exc}"}

    if not statements:
        return {"ok": False, "stage": "transpile", "error": "transpile produced no statements"}

    try:
        for statement in statements:
            sqlglot.parse_one(statement, read=DIALECT)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "stage": "parse-back", "error": f"{type(exc).__name__}: {exc}"}

    return {
        "ok": True,
        "statements": len(statements),
        "sample": statements[0][:200].replace("\n", " "),
    }


def run_on_trino(sql: str) -> dict:
    result = trino_query(sql)
    return {"engine": "trino", "rows": result["rows"], "row_count": result["row_count"]}


def evaluate(sql: str) -> dict:
    return {
        "dialect": DIALECT,
        "transpile": transpile_check(sql),
        "execution": run_on_trino(sql),
        "status": "compiled, executed on trino",
    }
