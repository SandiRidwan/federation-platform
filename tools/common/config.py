"""Shared configuration. One place reads the environment.

Every endpoint here has a default that works on this machine with no cloud account: Azurite on
its standard ports, Trino on 8080, Druid broker on 8082. A real deployment changes environment
variables, not code.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


@dataclass(frozen=True)
class TrinoSettings:
    host: str
    port: int
    user: str

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}"


@dataclass(frozen=True)
class DruidSettings:
    host: str
    broker_port: int
    router_port: int
    coordinator_port: int

    @property
    def broker_url(self) -> str:
        return f"http://{self.host}:{self.broker_port}"

    @property
    def router_url(self) -> str:
        return f"http://{self.host}:{self.router_port}"

    @property
    def coordinator_url(self) -> str:
        return f"http://{self.host}:{self.coordinator_port}"


@dataclass(frozen=True)
class AzuriteSettings:
    host: str
    port: int
    account: str
    key: str
    container: str

    @property
    def blob_endpoint(self) -> str:
        return f"http://{self.host}:{self.port}/{self.account}"

    @property
    def connection_string(self) -> str:
        return (
            "DefaultEndpointsProtocol=http;"
            f"AccountName={self.account};"
            f"AccountKey={self.key};"
            f"BlobEndpoint=http://{self.host}:{self.port}/{self.account};"
        )


@dataclass(frozen=True)
class S3Settings:
    endpoint: str
    access_key: str
    secret_key: str
    region: str
    warehouse: str


@dataclass(frozen=True)
class Settings:
    root: Path
    duckdb_path: Path
    warehouse: Path
    trino: TrinoSettings
    druid: DruidSettings
    azurite: AzuriteSettings
    s3: S3Settings
    extra: dict[str, str] = field(default_factory=dict)

    @property
    def reports(self) -> Path:
        return self.root / "quality" / "reports"

    @property
    def data(self) -> Path:
        return self.root / "data"


# Azurite's well-known development account. Not a secret: it is published in Microsoft's docs
# and only works against the emulator.
AZURITE_ACCOUNT = "devstoreaccount1"
AZURITE_KEY = (
    "Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw=="
)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    load_dotenv(ROOT / ".env", override=False)

    duckdb_path = Path(os.environ.get("DUCKDB_PATH", "./data/warehouse/federation.duckdb"))
    if not duckdb_path.is_absolute():
        duckdb_path = (ROOT / duckdb_path).resolve()

    warehouse = Path(os.environ.get("FEDERATION_WAREHOUSE", "./data/warehouse/tables"))
    if not warehouse.is_absolute():
        warehouse = (ROOT / warehouse).resolve()

    return Settings(
        root=ROOT,
        duckdb_path=duckdb_path,
        warehouse=warehouse,
        trino=TrinoSettings(
            host=os.environ.get("TRINO_HOST", "localhost"),
            # 8090, not 8080: Airflow in the neighbouring stack already owns 8080 on this machine.
            port=_int("TRINO_PORT", 8090),
            user=os.environ.get("TRINO_USER", "federation"),
        ),
        druid=DruidSettings(
            host=os.environ.get("DRUID_HOST", "localhost"),
            broker_port=_int("DRUID_BROKER_PORT", 8082),
            router_port=_int("DRUID_ROUTER_PORT", 8888),
            coordinator_port=_int("DRUID_COORDINATOR_PORT", 8081),
        ),
        azurite=AzuriteSettings(
            host=os.environ.get("AZURITE_HOST", "127.0.0.1"),
            port=_int("AZURITE_PORT", 10000),
            account=os.environ.get("AZURITE_ACCOUNT", AZURITE_ACCOUNT),
            key=os.environ.get("AZURITE_KEY", AZURITE_KEY),
            container=os.environ.get("AZURITE_CONTAINER", "lakehouse"),
        ),
        # This repository's own MinIO (docker-compose service `minio`, host port 9092). Trino
        # reaches it as `minio:9000` on the compose network; see infra/trino/etc/catalog.
        s3=S3Settings(
            endpoint=os.environ.get("S3_ENDPOINT", "http://127.0.0.1:9092"),
            access_key=os.environ.get("S3_ACCESS_KEY", "federation"),
            secret_key=os.environ.get("S3_SECRET_KEY", "federation123"),
            region=os.environ.get("S3_REGION", "us-east-1"),
            warehouse=os.environ.get("S3_WAREHOUSE", "s3://federation/iceberg"),
        ),
    )
