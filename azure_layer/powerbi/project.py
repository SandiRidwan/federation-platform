"""Power BI project files: generated, validated, and honestly labelled `compiled, not executed`.

A `.pbip` project is a folder: a project file, a report folder, and a semantic-model folder whose
TMDL is the model this repository translates and tests. Generating it is worth doing because the
artefact is reviewable and reproducible — but **nothing here opens Power BI**, so the report says
`compiled, not executed` and does not pretend a render happened.

The one check that is real: the generated TMDL contains every measure that was executed in
`azure_layer/tabular/model.py`, with the same DAX text. If the two drift, the artefact stops being
a description of the model that was tested.

    python -m azure_layer.powerbi.project
"""

from __future__ import annotations

import json
from pathlib import Path

from azure_layer.tabular.model import MEASURES, tmdl
from tools.common.config import get_settings

OUT = Path("azure_layer/powerbi/out")
PROJECT_NAME = "federation"

PBIP = {
    "$schema": "https://developer.microsoft.com/json-schemas/fabric/pbip/pbipProperties/1.0.0/schema.json",
    "version": "1.0",
    "artifacts": [{"report": {"path": f"{PROJECT_NAME}.Report"}}],
    "settings": {"enableAutoRecovery": False},
}

REPORT_JSON = {
    "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definitionProperties/1.0.0/schema.json",
    "version": "4.0",
    "datasetReference": {"byPath": {"path": f"../{PROJECT_NAME}.SemanticModel"}},
}


def build() -> dict:
    settings = get_settings()
    out = settings.root / OUT
    (out / f"{PROJECT_NAME}.SemanticModel" / "definition").mkdir(parents=True, exist_ok=True)
    (out / f"{PROJECT_NAME}.Report").mkdir(parents=True, exist_ok=True)

    (out / f"{PROJECT_NAME}.pbip").write_text(json.dumps(PBIP, indent=2), encoding="utf-8")
    (out / f"{PROJECT_NAME}.Report" / "report.json").write_text(json.dumps(REPORT_JSON, indent=2), encoding="utf-8")

    model_text = tmdl(MEASURES)
    (out / f"{PROJECT_NAME}.SemanticModel" / "definition" / "model.tmdl").write_text(model_text, encoding="utf-8")

    return {
        "out": str(OUT),
        "files": sorted(str(path.relative_to(out)).replace("\\", "/") for path in out.rglob("*") if path.is_file()),
        "model_text": model_text,
    }


def main() -> int:
    settings = get_settings()
    built = build()
    model_text = built["model_text"]

    # The real check: every executed measure appears in the generated TMDL, verbatim.
    missing = [measure.name for measure in MEASURES if f'measure "{measure.name}" = ' not in model_text]
    mismatched = [
        measure.name
        for measure in MEASURES
        if f'measure "{measure.name}" = {measure.dax.replace(chr(34), chr(34) * 2)}' not in model_text
    ]

    checks = {
        "project_file_written": any(name.endswith(".pbip") for name in built["files"]),
        "report_folder_written": any(name.startswith(f"{PROJECT_NAME}.Report/") for name in built["files"]),
        "tmdl_written": any(name.endswith("model.tmdl") for name in built["files"]),
        "every_measure_present": not missing,
        "every_measure_dax_verbatim": not mismatched,
    }

    report = {
        "target": "power bi",
        "status": "compiled, not executed (no Power BI Desktop in this environment)",
        "files": built["files"],
        "measures": [measure.name for measure in MEASURES],
        "missing_measures": missing,
        "mismatched_measures": mismatched,
        "checks": checks,
        "result": "PASS" if all(checks.values()) else "FAIL",
        "note": (
            "The .pbip/TMDL artefact is generated from the same measure definitions that were "
            "translated to SQL and executed. Nothing opens Power BI, so no render is claimed."
        ),
    }

    path = settings.reports / "azure_powerbi.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    for name in built["files"]:
        print(f"  wrote {name}")
    print()
    for name, ok in checks.items():
        print(f"  [{'OK  ' if ok else 'FAIL'}] {name}")
    print(f"RESULT: {report['result']}")
    return 0 if report["result"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
