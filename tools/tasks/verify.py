"""One command that runs every component and fails if any of them did not pass.

`make verify` is this file. It exists because a repository of eleven green reports proves nothing
if nobody runs all eleven: the interesting failures are the ones that only appear when the pipeline
runs end to end, after the data has been regenerated.

Two things are checked, and they are different:

    the process exit code   a script can exit 0 while writing a FAIL report
    the report's `result`   every report that carries a verdict must say PASS

The second check is the one that matters. A step that prints "FAIL" and returns 0 would otherwise
be invisible in a wall of green.

    python -m tools.tasks.verify
    python -m tools.tasks.verify --list
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REPORTS = ROOT / "quality" / "reports"

# Reports without a `result` field: raw measurements, not verdicts.
NO_VERDICT = {"expected.json", "corpus.json", "iceberg_load.json", "druid_load.json"}


@dataclass(frozen=True)
class Step:
    name: str
    module: str
    args: tuple[str, ...] = ()
    needs_docker: bool = False


STEPS: tuple[Step, ...] = (
    # data first: everything downstream compares against the generator's own totals
    Step("data", "ingestion.generate"),
    Step("iceberg-load", "ingestion.iceberg_load", needs_docker=True),
    Step("trino-catalog", "federation.trino.catalog", ("--register",), needs_docker=True),
    Step("druid-load", "federation.druid.ingest", ("--load",), needs_docker=True),
    Step("federation-parity", "federation.parity", ("--runs", "5"), needs_docker=True),
    Step("azure-storage", "azure_layer.storage.adls", needs_docker=True),
    Step("azure-adf", "azure_layer.adf.runner"),
    Step("azure-synapse", "azure_layer.synapse.transpile"),
    Step("azure-tabular", "azure_layer.tabular.model"),
    Step("azure-powerbi", "azure_layer.powerbi.project"),
    Step("azure-devops", "azure_layer.devops.pipeline"),
    Step("llm-corpus", "llm.corpus"),
    Step("llm-labeling", "llm.labeling.kappa"),
    Step("llm-ner", "llm.ner"),
    Step("llm-eval", "llm.eval.harness"),
    Step("llm-preference", "llm.preference"),
    Step("bench-retrieval", "bench.retrieval"),
    Step("bench-cost", "bench.cost"),
    Step("crosslayer", "crosslayer.question", needs_docker=True),
    Step("tests", "pytest", ("-q",)),
    Step("lint", "ruff", ("check", ".")),
)


def run_step(step: Step) -> dict:
    started = time.perf_counter()
    completed = subprocess.run(
        [sys.executable, "-m", step.module, *step.args],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=3600,
    )
    elapsed = round(time.perf_counter() - started, 1)
    output = ((completed.stdout or "") + (completed.stderr or "")).strip()
    return {
        "step": step.name,
        "command": " ".join(("python -m", step.module, *step.args)),
        "exit_code": completed.returncode,
        "seconds": elapsed,
        "tail": "\n".join(output.splitlines()[-6:]),
        "output": output,
    }


def report_verdicts() -> list[dict]:
    verdicts = []
    for path in sorted(REPORTS.glob("*.json")):
        if path.name in NO_VERDICT:
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            verdicts.append({"report": path.name, "result": f"unreadable: {exc}"})
            continue
        if "result" in payload:
            verdicts.append(
                {
                    "report": path.name,
                    "result": payload["result"],
                    "failed_checks": [name for name, ok in payload.get("checks", {}).items() if not ok],
                }
            )
    return verdicts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run every component and check every verdict.")
    parser.add_argument("--list", action="store_true", help="print the steps and exit")
    parser.add_argument(
        "--skip-docker",
        action="store_true",
        help="skip the steps that need the compose stack (useful for a quick logic check)",
    )
    args = parser.parse_args(argv)

    if args.list:
        for step in STEPS:
            marker = "docker" if step.needs_docker else "local "
            print(f"  [{marker}] {step.name:<18} python -m {step.module} {' '.join(step.args)}")
        return 0

    results = []
    for step in STEPS:
        if args.skip_docker and step.needs_docker:
            print(f"  [SKIP] {step.name}")
            continue
        print(f"  [ .. ] {step.name}", end="\r", flush=True)
        result = run_step(step)
        results.append(result)
        status = "OK  " if result["exit_code"] == 0 else "FAIL"
        print(f"  [{status}] {step.name:<18} {result['seconds']:>6.1f}s")
        if result["exit_code"] != 0:
            print("\n".join(f"        {line}" for line in result["output"].splitlines()[-40:]))

    verdicts = report_verdicts()
    print()
    for verdict in verdicts:
        failed = verdict.get("failed_checks") or []
        suffix = f"  failed: {', '.join(failed)}" if failed else ""
        print(f"  {verdict['report']:<30} {verdict['result']}{suffix}")

    process_failures = [item["step"] for item in results if item["exit_code"] != 0]
    report_failures = [item["report"] for item in verdicts if item["result"] != "PASS"]
    print()
    if process_failures:
        print(f"  steps failed   : {', '.join(process_failures)}")
    if report_failures:
        print(f"  reports failed : {', '.join(report_failures)}")
    ok = not process_failures and not report_failures
    print(f"RESULT: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

