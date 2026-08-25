"""Tenant-scoped immutable object storage for source snapshots and papers."""

import asyncio
import gzip
import hashlib
from abc import ABC, abstractmethod
from pathlib import Path, PurePosixPath
from uuid import UUID

import boto3

from app.core.config import settings


def validate_object_key(key: str) -> str:
    path = PurePosixPath(key)
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("Object key must be a non-empty relative POSIX path")
    return str(path)


def research_object_prefix(
    *, organization_id: UUID | None, project_id: UUID, run_id: UUID
) -> str:
    tenant = str(organization_id) if organization_id else "personal"
    return f"tenants/{tenant}/projects/{project_id}/runs/{run_id}"


class ResearchObjectStore(ABC):
    @abstractmethod
    async def put(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str,
        metadata: dict[str, str] | None = None,
    ) -> str: ...

    @abstractmethod
    async def get(self, key: str) -> bytes: ...

    @abstractmethod
    async def healthcheck(self) -> None: ...


class LocalResearchObjectStore(ResearchObjectStore):
    def __init__(self, root: Path | None = None):
        self.root = (root or settings.MEDIA_DIR / "literature-research").resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        candidate = (self.root / validate_object_key(key)).resolve()
        if self.root != candidate and self.root not in candidate.parents:
            raise ValueError("Object key escapes storage root")
        return candidate

    async def put(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str,
        metadata: dict[str, str] | None = None,
    ) -> str:
        del content_type, metadata
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(path.write_bytes, data)
        return validate_object_key(key)

    async def get(self, key: str) -> bytes:
        return await asyncio.to_thread(self._path(key).read_bytes)

    async def healthcheck(self) -> None:
        await asyncio.to_thread(self.root.mkdir, parents=True, exist_ok=True)


class S3ResearchObjectStore(ResearchObjectStore):
    def __init__(self) -> None:
        self.bucket = settings.S3_BUCKET
        self.client = boto3.client(
            "s3",
            endpoint_url=settings.S3_ENDPOINT,
            aws_access_key_id=settings.S3_ACCESS_KEY,
            aws_secret_access_key=settings.S3_SECRET_KEY,
            region_name=settings.S3_REGION,
        )

    async def put(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str,
        metadata: dict[str, str] | None = None,
    ) -> str:
        object_key = validate_object_key(key)
        await asyncio.to_thread(
            self.client.put_object,
            Bucket=self.bucket,
            Key=object_key,
            Body=data,
            ContentType=content_type,
            Metadata=metadata or {},
        )
        return object_key

    async def get(self, key: str) -> bytes:
        response = await asyncio.to_thread(
            self.client.get_object,
            Bucket=self.bucket,
            Key=validate_object_key(key),
        )
        return await asyncio.to_thread(response["Body"].read)

    async def healthcheck(self) -> None:
        await asyncio.to_thread(self.client.head_bucket, Bucket=self.bucket)


def get_research_object_store() -> ResearchObjectStore:
    if settings.S3_ENDPOINT:
        return S3ResearchObjectStore()
    return LocalResearchObjectStore()


async def store_source_page(
    store: ResearchObjectStore,
    *,
    organization_id: UUID | None,
    project_id: UUID,
    run_id: UUID,
    source: str,
    query_id: str,
    page_number: int,
    raw_body: bytes,
) -> tuple[str, str]:
    """Persist a gzip snapshot and return object key plus raw response SHA-256."""
    raw_sha256 = hashlib.sha256(raw_body).hexdigest()
    compressed = gzip.compress(raw_body, mtime=0)
    prefix = research_object_prefix(
        organization_id=organization_id, project_id=project_id, run_id=run_id
    )
    key = f"{prefix}/sources/{source}/{query_id}/page-{page_number:04d}-{raw_sha256[:16]}.raw.gz"
    object_key = await store.put(
        key,
        compressed,
        content_type="application/gzip",
        metadata={"raw-sha256": raw_sha256},
    )
    return object_key, raw_sha256
