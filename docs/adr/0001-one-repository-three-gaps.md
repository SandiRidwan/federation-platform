# ADR 0001 — One repository for three gaps, and what it does not close

**Status:** accepted · 2026-10-23

## Context

Three separate gaps came out of the resume ledger, and each one is a different layer of the same
system:

| Gap | What the job descriptions ask for |
|---|---|
| 7.C2 | Hive, Presto, Druid — distributed query engines (Wikimedia) |
| 8.A | Synapse, ADF, ADLS Gen2, SSAS, Power BI, Azure DevOps (Thales ×2, NTT) |
| 8.B | vector databases, embeddings, labelling, inter-annotator agreement, evaluation |

The tempting move is three small repositories, each proving one tool. That produces three
repositories that each invent their own data, and none of them proves the layers can coexist.

## Decision

One repository, one generator, four layers, one question that crosses all of them:

> Which category holds the most inventory value, and which earned the most revenue?

answered by Druid (fast aggregation) → Trino (join over the Iceberg catalog) → Azurite (the joined
result parked as a blob) → the retrieval layer (the document that states the fact, with a quote).

Every number is checked against the generator, not against another engine's answer. A pipeline
where each stage is green but the numbers do not line up is exactly the failure this is built to
catch, and it is the failure three separate demos cannot catch at all.

## Consequences

* One `make verify` runs everything. Eleven green reports are worthless if nobody runs them.
* The data is synthetic and deterministic, so parity is testable at all. That is also the limit:
  this proves the engines agree on *this* data, not on a production workload.
* The layers are coupled by design: changing the generator's layout touches ingestion, Trino,
  Druid, Azurite and the LLM corpus. That coupling is the point, and it is also why the layout
  changes are recorded here rather than made quietly.

## What this does not close

* **6.G2 — `dbt build` against a real Snowflake.** Needs an account; none exists. Stays
  `compiled, not executed`.
* **7.D — Java service.** One job description, and the direction is backend JVM.
* **7.E — academic record, Linux kernel packaging.** Structural; no repository creates a degree.
* **8.C — people management.** Experience, not an artefact.
* **8.D — Talend / SAP / Hadoop / .NET.** Two job descriptions, and a stack the target roles are
  moving away from.

Naming those here is the point. A gap that is honestly still open is cheaper than a repository
that pretends to close it.
