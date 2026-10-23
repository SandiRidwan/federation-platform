"""DAX measures translated to SQL, then executed — because a DAX file nobody can run is a guess.

The tabular model is the part of the Azure stack that is easiest to fake: write a TMDL file, call
it "a semantic model", never evaluate a measure. This module takes the other route. A bounded DAX
subset is parsed and **translated to SQL**, the SQL runs, and the result is compared with a
hand-written query for the same number.

What is supported is stated exactly, because a parser that silently mishandles `CALCULATE` is worse
than one that refuses it:

    SUM(Table[col])                     AVERAGE(Table[col])
    COUNTROWS(Table)                    MIN/MAX(Table[col])
    SUMX(Table, Table[a] * Table[b])    DIVIDE(a, b)
    CALCULATE([measure], Table[col] = "literal")
    references to other measures: [measure]

Anything else raises `Unsupported` with the offending text. That is the point: the boundary is
visible rather than guessed.

    python -m azure_layer.tabular.model
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from azure_layer.synapse.transpile import _connect
from tools.common.config import get_settings

REPORT = Path("quality/reports/azure_tabular.json")
OUT_DIR = Path("azure_layer/tabular/out")

TABLE_MAP = {"Sales": "sales", "Products": "products"}


class Unsupported(Exception):
    """Raised for DAX the translator does not handle. A refusal, not a wrong answer."""


@dataclass(frozen=True)
class Measure:
    name: str
    dax: str
    # An independent SQL query for the same number. Written by hand, so the comparison is between
    # two implementations rather than between the translator and itself.
    check_sql: str


MEASURES = [
    Measure("Total Revenue", "SUM(Sales[amount_cents])", "SELECT SUM(amount_cents) FROM sales"),
    Measure("Total Events", "COUNTROWS(Sales)", "SELECT COUNT(*) FROM sales"),
    Measure("Avg Price", "AVERAGE(Products[price_cents])", "SELECT AVG(price_cents) FROM products"),
    Measure(
        "Inventory Value",
        "SUMX(Products, Products[price_cents] * Products[stock_count])",
        "SELECT SUM(price_cents * stock_count) FROM products",
    ),
    Measure(
        "Revenue per Event",
        "DIVIDE([Total Revenue], [Total Events])",
        "SELECT SUM(amount_cents) * 1.0 / COUNT(*) FROM sales",
    ),
    Measure(
        "Books Revenue",
        'CALCULATE([Total Revenue], Sales[category] = "books")',
        "SELECT SUM(amount_cents) FROM sales WHERE category = 'books'",
    ),
    Measure(
        "Revenue per Product",
        "DIVIDE([Total Revenue], COUNTROWS(Products))",
        "SELECT SUM(amount_cents) * 1.0 / (SELECT COUNT(*) FROM products) FROM sales",
    ),
    Measure(
        "Max Inventory Product",
        "MAX(Products[stock_count])",
        "SELECT MAX(stock_count) FROM products",
    ),
]

_BY_NAME = {measure.name: measure for measure in MEASURES}


def _column(ref: str) -> str:
    """`Sales[amount_cents]` -> `sales.amount_cents`."""
    match = re.fullmatch(r"\s*([A-Za-z_][A-Za-z0-9_]*)\[([A-Za-z_][A-Za-z0-9_]*)\]\s*", ref)
    if not match:
        raise Unsupported(f"not a column reference: {ref!r}")
    table, column = match.group(1), match.group(2)
    if table not in TABLE_MAP:
        raise Unsupported(f"unknown table {table!r}; known: {sorted(TABLE_MAP)}")
    return f"{TABLE_MAP[table]}.{column}"


def _table(name: str) -> str:
    name = name.strip()
    if name not in TABLE_MAP:
        raise Unsupported(f"unknown table {name!r}; known: {sorted(TABLE_MAP)}")
    return TABLE_MAP[name]


def _where(filters: list[tuple[str, str, str]] | None, table: str) -> str:
    """Turn a filter context into a WHERE clause for one table.

    Filters are carried down the recursion rather than applied to an outer query, because a DAX
    measure keeps its grain: `CALCULATE([Total Revenue], Sales[category] = "books")` filters the
    aggregate, not the row set that contains it. Getting this wrong produced the unfiltered total
    the first time — a wrong answer that looked like a passing measure.
    """
    if not filters:
        return ""
    clauses = [f"{table}.{column} = '{value}'" for target, column, value in filters if target == table]
    return " WHERE " + " AND ".join(clauses) if clauses else ""


def translate(dax: str, filters: list[tuple[str, str, str]] | None = None, _depth: int = 0) -> str:
    """Translate one DAX expression to a SQL scalar expression under a filter context.

    Recursive descent over a small grammar. Each branch either returns SQL or raises; there is no
    branch that returns "something close".
    """
    if _depth > 10:
        raise Unsupported("measure references nest more than 10 deep; probably a cycle")

    text = dax.strip()
    filters = filters or []

    # CALCULATE(<expr>, <Table>[<col>] = "<literal>") — adds to the filter context, then recurses.
    calculate = re.fullmatch(
        r'CALCULATE\(\s*(.+?)\s*,\s*([A-Za-z_][A-Za-z0-9_]*)\[([A-Za-z_][A-Za-z0-9_]*)\]\s*=\s*"([^"]*)"\s*\)',
        text,
        re.DOTALL,
    )
    if calculate:
        inner, table, column, value = calculate.groups()
        target = _table(table)
        return translate(inner, [*filters, (target, column, value)], _depth + 1)

    # SUMX(Table, Table[a] * Table[b])
    sumx = re.fullmatch(r"SUMX\(\s*([A-Za-z_][A-Za-z0-9_]*)\s*,\s*(.+?)\s*\)", text, re.DOTALL)
    if sumx:
        table, expression = sumx.groups()
        target = _table(table)
        product = re.fullmatch(r"\s*(.+?)\s*\*\s*(.+?)\s*", expression)
        if not product:
            raise Unsupported(f"SUMX only supports a product of two columns, got {expression!r}")
        left = _column(product.group(1))
        right = _column(product.group(2))
        return f"(SELECT SUM({left} * {right}) FROM {target}{_where(filters, target)})"

    # SUM/AVERAGE/MIN/MAX(Table[col])
    aggregate = re.fullmatch(r"(SUM|AVERAGE|MIN|MAX)\(\s*(.+?)\s*\)", text, re.DOTALL)
    if aggregate:
        function, argument = aggregate.groups()
        column = _column(argument)
        table, name = column.split(".")
        sql_function = {"SUM": "SUM", "AVERAGE": "AVG", "MIN": "MIN", "MAX": "MAX"}[function]
        return f"(SELECT {sql_function}({name}) FROM {table}{_where(filters, table)})"

    # COUNTROWS(Table)
    count_rows = re.fullmatch(r"COUNTROWS\(\s*([A-Za-z_][A-Za-z0-9_]*)\s*\)", text)
    if count_rows:
        target = _table(count_rows.group(1))
        return f"(SELECT COUNT(*) FROM {target}{_where(filters, target)})"

    # DIVIDE(a, b)
    divide = re.fullmatch(r"DIVIDE\(\s*(.+?)\s*,\s*(.+?)\s*\)", text, re.DOTALL)
    if divide:
        numerator, denominator = divide.groups()
        return (
            f"({translate(numerator, filters, _depth + 1)} * 1.0"
            f" / {translate(denominator, filters, _depth + 1)})"
        )

    # A reference to another measure, carrying the filter context.
    reference = re.fullmatch(r"\[([^\]]+)\]", text)
    if reference:
        name = reference.group(1)
        if name not in _BY_NAME:
            raise Unsupported(f"unknown measure {name!r}")
        return translate(_BY_NAME[name].dax, filters, _depth + 1)

    # A bare number, for completeness.
    if re.fullmatch(r"-?\d+(\.\d+)?", text):
        return text

    raise Unsupported(f"cannot translate: {dax[:80]!r}")


def evaluate(measure: Measure) -> dict:
    sql = translate(measure.dax)
    connection = _connect()
    try:
        value = connection.execute(f"SELECT {sql}").fetchone()[0]
        expected = connection.execute(measure.check_sql).fetchone()[0]
    finally:
        connection.close()

    same = (
        value == expected
        if not isinstance(value, float) and not isinstance(expected, float)
        else abs(float(value) - float(expected)) < 1e-6
    )
    return {
        "measure": measure.name,
        "dax": measure.dax,
        "sql": sql,
        "value": value,
        "check_value": expected,
        "matches_check": bool(same),
    }


def tmdl(measures: list[Measure]) -> str:
    """The TMDL text a Power BI project would contain.

    Generated from the same definitions that are translated and executed, so the artefact and the
    verified logic cannot drift.
    """
    lines = [
        "/// Tabular model generated by azure_layer.tabular.model",
        "model Model",
        "\tculture: en-US",
        "\tdefaultPowerBIDataSourceVersion: powerBI_V3",
        "",
        "\ttable Sales",
        "\t\tcolumn amount_cents",
        "\t\t\tdataType: int64",
        "\t\t\tsummarizeBy: sum",
        "\t\tcolumn quantity",
        "\t\t\tdataType: int64",
        "\t\t\tsummarizeBy: sum",
        "",
        "\ttable Products",
        "\t\tcolumn price_cents",
        "\t\t\tdataType: int64",
        "\t\t\tsummarizeBy: sum",
        "\t\tcolumn stock_count",
        "\t\t\tdataType: int64",
        "\t\t\tsummarizeBy: sum",
        "",
        "\ttable _Measures",
    ]
    for measure in measures:
        escaped = measure.dax.replace('"', '""')
        lines.append(f'\t\tmeasure "{measure.name}" = {escaped}')
        lines.append("\t\t\tformatString: 0")
    return "\n".join(lines) + "\n"


def main() -> int:
    settings = get_settings()
    results = []
    errors = {}
    for measure in MEASURES:
        try:
            results.append(evaluate(measure))
        except Unsupported as exc:
            errors[measure.name] = f"Unsupported: {exc}"
        except Exception as exc:  # noqa: BLE001
            errors[measure.name] = f"{type(exc).__name__}: {exc}"

    out = settings.root / OUT_DIR
    out.mkdir(parents=True, exist_ok=True)
    (out / "model.tmdl").write_text(tmdl(MEASURES), encoding="utf-8")

    checks = {
        "every_measure_translated": len(results) == len(MEASURES),
        "every_measure_matches_independent_sql": all(item["matches_check"] for item in results),
        "tmdl_written": (out / "model.tmdl").exists(),
    }

    report = {
        "target": "analysis services (tabular)",
        "status": "compiled (TMDL) + executed (DAX translated to SQL on duckdb)",
        "supported_dax": [
            "SUM/AVERAGE/MIN/MAX(Table[col])", "COUNTROWS(Table)", "SUMX(Table, a * b)",
            "DIVIDE(a, b)", "CALCULATE([measure], Table[col] = \"literal\")", "[measure] references",
        ],
        "measures": results,
        "errors": errors,
        "tmdl_file": str(OUT_DIR / "model.tmdl"),
        "checks": checks,
        "result": "PASS" if all(checks.values()) else "FAIL",
    }

    path = settings.root / REPORT
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")

    for item in results:
        state = "ok" if item["matches_check"] else "MISMATCH"
        print(f"  {item['measure']:<24} {state:<9} value={item['value']} check={item['check_value']}")
    for name, error in errors.items():
        print(f"  {name:<24} REFUSED   {error[:90]}")
    print()
    for name, ok in checks.items():
        print(f"  [{'OK  ' if ok else 'FAIL'}] {name}")
    print(f"RESULT: {report['result']}")
    return 0 if report["result"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
