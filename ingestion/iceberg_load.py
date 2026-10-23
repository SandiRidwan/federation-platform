"""Write the shared dataset into Iceberg, through a catalog Postgres owns.

The interesting part is not that Python can write Parquet. It is that the **table metadata lives
in Postgres**, and a completely separate engine — Trino, in a JVM — reads the same catalog. If the
row counts agree, two independent implementations agree about what the table contains.

    python -m ingestion.iceberg_load
"""

from __future__ import annotations

import json

import pyarrow as pa
import pyarrow.fs as pa_fs
from pyiceberg.catalog.sql import SqlCatalog
from pyiceberg.exceptions import NamespaceAlreadyExistsError, NoSuchTableError
from pyiceberg.partitioning import PartitionField, PartitionSpec
from pyiceberg.schema import Schema
from pyiceberg.transforms import IdentityTransform
from pyiceberg.types import IntegerType, LongType, NestedField, StringType

from ingestion.generate import PRODUCT_SCHEMA, SALE_SCHEMA, build
from tools.common.config import get_settings
from tools.common.logging_setup import get_logger, setup_logging

log = get_logger("iceberg_load")

NAMESPACE = "lakehouse"

PRODUCT_ICEBERG = Schema(
    NestedField(1, "product_id", LongType(), required=False),
    NestedField(2, "category", StringType(), required=False),
    NestedField(3, "supplier", StringType(), required=False),
    NestedField(4, "price_cents", LongType(), required=False),
    NestedField(5, "stock_count", IntegerType(), required=False),
    NestedField(6, "rating", IntegerType(), required=False),
)

SALE_ICEBERG = Schema(
    NestedField(1, "event_id", LongType(), required=False),
    NestedField(2, "product_id", LongType(), required=False),
    NestedField(3, "category", StringType(), required=False),
    NestedField(4, "dt", StringType(), required=False),
    NestedField(5, "amount_cents", LongType(), required=False),
    NestedField(6, "quantity", IntegerType(), required=False),
)


def catalog() -> SqlCatalog:
    """A JDBC catalog over Postgres, with the data files in this repository's MinIO.

    Two choices, both forced by what actually runs:

    * **Postgres, not SQLite.** Trino cannot read a SQLite catalog, and a catalog only one engine
      can read is not a shared catalog.
    * **S3, not `file://`.** Trino's Iceberg connector refuses the local filesystem scheme
      (`Unsupported file system scheme file`), so a local warehouse is readable by Python and
      invisible to Trino. S3 is the smallest thing both engines speak, and it is what production
      uses anyway. The endpoint comes from settings so the repo does not depend on a container
      owned by another project.
    """
    settings = get_settings()
    return SqlCatalog(
        "federation",
        **{
            "uri": "postgresql+psycopg2://federation:federation@127.0.0.1:5434/iceberg",
            "warehouse": settings.s3.warehouse,
            "s3.endpoint": settings.s3.endpoint,
            "s3.access-key-id": settings.s3.access_key,
            "s3.secret-access-key": settings.s3.secret_key,
            "s3.region": settings.s3.region,
        },
    )


def _arrow(schema: pa.Schema, rows: list[dict]) -> pa.Table:
    return pa.Table.from_pylist(rows, schema=schema)


def ensure_bucket() -> dict:
    """Create the warehouse bucket if it is missing, through pyarrow's S3 filesystem.

    MinIO has no "create this bucket at start" flag, and an image just to run `mc mb` is another
    thing to pull. pyarrow already speaks S3 for the Iceberg writer, so the bucket is created with
    the client that will use it.
    """
    settings = get_settings()
    filesystem = pa_fs.S3FileSystem(
        endpoint_override=settings.s3.endpoint,
        access_key=settings.s3.access_key,
        secret_key=settings.s3.secret_key,
        region=settings.s3.region,
        scheme="http",
    )
    bucket = settings.s3.warehouse.split("://", 1)[1].split("/", 1)[0]
    existed = filesystem.get_file_info(bucket).type != pa_fs.FileType.NotFound
    if not existed:
        filesystem.create_bucket(bucket)
    return {"bucket": bucket, "created": not existed, "endpoint": settings.s3.endpoint}


def load(seed: int | None = None) -> dict:
    dataset = build(seed) if seed else build()
    ensure_bucket()
    cat = catalog()

    try:
        cat.create_namespace(NAMESPACE)
    except NamespaceAlreadyExistsError:
        pass

    # Partitioned by day, so a date filter can skip files. `dt` is a string in the source data;
    # Iceberg partitions on it directly, which is honest: the column really is a date string.
    sales_spec = PartitionSpec(PartitionField(source_id=4, field_id=1000, transform=IdentityTransform(), name="dt"))

    results = {}
    for identifier, schema, spec, rows, arrow_schema in (
        (f"{NAMESPACE}.products", PRODUCT_ICEBERG, PartitionSpec(), dataset.products, PRODUCT_SCHEMA),
        (f"{NAMESPACE}.sales", SALE_ICEBERG, sales_spec, dataset.sales, SALE_SCHEMA),
    ):
        try:
            cat.drop_table(identifier)
        except NoSuchTableError:
            pass
        table = cat.create_table(identifier, schema=schema, partition_spec=spec)
        table.append(_arrow(arrow_schema, rows))

        reloaded = cat.load_table(identifier)
        scanned = reloaded.scan().to_arrow().num_rows
        files = [item.file for item in reloaded.scan().plan_files()]
        results[identifier] = {
            "rows_appended": len(rows),
            "rows_read_back": scanned,
            "data_files": len(files),
            "snapshots": len(reloaded.metadata.snapshots),
            "partition_spec": str(reloaded.spec()).replace("\n", " "),
        }
        log.info("loaded %s", identifier)

    return results


def main() -> int:
    setup_logging()
    results = load()
    settings = get_settings()
    path = settings.reports / "iceberg_load.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(json.dumps(results, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
