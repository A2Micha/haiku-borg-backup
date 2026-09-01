import asyncio
import json
import os
import re
import shutil
import time
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Coroutine

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import Depends, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from .borg import (
    BORG_REPO_HOST_ROOT_RAW,
    HOST_SOURCE_ROOT_RAW,
    BorgError,
    MOCK_BORG,
    borg_available,
    ensure_repository_parent,
    friendly_borg_error,
    local_repository_container_path,
    run_capture,
    safe_restore_target,
    stream_process,
    validate_source_paths,
)
from .database import Base, SessionLocal, engine, get_db
from .models import BackupJob, Repository, Schedule
from .schemas import BackupCreate, BackupJobOut, RepositoryCreate, RepositoryOut, RestoreCreate, ScheduleCreate
from .security import encrypt_secret, using_default_secret

Base.metadata.create_all(bind=engine)

subscribers: dict[int, set[WebSocket]] = {}
running_processes: dict[int, asyncio.subprocess.Process] = {}
cancel_requested: set[int] = set()
_repo_locks: dict[int, asyncio.Lock] = {}
_background_tasks: set[asyncio.Task] = set()
scheduler = AsyncIOScheduler(timezone=os.getenv("TZ", "Europe/Berlin"))
_metrics_cache: dict[int, tuple[float, dict]] = {}
METRICS_TTL = int(os.getenv("METRICS_TTL", "300"))
METRICS_CONCURRENCY = max(1, int(os.getenv("METRICS_CONCURRENCY", "2")))
_metrics_semaphore = asyncio.Semaphore(METRICS_CONCURRENCY)
APP_VERSION = "0.4.1"
_UNIX_WEEKDAYS = ["sun", "mon", "tue", "wed", "thu", "fri", "sat"]


def _spawn(coro: Coroutine) -> asyncio.Task:
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return task


def _now() -> datetime:
    return datetime.utcnow()


def get_repo(db: Session, repo_id: int) -> Repository:
    repo = db.get(Repository, repo_id)
    if not repo:
        raise HTTPException(404, "Repository not found")
    return repo


def _schedule_config(schedule: Schedule) -> dict:
    try:
        raw = json.loads(schedule.sources_json)
    except (json.JSONDecodeError, TypeError):
        raw = []
    if isinstance(raw, list):
        return {"sources": raw, "compression": "zstd,3", "excludes": []}
    if not isinstance(raw, dict):
        return {"sources": [], "compression": "zstd,3", "excludes": []}
    return {
        "sources": raw.get("sources", []),
        "compression": raw.get("compression", "zstd,3"),
        "excludes": raw.get("excludes", []),
    }


def _serialize_schedule(schedule: Schedule) -> dict:
    cfg = _schedule_config(schedule)
    job = scheduler.get_job(f"schedule-{schedule.id}") if scheduler.running else None
    return {
        "id": schedule.id,
        "name": schedule.name,
        "repository_id": schedule.repository_id,
        "cron": schedule.cron,
        "sources": cfg["sources"],
        "compression": cfg["compression"],
        "excludes": cfg["excludes"],
        "enabled": schedule.enabled,
        "next_run_at": job.next_run_time.isoformat() if job and job.next_run_time else None,
    }


def _expand_unix_weekday_token(token: str) -> list[str]:
    """Convert Unix-cron weekday numbers (0/7=Sun, 1=Mon) to names.

    APScheduler 3.x historically treats numeric 0 as Monday even when parsing a
    crontab expression. Using names avoids that mismatch and preserves standard
    Unix cron semantics for schedules already stored by older Borg Manager builds.
    """
    token = token.strip().lower()
    if not token:
        raise ValueError("empty day-of-week token")
    if token == "*":
        return ["*"]
    if re.search(r"[a-z]", token):
        return [token]

    step = 1
    base = token
    if "/" in token:
        base, step_text = token.split("/", 1)
        step = int(step_text)
        if step < 1 or step > 7:
            raise ValueError("day-of-week step must be between 1 and 7")

    def day_number(text: str) -> int:
        value = int(text)
        if value == 7:
            value = 0
        if value < 0 or value > 6:
            raise ValueError("day-of-week must be 0-7")
        return value

    if base == "*":
        numbers = list(range(0, 7))[::step]
    elif "-" in base:
        start_text, end_text = base.split("-", 1)
        start, end = day_number(start_text), day_number(end_text)
        if start <= end:
            numbers = list(range(start, end + 1))
        else:
            numbers = list(range(start, 7)) + list(range(0, end + 1))
        numbers = numbers[::step]
    else:
        numbers = [day_number(base)]

    return [_UNIX_WEEKDAYS[number] for number in numbers]


def _cron_for_apscheduler(expr: str) -> str:
    parts = expr.strip().split()
    if len(parts) != 5:
        raise ValueError(f"Wrong number of fields; got {len(parts)}, expected 5")
    weekday = parts[4]
    if weekday != "*":
        converted: list[str] = []
        for token in weekday.split(","):
            converted.extend(_expand_unix_weekday_token(token))
        # De-duplicate while preserving the user's ordering.
        parts[4] = ",".join(dict.fromkeys(converted))
    return " ".join(parts)


def _validate_cron(expr: str) -> CronTrigger:
    try:
        normalized = _cron_for_apscheduler(expr)
        return CronTrigger.from_crontab(normalized, timezone=os.getenv("TZ", "Europe/Berlin"))
    except (ValueError, TypeError) as exc:
        raise HTTPException(422, f"Invalid cron expression: {exc}") from exc


def _dir_size(path: Path) -> int | None:
    try:
        if not path.exists() or not path.is_dir():
            return None
        total = 0
        for root, _, files in os.walk(path, onerror=lambda _: None):
            for name in files:
                try:
                    total += os.path.getsize(os.path.join(root, name))
                except OSError:
                    pass
        return total
    except OSError:
        return None


def _number(value, default=0):
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return default


def _float(value, default=0.0):
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return default


def _archive_row(raw: dict) -> dict:
    stats = raw.get("stats") or {}
    return {
        "name": raw.get("name") or raw.get("archive") or "archive",
        "time": raw.get("start") or raw.get("time") or raw.get("end"),
        "duration": _float(raw.get("duration")),
        "nfiles": _number(stats.get("nfiles", raw.get("nfiles"))),
        "original_size": _number(stats.get("original_size")),
        "compressed_size": _number(stats.get("compressed_size")),
        "deduplicated_size": _number(stats.get("deduplicated_size")),
    }


def _append_log(job: BackupJob, text: str) -> None:
    if text:
        job.log = ((job.log or "") + text.rstrip() + "\n")[-500000:]


def _friendly_schedule_archive(name: str) -> str:
    base = re.sub(r"[^A-Za-z0-9._-]+", "-", name.strip()).strip("-._") or "scheduled"
    return f"{base}-{_now().strftime('%Y-%m-%d_%H-%M-%S-%f')[:-3]}"


def _normalize_sources_or_http(values: list[str]) -> list[str]:
    try:
        return validate_source_paths(values, require_exists=not MOCK_BORG)
    except BorgError as exc:
        raise HTTPException(422, str(exc)) from exc


async def _repository_metrics(repo: Repository, force: bool = False) -> dict:
    now = time.monotonic()
    cached = _metrics_cache.get(repo.id)
    if cached and not force and now - cached[0] < METRICS_TTL:
        return cached[1]

    result = {
        "id": repo.id,
        "name": repo.name,
        "location": repo.location,
        "ok": False,
        "error": None,
        "archive_count": 0,
        "last_archive_at": None,
        "logical_size": 0,
        "compressed_size": 0,
        "deduplicated_size": 0,
        "physical_size": None,
        "disk_total": None,
        "disk_free": None,
        "dedup_ratio": None,
        "compression_ratio": None,
        "unique_chunks": None,
        "total_chunks": None,
        "history": [],
    }

    if MOCK_BORG:
        result.update(
            {
                "ok": True,
                "archive_count": 3,
                "logical_size": 48_000_000_000,
                "compressed_size": 31_000_000_000,
                "deduplicated_size": 12_000_000_000,
                "physical_size": 12_600_000_000,
                "dedup_ratio": 4.0,
                "compression_ratio": 1.55,
                "history": [
                    {"name": "demo-1", "time": "2026-08-28T02:00:00", "duration": 52, "nfiles": 40210, "original_size": 13_000_000_000, "compressed_size": 8_700_000_000, "deduplicated_size": 4_100_000_000},
                    {"name": "demo-2", "time": "2026-08-29T02:00:00", "duration": 39, "nfiles": 40502, "original_size": 14_100_000_000, "compressed_size": 9_100_000_000, "deduplicated_size": 1_300_000_000},
                    {"name": "demo-3", "time": "2026-08-30T02:00:00", "duration": 43, "nfiles": 40788, "original_size": 14_600_000_000, "compressed_size": 9_300_000_000, "deduplicated_size": 900_000_000},
                ],
            }
        )
        _metrics_cache[repo.id] = (now, result)
        return result

    async with _metrics_semaphore:
        try:
            list_code, list_out, list_err = await run_capture(
                ["borg", "list", "--json", repo.location], repo.encrypted_passphrase
            )
            if list_code:
                raise BorgError(friendly_borg_error(list_out, list_err, "borg list failed"))
            listed = json.loads(list_out or "{}")
            archive_list = listed.get("archives", []) if isinstance(listed, dict) else []
            result["archive_count"] = len(archive_list)
            if archive_list:
                result["last_archive_at"] = max(
                    (a.get("start") or a.get("time") or "" for a in archive_list), default=""
                ) or None

            info_code, info_out, info_err = await run_capture(
                ["borg", "info", "--json", "--last", "30", repo.location], repo.encrypted_passphrase
            )
            if info_code:
                raise BorgError(friendly_borg_error(info_out, info_err, "borg info failed"))
            info = json.loads(info_out or "{}")
            archive_info = info.get("archives", []) if isinstance(info, dict) else []
            history = [_archive_row(a) for a in archive_info]
            history.sort(key=lambda x: x.get("time") or "")
            result["history"] = history
            if history:
                result["last_archive_at"] = history[-1].get("time") or result["last_archive_at"]

            cache_stats = ((info.get("cache") or {}).get("stats") or {}) if isinstance(info, dict) else {}
            total_original = _number(cache_stats.get("total_size"))
            total_compressed = _number(cache_stats.get("total_csize"))
            total_unique = _number(cache_stats.get("total_unique_csize"))
            if not total_original and history:
                total_original = sum(x["original_size"] for x in history)
            if not total_compressed and history:
                total_compressed = sum(x["compressed_size"] for x in history)
            result["logical_size"] = total_original
            result["compressed_size"] = total_compressed
            result["deduplicated_size"] = total_unique
            result["unique_chunks"] = cache_stats.get("unique_chunks")
            result["total_chunks"] = cache_stats.get("total_chunks")
            if total_original and total_unique:
                result["dedup_ratio"] = round(total_original / total_unique, 2)
            if total_original and total_compressed:
                result["compression_ratio"] = round(total_original / total_compressed, 2)

            local_path = local_repository_container_path(repo.location)
            if local_path is not None and local_path.exists():
                result["physical_size"] = await asyncio.to_thread(_dir_size, local_path)
                try:
                    usage = shutil.disk_usage(local_path)
                    result["disk_total"] = usage.total
                    result["disk_free"] = usage.free
                except OSError:
                    pass
            if not result["deduplicated_size"] and result["physical_size"]:
                result["deduplicated_size"] = result["physical_size"]
            result["ok"] = True
        except (BorgError, json.JSONDecodeError, OSError) as exc:
            result["error"] = str(exc)

    _metrics_cache[repo.id] = (now, result)
    return result


async def publish(job_id: int, line: str):
    dead = []
    for ws in subscribers.get(job_id, set()):
        try:
            await ws.send_json({"job_id": job_id, "line": line})
        except Exception:
            dead.append(ws)
    for ws in dead:
        subscribers.get(job_id, set()).discard(ws)


def _queue_backup(db: Session, repository_id: int, sources: list[str], archive_name: str | None) -> BackupJob:
    archive = archive_name or f"backup-{_now().strftime('%Y-%m-%d_%H-%M-%S-%f')[:-3]}"
    job = BackupJob(
        repository_id=repository_id,
        archive_name=archive,
        sources_json=json.dumps(sources),
        status="queued",
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def _mark_failed(db: Session, job: BackupJob, message: str, return_code: int = 1) -> None:
    job.status = "failed"
    job.return_code = return_code
    job.finished_at = _now()
    _append_log(job, f"ERROR: {message}")
    db.commit()


async def execute_backup(job_id: int, compression: str, excludes: list[str]):
    db = SessionLocal()
    repo_id: int | None = None
    try:
        job = db.get(BackupJob, job_id)
        repo = db.get(Repository, job.repository_id) if job else None
        if not job or not repo:
            return
        repo_id = repo.id
        lock = _repo_locks.setdefault(repo.id, asyncio.Lock())
        if lock.locked():
            _append_log(job, "Waiting for another Borg operation on this repository…")
            db.commit()

        async with lock:
            db.refresh(job)
            if job_id in cancel_requested or job.status == "cancelled":
                if not job.finished_at:
                    job.finished_at = _now()
                    job.return_code = -1
                    db.commit()
                return

            job.status = "running"
            job.started_at = _now()
            job.finished_at = None
            db.commit()
            sources = json.loads(job.sources_json)
            args = ["borg", "create", "--stats", "--progress", "--compression", compression]
            for pattern in excludes:
                args += ["--exclude", pattern]
            args += [f"{repo.location}::{job.archive_name}", *sources]
            last_commit = time.monotonic()

            try:
                async for line, proc in stream_process(args, repo.encrypted_passphrase):
                    if proc is not None:
                        running_processes[job_id] = proc
                    if job_id in cancel_requested:
                        if proc is not None and proc.returncode is None:
                            proc.terminate()
                        continue
                    if line:
                        _append_log(job, line)
                        await publish(job_id, line)
                    if time.monotonic() - last_commit >= 0.75:
                        db.commit()
                        last_commit = time.monotonic()

                if job_id in cancel_requested:
                    job.status = "cancelled"
                    job.return_code = -1
                else:
                    job.status = "success"
                    job.return_code = 0
                    _metrics_cache.pop(repo.id, None)
            except asyncio.CancelledError:
                job.status = "cancelled"
                job.return_code = -1
                raise
            except Exception as exc:
                if job_id in cancel_requested:
                    job.status = "cancelled"
                    job.return_code = -1
                    _append_log(job, "Backup cancelled by user.")
                else:
                    job.status = "failed"
                    job.return_code = 1
                    _append_log(job, f"ERROR: {exc}")
                    await publish(job_id, f"ERROR: {exc}")
            finally:
                job.finished_at = _now()
                db.commit()
                running_processes.pop(job_id, None)
                cancel_requested.discard(job_id)
    finally:
        if repo_id is not None:
            _metrics_cache.pop(repo_id, None)
        db.close()


async def execute_schedule(schedule_id: int):
    db = SessionLocal()
    try:
        schedule = db.get(Schedule, schedule_id)
        if not schedule or not schedule.enabled:
            return
        cfg = _schedule_config(schedule)
        if not cfg["sources"]:
            return
        repo = db.get(Repository, schedule.repository_id)
        if not repo:
            return
        archive = _friendly_schedule_archive(schedule.name)
        try:
            sources = validate_source_paths(cfg["sources"], require_exists=not MOCK_BORG)
        except BorgError as exc:
            job = _queue_backup(db, schedule.repository_id, cfg["sources"], archive)
            _mark_failed(db, job, str(exc))
            return
        job = _queue_backup(db, schedule.repository_id, sources, archive)
        await execute_backup(job.id, cfg["compression"], cfg["excludes"])
    finally:
        db.close()


def register_schedule(schedule: Schedule):
    job_id = f"schedule-{schedule.id}"
    if scheduler.get_job(job_id):
        scheduler.remove_job(job_id)
    if not schedule.enabled:
        return
    trigger = _validate_cron(schedule.cron)
    scheduler.add_job(
        execute_schedule,
        trigger=trigger,
        args=[schedule.id],
        id=job_id,
        replace_existing=True,
        coalesce=True,
        max_instances=1,
        misfire_grace_time=3600,
    )


def _recover_interrupted_jobs(db: Session) -> int:
    stale = db.scalars(select(BackupJob).where(BackupJob.status.in_(["queued", "running"]))).all()
    for job in stale:
        job.status = "interrupted"
        job.return_code = -2
        job.finished_at = _now()
        _append_log(job, "Job was interrupted because Borg Manager restarted.")
    if stale:
        db.commit()
    return len(stale)


@asynccontextmanager
async def lifespan(app: FastAPI):
    db = SessionLocal()
    try:
        recovered = _recover_interrupted_jobs(db)
        if recovered:
            print(f"Recovered {recovered} interrupted backup job(s).")
        scheduler.start()
        for schedule in db.scalars(select(Schedule)).all():
            try:
                register_schedule(schedule)
            except HTTPException as exc:
                print(f"Skipping invalid schedule {schedule.id}: {exc.detail}")
    finally:
        db.close()

    yield

    for proc in list(running_processes.values()):
        if proc.returncode is None:
            proc.terminate()
    if scheduler.running:
        scheduler.shutdown(wait=False)


app = FastAPI(title="Borg Manager API", version=APP_VERSION, lifespan=lifespan)


@app.get("/api/health")
def health():
    warnings: list[str] = []
    if using_default_secret():
        warnings.append("APP_SECRET is still using a default/example value.")
    if MOCK_BORG:
        warnings.append("MOCK_BORG=1: Borg operations are simulated.")
    if BORG_REPO_HOST_ROOT_RAW and not os.path.isabs(BORG_REPO_HOST_ROOT_RAW):
        warnings.append("BORG_REPO_ROOT is relative; host-path mapping is limited. Prefer an absolute host path.")
    return {
        "ok": True,
        "version": APP_VERSION,
        "mock_borg": MOCK_BORG,
        "borg_available": borg_available(),
        "scheduler_running": scheduler.running,
        "timezone": os.getenv("TZ", "Europe/Berlin"),
        "borg_repo_host_root": BORG_REPO_HOST_ROOT_RAW,
        "host_source_root": HOST_SOURCE_ROOT_RAW,
        "active_processes": len(running_processes),
        "background_tasks": len(_background_tasks),
        "warnings": warnings,
    }


@app.get("/api/dashboard")
def dashboard(db: Session = Depends(get_db)):
    repos = db.scalars(select(Repository)).all()
    jobs = db.scalars(select(BackupJob).order_by(desc(BackupJob.id)).limit(30)).all()
    schedules = db.scalars(select(Schedule)).all()
    return {
        "repository_count": len(repos),
        "schedule_count": len(schedules),
        "active_schedule_count": sum(1 for s in schedules if s.enabled),
        "running_count": sum(1 for j in jobs if j.status in {"queued", "running"}),
        "recent_jobs": [BackupJobOut.model_validate(j) for j in jobs[:12]],
        "successful_recent": sum(1 for j in jobs if j.status == "success"),
        "failed_recent": sum(1 for j in jobs if j.status in {"failed", "interrupted"}),
    }


@app.get("/api/metrics")
async def metrics(force: bool = False, db: Session = Depends(get_db)):
    repos = db.scalars(select(Repository).order_by(Repository.name)).all()
    repo_metrics = await asyncio.gather(*[_repository_metrics(repo, force=force) for repo in repos])
    total_size = sum((r.get("physical_size") or r.get("deduplicated_size") or 0) for r in repo_metrics)
    total_archives = sum(r.get("archive_count", 0) for r in repo_metrics)
    return {
        "repositories": repo_metrics,
        "total_repository_size": total_size,
        "total_archives": total_archives,
        "updated_at": _now().isoformat() + "Z",
    }


@app.get("/api/repositories", response_model=list[RepositoryOut])
def list_repositories(db: Session = Depends(get_db)):
    return db.scalars(select(Repository).order_by(Repository.name)).all()


@app.post("/api/repositories", response_model=RepositoryOut)
async def create_repository(payload: RepositoryCreate, db: Session = Depends(get_db)):
    existing = db.scalar(select(Repository).where(Repository.name == payload.name))
    if existing:
        raise HTTPException(409, "Repository name already exists")
    encrypted = encrypt_secret(payload.passphrase)
    try:
        local_path = local_repository_container_path(payload.location)
        if payload.initialize:
            if payload.encryption != "none" and not payload.passphrase:
                raise HTTPException(422, "A passphrase is required for encrypted repositories")
            if local_path is not None:
                ensure_repository_parent(payload.location)
                if local_path.exists() and any(local_path.iterdir()):
                    raise HTTPException(409, f"Repository destination is not empty: {payload.location}")
            code, out, err = await run_capture(
                ["borg", "init", "--encryption", payload.encryption, payload.location], encrypted
            )
            if code:
                raise BorgError(friendly_borg_error(out, err, "borg init failed"))
        else:
            if local_path is not None and not local_path.exists():
                raise HTTPException(404, f"Repository path does not exist: {payload.location}")
            code, out, err = await run_capture(["borg", "info", "--json", payload.location], encrypted)
            if code:
                raise BorgError(friendly_borg_error(out, err, "Could not open repository"))
    except BorgError as exc:
        raise HTTPException(502, str(exc)) from exc

    repo = Repository(name=payload.name, location=payload.location, encrypted_passphrase=encrypted)
    db.add(repo)
    db.commit()
    db.refresh(repo)
    _metrics_cache.pop(repo.id, None)
    return repo


@app.get("/api/repositories/{repo_id}/status")
async def repository_status(repo_id: int, db: Session = Depends(get_db)):
    repo = get_repo(db, repo_id)
    try:
        code, out, err = await run_capture(["borg", "info", "--json", repo.location], repo.encrypted_passphrase)
        if code:
            raise BorgError(friendly_borg_error(out, err, "Could not open repository"))
        return {"ok": True, "repository": json.loads(out or "{}") if out.strip() else {}}
    except (BorgError, json.JSONDecodeError) as exc:
        raise HTTPException(502, str(exc)) from exc


@app.post("/api/repositories/{repo_id}/check")
async def repository_check(repo_id: int, db: Session = Depends(get_db)):
    repo = get_repo(db, repo_id)
    lock = _repo_locks.setdefault(repo.id, asyncio.Lock())
    if lock.locked():
        raise HTTPException(409, "Repository is busy with another Borg operation")
    try:
        async with lock:
            code, out, err = await run_capture(
                ["borg", "check", "--repository-only", repo.location], repo.encrypted_passphrase
            )
        if code:
            raise BorgError(friendly_borg_error(out, err, "borg check failed"))
        return {"ok": True, "output": (out + "\n" + err).strip()[-10000:]}
    except BorgError as exc:
        raise HTTPException(502, str(exc)) from exc


@app.delete("/api/repositories/{repo_id}", status_code=204)
def delete_repository(repo_id: int, db: Session = Depends(get_db)):
    repo = get_repo(db, repo_id)
    active = db.scalar(
        select(BackupJob).where(
            BackupJob.repository_id == repo.id,
            BackupJob.status.in_(["queued", "running"]),
        )
    )
    if active:
        raise HTTPException(409, "Repository has an active backup job and cannot be removed")
    linked_schedules = db.scalars(select(Schedule).where(Schedule.repository_id == repo.id)).all()
    for item in linked_schedules:
        sid = f"schedule-{item.id}"
        if scheduler.get_job(sid):
            scheduler.remove_job(sid)
        db.delete(item)
    _metrics_cache.pop(repo.id, None)
    db.delete(repo)
    db.commit()


@app.post("/api/backups", response_model=BackupJobOut)
async def start_backup(payload: BackupCreate, db: Session = Depends(get_db)):
    repo = get_repo(db, payload.repository_id)
    sources = _normalize_sources_or_http(payload.sources)
    job = _queue_backup(db, repo.id, sources, payload.archive_name)
    _spawn(execute_backup(job.id, payload.compression, payload.excludes))
    return job


@app.get("/api/jobs", response_model=list[BackupJobOut])
def list_jobs(db: Session = Depends(get_db)):
    return db.scalars(select(BackupJob).order_by(desc(BackupJob.id)).limit(200)).all()


@app.get("/api/jobs/{job_id}", response_model=BackupJobOut)
def get_job(job_id: int, db: Session = Depends(get_db)):
    job = db.get(BackupJob, job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return job


@app.post("/api/jobs/{job_id}/cancel")
async def cancel_job(job_id: int, db: Session = Depends(get_db)):
    job = db.get(BackupJob, job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    if job.status not in {"queued", "running"}:
        raise HTTPException(409, f"Job is already {job.status}")
    cancel_requested.add(job_id)
    proc = running_processes.get(job_id)
    if proc and proc.returncode is None:
        proc.terminate()
    job.status = "cancelled"
    job.return_code = -1
    job.finished_at = _now()
    _append_log(job, "Cancellation requested by user.")
    db.commit()
    return {"cancelled": True}


@app.websocket("/api/jobs/{job_id}/ws")
async def job_ws(websocket: WebSocket, job_id: int):
    await websocket.accept()
    subscribers.setdefault(job_id, set()).add(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        subscribers.get(job_id, set()).discard(websocket)


@app.get("/api/repositories/{repo_id}/archives")
async def archives(repo_id: int, db: Session = Depends(get_db)):
    repo = get_repo(db, repo_id)
    try:
        code, out, err = await run_capture(["borg", "list", "--json", repo.location], repo.encrypted_passphrase)
        if code:
            raise BorgError(friendly_borg_error(out, err, "borg list failed"))
        return json.loads(out or "{}").get("archives", [])
    except (BorgError, json.JSONDecodeError) as exc:
        raise HTTPException(502, str(exc)) from exc


@app.get("/api/repositories/{repo_id}/archives/{archive}/files")
async def archive_files(repo_id: int, archive: str, db: Session = Depends(get_db)):
    repo = get_repo(db, repo_id)
    try:
        code, out, err = await run_capture(
            ["borg", "list", "--json-lines", f"{repo.location}::{archive}"], repo.encrypted_passphrase
        )
        if code:
            raise BorgError(friendly_borg_error(out, err, "borg list failed"))
        return [json.loads(line) for line in out.splitlines() if line.strip()]
    except (BorgError, json.JSONDecodeError) as exc:
        raise HTTPException(502, str(exc)) from exc


@app.post("/api/restore")
async def restore(payload: RestoreCreate, db: Session = Depends(get_db)):
    repo = get_repo(db, payload.repository_id)
    lock = _repo_locks.setdefault(repo.id, asyncio.Lock())
    if lock.locked():
        raise HTTPException(409, "Repository is busy with another Borg operation")
    try:
        target = safe_restore_target(payload.target)
        args = ["borg", "extract", f"{repo.location}::{payload.archive}", *payload.paths]
        async with lock:
            code, out, err = await run_capture(args, repo.encrypted_passphrase, cwd=str(target))
        if code:
            raise BorgError(friendly_borg_error(out, err, "borg extract failed"))
        return {"ok": True, "target": str(target), "output": (out + "\n" + err).strip()[-10000:]}
    except BorgError as exc:
        raise HTTPException(502, str(exc)) from exc


@app.get("/api/schedules")
def schedules(db: Session = Depends(get_db)):
    return [_serialize_schedule(s) for s in db.scalars(select(Schedule).order_by(Schedule.name)).all()]


@app.post("/api/schedules")
def create_schedule(payload: ScheduleCreate, db: Session = Depends(get_db)):
    get_repo(db, payload.repository_id)
    _validate_cron(payload.cron)
    # Validate now, but keep the user-provided host paths so HOST_ROOT mapping remains understandable.
    _normalize_sources_or_http(payload.sources)
    config = {
        "sources": payload.sources,
        "compression": payload.compression,
        "excludes": payload.excludes,
    }
    schedule = Schedule(
        name=payload.name,
        repository_id=payload.repository_id,
        cron=payload.cron,
        sources_json=json.dumps(config),
        enabled=payload.enabled,
    )
    db.add(schedule)
    db.commit()
    db.refresh(schedule)
    register_schedule(schedule)
    return _serialize_schedule(schedule)


@app.delete("/api/schedules/{schedule_id}", status_code=204)
def delete_schedule(schedule_id: int, db: Session = Depends(get_db)):
    schedule = db.get(Schedule, schedule_id)
    if not schedule:
        raise HTTPException(404, "Schedule not found")
    sid = f"schedule-{schedule.id}"
    if scheduler.get_job(sid):
        scheduler.remove_job(sid)
    db.delete(schedule)
    db.commit()


@app.post("/api/schedules/{schedule_id}/run", response_model=BackupJobOut)
async def run_schedule_now(schedule_id: int, db: Session = Depends(get_db)):
    schedule = db.get(Schedule, schedule_id)
    if not schedule:
        raise HTTPException(404, "Schedule not found")
    cfg = _schedule_config(schedule)
    if not cfg["sources"]:
        raise HTTPException(422, "Schedule has no source paths")
    sources = _normalize_sources_or_http(cfg["sources"])
    archive = _friendly_schedule_archive(schedule.name)
    job = _queue_backup(db, schedule.repository_id, sources, archive)
    _spawn(execute_backup(job.id, cfg["compression"], cfg["excludes"]))
    return job
