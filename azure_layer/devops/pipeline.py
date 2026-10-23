"""Azure DevOps: the pipeline YAML, validated, with a local equivalent that actually runs.

Two claims again, kept apart:

    validate()  the YAML has the fields Azure Pipelines requires (trigger, pool, stages/jobs/steps)
    local()     the same commands run here, through the project's own task runner

`local()` is not "the Azure pipeline ran". It is the same *commands* on the same machine, which is
what catches a broken command before a push does. The report says which of the two happened.

    python -m azure_layer.devops.pipeline
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import yaml

from tools.common.config import get_settings

YAML_PATH = Path(__file__).resolve().parent / "azure-pipelines.yml"


def validate(path: Path = YAML_PATH) -> dict:
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    problems: list[str] = []

    if "trigger" not in document:
        problems.append("missing `trigger`")
    if "pool" not in document:
        problems.append("missing `pool`")

    stages = document.get("stages") or []
    if not stages:
        problems.append("no stages")

    steps = []
    for stage in stages:
        for job in stage.get("jobs") or []:
            for step in job.get("steps") or []:
                steps.append(step)

    if not steps:
        problems.append("no steps in any job")

    for index, step in enumerate(steps):
        # A step must be either a script or a task. Anything else is a silent no-op in Azure.
        if not ("script" in step or "task" in step):
            problems.append(f"step[{index}] is neither a script nor a task")

    return {
        "valid": not problems,
        "problems": problems,
        "stages": len(stages),
        "steps": len(steps),
        "scripts": [step["script"].strip() for step in steps if "script" in step],
    }


def local(validation: dict) -> dict:
    """Run the pipeline's scripts here, in order, and stop at the first failure.

    The DAX/Synapse/parity scripts are skipped on purpose in this fast path: they need the Docker
    stack. `make verify` runs them. Running the two that need nothing is still a real check — a
    typo in `pytest -q` is caught before a push.
    """
    runnable = [script for script in validation["scripts"] if script.startswith(("ruff", "pytest"))]
    results = []
    for script in runnable:
        # `python -m ruff`, not bare `ruff`: a shell resolves whichever ruff is on PATH, which on
        # this machine was a different install and reported failures the project's own ruff did
        # not. Same tool, same arguments, invoked through the interpreter that owns them.
        parts = script.split()
        command = [sys.executable, "-m", parts[0], *parts[1:]]
        completed = subprocess.run(
            command, cwd=str(get_settings().root), capture_output=True, text=True, timeout=600
        )
        results.append(
            {
                "script": script,
                "exit_code": completed.returncode,
                "tail": "\n".join(((completed.stdout or "") + (completed.stderr or "")).strip().splitlines()[-3:]),
            }
        )
        if completed.returncode != 0:
            break
    return {"ran": len(results), "results": results, "all_passed": all(r["exit_code"] == 0 for r in results)}


def main() -> int:
    settings = get_settings()
    validation = validate()
    executed = local(validation) if validation["valid"] else {"ran": 0, "results": [], "all_passed": False}

    checks = {
        "yaml_validates": validation["valid"],
        "local_equivalent_ran": executed["ran"] > 0,
        "local_equivalent_passed": executed["all_passed"],
    }

    report = {
        "target": "azure devops",
        "status": "compiled (yaml) + executed (same scripts, locally)",
        "file": str(YAML_PATH.relative_to(settings.root)).replace("\\", "/"),
        "validation": validation,
        "local_equivalent": executed,
        "checks": checks,
        "result": "PASS" if all(checks.values()) else "FAIL",
        "note": (
            "The YAML is validated against the fields Azure Pipelines requires. The commands are "
            "then run locally; that is not the same as a hosted agent run, and the report does not "
            "claim it is."
        ),
    }

    path = settings.reports / "azure_devops.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print(f"stages {validation['stages']} · steps {validation['steps']} · scripts {len(validation['scripts'])}")
    for item in executed["results"]:
        print(f"  local: {item['script']:<18} exit={item['exit_code']}")
    print()
    for name, ok in checks.items():
        print(f"  [{'OK  ' if ok else 'FAIL'}] {name}")
    print(f"RESULT: {report['result']}")
    return 0 if report["result"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
