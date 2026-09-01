from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

Compression = Literal["zstd,3", "zstd,6", "lz4", "none"]


class RepositoryCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    location: str = Field(min_length=1, max_length=500)
    passphrase: str | None = Field(default=None, max_length=4096)
    initialize: bool = False
    encryption: Literal["repokey-blake2", "none"] = "repokey-blake2"

    @field_validator("name", "location")
    @classmethod
    def _strip_required(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be empty")
        return value


class RepositoryOut(BaseModel):
    id: int
    name: str
    location: str
    created_at: datetime
    model_config = {"from_attributes": True}


class BackupCreate(BaseModel):
    repository_id: int
    sources: list[str] = Field(min_length=1, max_length=128)
    archive_name: str | None = Field(default=None, max_length=180)
    compression: Compression = "zstd,3"
    excludes: list[str] = Field(default_factory=list, max_length=256)

    @field_validator("archive_name")
    @classmethod
    def _validate_archive_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            return None
        if "::" in value or "/" in value or "\x00" in value:
            raise ValueError("archive name must not contain '/', '::' or NUL")
        return value


class BackupJobOut(BaseModel):
    id: int
    repository_id: int
    archive_name: str
    status: str
    return_code: int | None
    log: str
    started_at: datetime | None
    finished_at: datetime | None
    model_config = {"from_attributes": True}


class RestoreCreate(BaseModel):
    repository_id: int
    archive: str = Field(min_length=1, max_length=220)
    paths: list[str] = Field(default_factory=list, max_length=512)
    target: str = Field(min_length=1, max_length=500)

    @field_validator("archive")
    @classmethod
    def _validate_archive(cls, value: str) -> str:
        value = value.strip()
        if "::" in value or "\x00" in value:
            raise ValueError("invalid archive name")
        return value


class ScheduleCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    repository_id: int
    cron: str = Field(min_length=9, max_length=80)
    sources: list[str] = Field(min_length=1, max_length=128)
    compression: Compression = "zstd,3"
    excludes: list[str] = Field(default_factory=list, max_length=256)
    enabled: bool = True
