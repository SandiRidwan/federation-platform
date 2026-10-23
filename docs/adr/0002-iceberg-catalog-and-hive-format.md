# ADR 0002 — Iceberg over Postgres + S3, and Hive's format through a file metastore

**Status:** accepted · 2026-10-23

## Context

Two claims have to be true at once:

1. **Iceberg** is a real shared catalog: one engine writes the table metadata, a different engine
   reads it, and both agree on what the table contains.
2. **Hive** appears in the job description. The table format and partition layout must be Hive's.

Both are demonstrated on a laptop, with no cloud account.

## Decision

**Iceberg.** `pyiceberg` writes with a JDBC catalog whose metadata lives in Postgres
(`postgres:5434`, database `iceberg`). The data files live in object storage
(`s3://federation/iceberg`). Trino reads the same catalog through its Iceberg connector
(`iceberg.catalog.type=jdbc`).

* Postgres, not SQLite: Trino cannot read a SQLite catalog, and a catalog only one engine can read
  is not a shared catalog.
* S3, not `file://`: Trino's Iceberg connector refuses the local scheme
  (`Unsupported file system scheme file`). A local warehouse is writable by Python and invisible to
  Trino. The S3 endpoint is this repository's own `s3` service (`adobe/s3mock`), on host port 9092;
  an earlier version pointed at a MinIO owned by a neighbouring project, which meant the stack only
  ran while that project did.
* `s3mock`, not MinIO: MinIO stopped publishing its images on Docker Hub (they moved to quay.io) and
  the pull failed. `s3mock` speaks the same PUT/GET/LIST wire protocol, which is all Iceberg needs.

**Hive.** Trino's Hive connector with `hive.metastore=file` and the filesystem metastore in
`data/warehouse/tables`. The table format, the `dt=` partition layout and the `external_location`
are Hive's; the metastore *service* is not running, and the report says so rather than implying a
Hive cluster.

## The four traps that cost the most time

All four were silent — every statement returned 200 while the data was invisible:

1. **`trinodb/trino:latest` moved.** Trino 484 disabled the local filesystem by default, so the
   file metastore failed with `Supported schemes: []`. The image is now pinned to `trino:484`, and
   the catalog sets `fs.local.enabled=true`.
2. **`file://` and `local://` are not interchangeable.** With `file://`, Trino 484 routed table
   reads through Hadoop's LocalFileSystem, which resolved the path under the JVM temp directory:
   every table was created and every `COUNT(*)` came back 0. `local://` uses Trino's own provider
   and reads the mounted directory. External locations now use `local://`.
3. **The file metastore resolves under the JVM temp directory.** Its metadata landed in
   `/tmp/data/warehouse/tables`, which disappears with the container. The data directory is
   therefore mounted twice (`/data` and `/tmp/data`), and the metastore now persists on the host.
4. **`external_location` must be a directory.** Pointing it at `products.parquet` created a valid
   table that read zero rows. The generator writes `data/landing/products/part-00000.parquet`, and
   the table points at the directory.

And one that is not Trino's fault: the file metastore does **not** discover `dt=` directories. The
partition metadata has to be registered (`CALL hive.system.sync_partition_metadata`), otherwise the
table is created and reads nothing.

## Consequences

* `python -m ingestion.iceberg_load` then `python -m federation.trino.catalog --register` is the
  whole setup. `trino_catalog.json` records the row counts (60,000 sales, 5,000 products) against
  the generator, not against a second Trino query.
* Single-node Trino: no workers, no fault tolerance. What is proven is catalog sharing and query
  execution, not cluster operations.
* The `local://` scheme is a Trino extension. A real deployment points the same catalog at S3 or
  HDFS and changes nothing else.
