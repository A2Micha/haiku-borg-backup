from datetime import datetime
from sqlalchemy import String, Integer, DateTime, Text, Boolean, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .database import Base

class Repository(Base):
    __tablename__ = "repositories"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    location: Mapped[str] = mapped_column(String(500))
    encrypted_passphrase: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    jobs: Mapped[list["BackupJob"]] = relationship(back_populates="repository", cascade="all, delete-orphan")

class BackupJob(Base):
    __tablename__ = "backup_jobs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    repository_id: Mapped[int] = mapped_column(ForeignKey("repositories.id"), index=True)
    archive_name: Mapped[str] = mapped_column(String(220))
    sources_json: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(30), default="queued", index=True)
    return_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    log: Mapped[str] = mapped_column(Text, default="")
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    repository: Mapped[Repository] = relationship(back_populates="jobs")

class Schedule(Base):
    __tablename__ = "schedules"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    repository_id: Mapped[int] = mapped_column(ForeignKey("repositories.id"), index=True)
    cron: Mapped[str] = mapped_column(String(80))
    sources_json: Mapped[str] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
