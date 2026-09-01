from datetime import datetime
from pydantic import BaseModel, Field

class RepositoryCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    location: str = Field(min_length=1, max_length=500)
    passphrase: str | None = None

class RepositoryOut(BaseModel):
    id: int
    name: str
    location: str
    created_at: datetime
    model_config = {"from_attributes": True}

class BackupCreate(BaseModel):
    repository_id: int
    sources: list[str] = Field(min_length=1)
    archive_name: str | None = None
    compression: str = "zstd,3"
    excludes: list[str] = Field(default_factory=list)

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
    archive: str
    paths: list[str] = Field(default_factory=list)
    target: str

class ScheduleCreate(BaseModel):
    name: str
    repository_id: int
    cron: str
    sources: list[str]
    enabled: bool = True
