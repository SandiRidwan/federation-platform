"""ADLS Gen2 through Azurite, and what that does and does not prove.

Azurite is Microsoft's emulator. It speaks the **Blob** API, and ADLS Gen2 is Blob plus a
hierarchical namespace: real directory semantics, atomic rename, POSIX ACLs. Azurite does not
implement the DFS endpoint, so what runs here is the Blob half of the stack — the wire protocol,
the SDK, the container and blob layout — and **not** directory rename or ACL inheritance.

That distinction is the point of this module. "We tested ADLS Gen2" and "we tested the Blob API
that ADLS Gen2 is built on" are different sentences, and only the second one is true.

    python -m azure_layer.storage.adls
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

from azure.storage.blob import BlobServiceClient

from tools.common.config import get_settings
from tools.common.logging_setup import get_logger, setup_logging

log = get_logger("azure.storage")

REPORT = Path("quality/reports/azure_storage.json")


def client() -> BlobServiceClient:
    settings = get_settings()
    return BlobServiceClient.from_connection_string(settings.azurite.connection_string)


def ensure_container() -> str:
    settings = get_settings()
    service = client()
    container = service.get_container_client(settings.azurite.container)
    try:
        container.create_container()
    except Exception:  # noqa: BLE001 - exists is the normal case
        pass
    return settings.azurite.container


def upload_landing() -> dict:
    """Upload the generated dataset, then read it back and compare bytes.

    Byte comparison, not "no exception": a write that returns 201 and stores nothing is the failure
    this kind of check exists for.
    """
    settings = get_settings()
    container_name = ensure_container()
    service = client()
    container = service.get_container_client(container_name)

    landing = settings.root / "data" / "landing"
    files = sorted(path for path in landing.rglob("*.parquet"))
    if not files:
        raise RuntimeError("no landing files; run `python -m ingestion.generate` first")

    uploaded = []
    total_bytes = 0
    started = time.perf_counter()

    # Empty the container first. Without this the listing check compares against blobs from earlier
    # runs — the count was 32 for 31 uploaded files because a `products.parquet` from the previous
    # layout was still there. A stale blob is indistinguishable from a wrong upload by count alone.
    removed = 0
    for blob in list(container.list_blobs()):
        container.delete_blob(blob.name)
        removed += 1

    for path in files:
        relative = path.relative_to(landing).as_posix()
        # A path with slashes is how a directory *looks* in Blob storage. It is not a directory.
        blob_name = f"landing/{relative}"
        data = path.read_bytes()
        container.upload_blob(name=blob_name, data=data, overwrite=True)
        total_bytes += len(data)
        uploaded.append({"blob": blob_name, "bytes": len(data)})

    elapsed = time.perf_counter() - started

    # Read one file back and compare it byte for byte.
    sample = uploaded[0]
    downloaded = container.download_blob(sample["blob"]).readall()
    source = (landing / sample["blob"].removeprefix("landing/")).read_bytes()

    # A control file the pipeline reads. Written from the generator's own output, so the pipeline
    # is driven by data rather than by a hard-coded list that would drift.
    expected = json.loads((settings.reports / "expected.json").read_text(encoding="utf-8"))
    control = "\n".join(
        json.dumps({"category": name, "products": count})
        for name, count in expected["products_by_category"].items()
    )
    container.upload_blob(name="control/categories.jsonl", data=control.encode("utf-8"), overwrite=True)

    listed = [blob.name for blob in container.list_blobs(name_starts_with="landing/")]

    return {
        "container": container_name,
        "blobs_removed_before_upload": removed,
        "files_uploaded": len(uploaded),
        "bytes_uploaded": total_bytes,
        "upload_seconds": round(elapsed, 2),
        "upload_bytes_per_second": int(total_bytes / elapsed) if elapsed else 0,
        "readback_blob": sample["blob"],
        "readback_matches_source": downloaded == source,
        "blobs_listed": len(listed),
        "flat_namespace_note": (
            "Blob API only. ADLS Gen2's hierarchical namespace (directory rename, ACL inheritance) "
            "is not exercised, because Azurite does not implement the DFS endpoint."
        ),
    }


def main() -> int:
    setup_logging()
    started = datetime.now(timezone.utc)
    result = upload_landing()

    checks = {
        "every_file_uploaded": result["files_uploaded"] > 0,
        "readback_is_byte_identical": result["readback_matches_source"],
        "listing_matches_upload_count": result["blobs_listed"] == result["files_uploaded"],
    }
    report = {
        "ran_at": started.isoformat(),
        **result,
        "checks": checks,
        "result": "PASS" if all(checks.values()) else "FAIL",
        "endpoint": get_settings().azurite.blob_endpoint,
    }

    path = get_settings().root / REPORT
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print(f"container   : {result['container']}")
    print(f"files       : {result['files_uploaded']}  bytes: {result['bytes_uploaded']:,}")
    print(f"throughput  : {result['upload_bytes_per_second']:,} B/s")
    print(f"readback    : {'identical' if result['readback_matches_source'] else 'DIFFERENT'}")
    print()
    for name, ok in checks.items():
        print(f"  [{'OK  ' if ok else 'FAIL'}] {name}")
    print(f"RESULT: {report['result']}")
    return 0 if report["result"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
