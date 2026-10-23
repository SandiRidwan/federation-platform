"""Data Factory pipelines: the JSON an ADF would accept, and a runner that executes it here.

Two separate claims, kept apart on purpose:

    validate()   the pipeline definition matches the shape ADF requires
    run()        the same activities actually execute, against Azurite

A pipeline that validates and never runs is a YAML file. A pipeline that runs with no
validation is a script that happens to look like ADF. Both are reported, and the status says
`compiled` for the first and `executed` for the second.

The runner supports the four activity types this repository uses — Copy, Lookup, ForEach, Filter —
and refuses anything else by name rather than silently skipping it. A silent skip is how a
pipeline "succeeds" while doing nothing.

    python -m azure_layer.adf.runner
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from azure.storage.blob import BlobServiceClient

from tools.common.config import get_settings
from tools.common.logging_setup import get_logger, setup_logging

log = get_logger("azure.adf")

PIPELINE = Path(__file__).resolve().parent / "pipeline.json"
REPORT = Path("quality/reports/azure_adf.json")

SUPPORTED_ACTIVITIES = {"Copy", "Lookup", "ForEach", "Filter"}

REQUIRED_PIPELINE_KEYS = {"name", "properties"}
REQUIRED_ACTIVITY_KEYS = {"name", "type", "typeProperties"}


def validate(definition: dict) -> dict:
    """Check the fields ADF itself would reject a deployment for.

    Not a JSON-schema library: the four rules that matter are written out, because a generic
    schema error message ("instance does not match schema") tells nobody what to fix.
    """
    problems: list[str] = []
    missing = REQUIRED_PIPELINE_KEYS - set(definition)
    if missing:
        problems.append(f"pipeline is missing {sorted(missing)}")

    properties = definition.get("properties", {})
    activities = properties.get("activities")
    if not activities:
        problems.append("pipeline has no activities")

    for index, activity in enumerate(activities or []):
        missing_activity = REQUIRED_ACTIVITY_KEYS - set(activity)
        if missing_activity:
            problems.append(f"activity[{index}] is missing {sorted(missing_activity)}")
            continue
        if activity["type"] not in SUPPORTED_ACTIVITIES:
            problems.append(
                f"activity {activity['name']!r} has type {activity['type']!r}; the runner supports "
                f"{sorted(SUPPORTED_ACTIVITIES)}"
            )

    return {"valid": not problems, "problems": problems, "activities": len(activities or [])}


def _container():
    settings = get_settings()
    service = BlobServiceClient.from_connection_string(settings.azurite.connection_string)
    return service.get_container_client(settings.azurite.container)


def _run_copy(activity: dict, context: dict) -> dict:
    properties = activity["typeProperties"]
    source = properties["source"]
    sink = properties["sink"]
    data = _container().download_blob(source["blobPath"]).readall()
    _container().upload_blob(name=sink["blobPath"], data=data, overwrite=True)
    return {"activity": activity["name"], "type": "Copy", "bytes": len(data), "to": sink["blobPath"]}


def _run_lookup(activity: dict, context: dict) -> dict:
    properties = activity["typeProperties"]
    blob = properties["blobPath"]
    raw = _container().download_blob(blob).readall()
    rows = [json.loads(line) for line in raw.decode("utf-8").splitlines() if line.strip()]
    context[activity["name"]] = rows
    return {"activity": activity["name"], "type": "Lookup", "rows": len(rows)}


def _run_filter(activity: dict, context: dict) -> dict:
    properties = activity["typeProperties"]
    source_rows = context.get(properties["source"], [])
    field = properties["field"]
    value = properties["equals"]
    kept = [row for row in source_rows if row.get(field) == value]
    context[activity["name"]] = kept
    return {"activity": activity["name"], "type": "Filter", "kept": len(kept), "of": len(source_rows)}


def _run_foreach(activity: dict, context: dict) -> dict:
    properties = activity["typeProperties"]
    items = context.get(properties["items"], [])
    inner = properties["activities"]
    ran = []
    for row in items:
        for child in inner:
            child_context = {**context, "item": row}
            ran.append(_dispatch(child, child_context))
    return {"activity": activity["name"], "type": "ForEach", "iterations": len(items), "inner": ran}


_DISPATCH = {
    "Copy": _run_copy,
    "Lookup": _run_lookup,
    "Filter": _run_filter,
    "ForEach": _run_foreach,
}


def _dispatch(activity: dict, context: dict) -> dict:
    runner = _DISPATCH.get(activity["type"])
    if runner is None:
        raise RuntimeError(f"activity type {activity['type']!r} is not supported by this runner")
    return runner(activity, context)


def run(definition: dict | None = None) -> dict:
    definition = definition or json.loads(PIPELINE.read_text(encoding="utf-8"))
    validation = validate(definition)
    if not validation["valid"]:
        raise RuntimeError(f"pipeline does not validate: {validation['problems']}")

    context: dict[str, Any] = {}
    started = datetime.now(timezone.utc)
    results = [_dispatch(activity, context) for activity in definition["properties"]["activities"]]

    return {
        "pipeline": definition["name"],
        "validation": validation,
        "activities_run": len(results),
        "activity_results": results,
        "context_keys": sorted(key for key in context if key != "item"),
        "ran_at": started.isoformat(),
        "status": "compiled for ADF, executed by the local runner",
    }


def main() -> int:
    setup_logging()
    try:
        result = run()
    except RuntimeError as exc:
        print(f"FAILED: {exc}")
        return 1

    checks = {
        "pipeline_validates": result["validation"]["valid"],
        "every_activity_ran": result["activities_run"] == result["validation"]["activities"],
    }
    report = {**result, "checks": checks, "result": "PASS" if all(checks.values()) else "FAIL"}

    path = get_settings().root / REPORT
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print(json.dumps(result["activity_results"], indent=2))
    print()
    for name, ok in checks.items():
        print(f"  [{'OK  ' if ok else 'FAIL'}] {name}")
    print(f"RESULT: {report['result']}")
    return 0 if report["result"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
