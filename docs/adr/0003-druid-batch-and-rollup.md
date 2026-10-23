# ADR 0003 — Druid: batch ingestion, rollup off, deterministic timestamps

**Status:** accepted · 2026-10-23

## Context

The plan said "Druid single-node (Docker): ingestion from Kafka → datasource". What actually runs is
a batch `index_parallel` task over a local JSON file.

The claim being tested is **query latency on the same rows as the other engines**, not the
transport. A broker would add a moving part and change nothing about that measurement. The
deviation is recorded here instead of being quietly described as Kafka in the README.

## Decision

* **Batch ingestion, not Kafka.** `federation.druid.ingest` submits an `index_parallel` task
  reading `data/druid-ingest/{sales,products}.json`, which `ingestion.generate` writes. One
  generator, three storage shapes.
* **Rollup off.** Druid's default rollup collapses identical rows: the first ingestion produced
  49,399 rows for 60,000 events, and `COUNT(*)` stopped being comparable with the other engines.
  Off, so "the same rows" means the same rows.
* **`dynamic` partitioning, `forceGuaranteedRollup: false`.** A hashed partitions spec requires
  guaranteed rollup, which would collapse the rows again.
* **Fixed `__time` values.** Sales use their `dt`; products use the first day of the dataset.
  Both are pure functions of the generator, so re-running `--load` replaces segments instead of
  adding to them.

## What went wrong, and what it cost

**A silent double count.** The first version of the generator wrote Druid input to `data/druid/`,
while the container mounts `data/druid-ingest/`. A manually copied file made it work, and that copy
was invisible. When the generator was fixed, the products file's `__time` moved from `2026-10-01` to
`2026-09-24`; the new task replaced only its own interval, the old segments survived, and the
products datasource held two copies of every row. The symptom was not an error: `COUNT(*)` on sales
was correct, `SUM(price_cents * stock_count)` on products was exactly double. That is the shape of
bug this repository exists to make visible — a sum that is wrong by a factor of two, with every
check green.

**Deleting a datasource does not work the way the docs suggest.** `DELETE
/druid/coordinator/v1/datasources/{name}?kill=true` answered 500 with an empty body on both the
coordinator and the router. A `kill` task with interval `1900-01-01/2100-01-01` reported `SUCCESS`
and the segments stayed queryable for more than five minutes. The reliable reset is a fresh
container (`docker compose rm -f druid && docker compose up -d druid`), which is what the README
says. A retry loop around a broken delete would have hidden the problem, not fixed it.

## Consequences

* Re-running `--load` with the same generated data is idempotent, because the intervals are
  identical. Changing the generator's timestamps requires a fresh Druid container — and that
  requirement is written down.
* Druid won every parity case at 60,000 rows (see `federation_parity.json`), including the join.
  That is Trino's fixed planning + Iceberg metadata overhead on a small dataset, not evidence that
  Druid is universally faster. The join case was included expecting the opposite result; the report
  keeps that finding instead of dropping the case.
* Single JVM: coordinator, overlord, broker, historical, middle-manager, router and an embedded
  ZooKeeper in one process. What is proven is ingestion and query latency, not cluster operations.
