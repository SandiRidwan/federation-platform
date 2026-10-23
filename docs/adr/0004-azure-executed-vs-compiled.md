# ADR 0004 — Azure: executed where it is free, compiled where it is not

**Status:** accepted · 2026-10-23

## Context

Five Azure data-platform services appear in the job descriptions. None of them can be run without
a subscription, and a repository that says "tested ADLS Gen2" when it tested a local emulator is
making a claim that falls apart in the first interview.

## Decision

**Status per target, never one aggregate verdict.** The same rule as the warehouse parity work in
SENTINEL: each target gets its own report and its own word.

| Target | What runs | Status |
|---|---|---|
| ADLS Gen2 | Azurite, Blob API: upload, read back, byte-compare, throughput | **executed** (Blob half only) |
| Data Factory | Pipeline JSON validated against the fields ADF requires; a local runner executes the same activities (copy → lookup → filter → for-each) | **executed** (local runner), artefact **compiled** |
| Synapse SQL | Models transpiled to T-SQL with `sqlglot`, executed on DuckDB | **compiled**, executed on an equivalent engine |
| Analysis Services / DAX | Tabular model as TMDL; every measure translated to SQL and compared with an independent SQL query | **compiled** (no DAX engine) |
| Power BI | `.pbip` project + TMDL rendered; every measure present verbatim | **compiled, not executed** (needs Power BI Desktop) |
| Azure DevOps | `azure-pipelines.yml` validated; the same `ruff` and `pytest` commands run locally | **compiled** (YAML), local equivalent **executed** |

The sentence this repository can defend, and no more:

> ADLS Gen2's Blob API and the pipeline's activities I ran against the official emulator; Synapse,
> SSAS, Power BI and the hosted Azure Pipelines agent I compiled, not ran.

## Why not just say "Azure"

Because Azurite is the Blob API, not ADLS Gen2. ADLS Gen2 is Blob plus a hierarchical namespace:
directory rename, atomic operations, POSIX ACL inheritance. Azurite does not implement the DFS
endpoint, so none of that is exercised here. The report carries that sentence in the artefact, not
only in this ADR.

## Consequences

* `azure_storage.json`, `azure_adf.json`, `azure_synapse.json`, `azure_tabular.json`,
  `azure_powerbi.json` and `azure_devops.json` each carry their own `checks` and `result`. `make
  verify` fails if any of them says FAIL.
* The local ADF runner is a real check of the pipeline's *shape* — a copy activity pointing at a
  blob that does not exist fails here before it fails in a hosted agent. It is not a check of ADF's
  scheduler, integration runtime or linked-service authentication.
* The DAX translation is deliberately partial: an unsupported function raises `Unsupported` with
  its name instead of returning a plausible wrong number. `RANKX` is refused, and the test asserts
  that refusal.
