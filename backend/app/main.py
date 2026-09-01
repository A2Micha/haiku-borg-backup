import asyncio
import json
import os
import shutil
import time
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI, Depends, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select, desc
from sqlalchemy.orm import Session

from .database import Base, engine, SessionLocal, get_db
from .models import Repository, BackupJob, Schedule
from .schemas import RepositoryCreate, RepositoryOut, BackupCreate, BackupJobOut, RestoreCreate, ScheduleCreate
from .security import encrypt_secret
from .borg import run_capture, stream_process, safe_restore_target, BorgError, MOCK_BORG, borg_available

Base.metadata.create_all(bind=engine)

subscribers: dict[int, set[WebSocket]] = {}
running_processes: dict[int, object] = {}
scheduler = AsyncIOScheduler(timezone=os.getenv("TZ", "Europe/Berlin"))
_metrics_cache: dict[int, tuple[float, dict]] = {}
METRICS_TTL = int(os.getenv("METRICS_TTL", "60"))


def get_repo(db: Session, repo_id: int) -> Repository:
    repo = db.get(Repository, repo_id)
    if not repo:
        raise HTTPException(404, "Repository not found")
    return repo


def _schedule_config(schedule: Schedule) -> dict:
    try:
        raw = json.loads(schedule.sources_json)
    except json.JSONDecodeError:
        raw = []
    if isinstance(raw, list):
        return {"sources": raw, "compression": "zstd,3", "excludes": []}
    return {"sources": raw.get("sources", []), "compression": raw.get("compression", "zstd,3"), "excludes": raw.get("excludes", [])}


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


def _validate_cron(expr: str) -> CronTrigger:
    try:
        return CronTrigger.from_crontab(expr, timezone=os.getenv("TZ", "Europe/Berlin"))
    except ValueError as exc:
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
        result.update({"ok": True, "archive_count": 3, "logical_size": 48_000_000_000, "compressed_size": 31_000_000_000, "deduplicated_size": 12_000_000_000, "physical_size": 12_600_000_000, "dedup_ratio": 4.0, "compression_ratio": 1.55, "history": [
            {"name": "demo-1", "time": "2026-08-28T02:00:00", "duration": 52, "nfiles": 40210, "original_size": 13_000_000_000, "compressed_size": 8_700_000_000, "deduplicated_size": 4_100_000_000},
            {"name": "demo-2", "time": "2026-08-29T02:00:00", "duration": 39, "nfiles": 40502, "original_size": 14_100_000_000, "compressed_size": 9_100_000_000, "deduplicated_size": 1_300_000_000},
            {"name": "demo-3", "time": "2026-08-30T02:00:00", "duration": 43, "nfiles": 40788, "original_size": 14_600_000_000, "compressed_size": 9_300_000_000, "deduplicated_size": 900_000_000},
        ]})
        _metrics_cache[repo.id] = (now, result)
        return result

    try:
        list_code, list_out, list_err = await run_capture(["borg", "list", "--json", repo.location], repo.encrypted_passphrase)
        if list_code:
            raise BorgError(list_err or list_out or "borg list failed")
        listed = json.loads(list_out or "{}")
        archive_list = listed.get("archives", []) if isinstance(listed, dict) else []
        result["archive_count"] = len(archive_list)
        if archive_list:
            result["last_archive_at"] = (archive_list[-1].get("start") or archive_list[-1].get("time"))

        info_code, info_out, info_err = await run_capture(["borg", "info", "--json", "--last", "30", repo.location], repo.encrypted_passphrase)
        if info_code:
            raise BorgError(info_err or info_out or "borg info failed")
        info = json.loads(info_out or "{}")
        archive_info = info.get("archives", []) if isinstance(info, dict) else []
        history = [_archive_row(a) for a in archive_info]
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

        local_path = Path(repo.location)
        if repo.location.startswith("/") and local_path.exists():
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
    archive = archive_name or datetime.now().strftime("backup-%Y-%m-%d_%H-%M-%S")
    job = BackupJob(repository_id=repository_id, archive_name=archive, sources_json=json.dumps(sources), status="queued")
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


async def execute_backup(job_id: int, compression: str, excludes: list[str]):
    db = SessionLocal()
    try:
        job = db.get(BackupJob, job_id)
        repo = db.get(Repository, job.repository_id) if job else None
        if not job or not repo:
            return
        job.status = "running"
        job.started_at = datetime.utcnow()
        db.commit()
        sources = json.loads(job.sources_json)
        args = ["borg", "create", "--stats", "--progress", "--compression", compression]
        for pattern in excludes:
            args += ["--exclude", pattern]
        args += [f"{repo.location}::{job.archive_name}", *sources]
        try:
            async for line, proc in stream_process(args, repo.encrypted_passphrase):
                if proc:
                    running_processes[job_id] = proc
                job.log = (job.log + line + "\n")[-500000:]
                db.commit()
                await publish(job_id, line)
            job.status = "success"
            job.return_code = 0
            _metrics_cache.pop(repo.id, None)
        except asyncio.CancelledError:
            job.status = "cancelled"
            job.return_code = -1
            raise
        except Exception as exc:
            job.status = "failed"
            job.return_code = 1
            job.log = (job.log + f"ERROR: {exc}\n")[-500000:]
            await publish(job_id, f"ERROR: {exc}")
        finally:
            job.finished_at = datetime.utcnow()
            db.commit()
            running_processes.pop(job_id, None)
    finally:
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
        get_repo(db, schedule.repository_id)
        archive = f"{schedule.name.replace(' ', '-')}-{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}"
        job = _queue_backup(db, schedule.repository_id, cfg["sources"], archive)
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
    scheduler.add_job(execute_schedule, trigger=trigger, args=[schedule.id], id=job_id, replace_existing=True, coalesce=True, max_instances=1, misfire_grace_time=3600)


@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler.start()
    db = SessionLocal()
    try:
        for schedule in db.scalars(select(Schedule)).all():
            try:
                register_schedule(schedule)
            except HTTPException as exc:
                print(f"Skipping invalid schedule {schedule.id}: {exc.detail}")
    finally:
        db.close()
    yield
    scheduler.shutdown(wait=False)


app = FastAPI(title="Borg Manager API", version="0.3.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])


@app.get("/api/health")
def health():
    return {"ok": True, "mock_borg": MOCK_BORG, "borg_available": borg_available(), "scheduler_running": scheduler.running, "timezone": os.getenv("TZ", "Europe/Berlin")}


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
        "failed_recent": sum(1 for j in jobs if j.status == "failed"),
    }


@app.get("/api/metrics")
async def metrics(force: bool = False, db: Session = Depends(get_db)):
    repos = db.scalars(select(Repository).order_by(Repository.name)).all()
    repo_metrics = await asyncio.gather(*[_repository_metrics(repo, force=force) for repo in repos])
    total_size = sum((r.get("physical_size") or r.get("deduplicated_size") or 0) for r in repo_metrics)
    total_archives = sum(r.get("archive_count", 0) for r in repo_metrics)
    return {"repositories": repo_metrics, "total_repository_size": total_size, "total_archives": total_archives, "updated_at": datetime.utcnow().isoformat() + "Z"}


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
        if payload.initialize:
            if payload.encryption != "none" and not payload.passphrase:
                raise HTTPException(422, "A passphrase is required for encrypted repositories")
            code, out, err = await run_capture(["borg", "init", "--encryption", payload.encryption, payload.location], encrypted)
            if code:
                raise BorgError(err or out or "borg init failed")
        else:
            code, out, err = await run_capture(["borg", "info", "--json", payload.location], encrypted)
            if code:
                raise BorgError(err or out or "Could not open repository")
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
            raise BorgError(err or out)
        return {"ok": True, "repository": json.loads(out or "{}") if out.strip() else {}}
    except (BorgError, json.JSONDecodeError) as exc:
        raise HTTPException(502, str(exc)) from exc


@app.post("/api/repositories/{repo_id}/check")
async def repository_check(repo_id: int, db: Session = Depends(get_db)):
    repo = get_repo(db, repo_id)
    try:
        code, out, err = await run_capture(["borg", "check", "--repository-only", repo.location], repo.encrypted_passphrase)
        if code:
            raise BorgError(err or out)
        return {"ok": True, "output": (out + "\n" + err).strip()[-10000:]}
    except BorgError as exc:
        raise HTTPException(502, str(exc)) from exc


@app.delete("/api/repositories/{repo_id}", status_code=204)
def delete_repository(repo_id: int, db: Session = Depends(get_db)):
    repo = get_repo(db, repo_id)
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
    if not MOCK_BORG:
        missing = [source for source in payload.sources if not Path(source).exists()]
        if missing:
            raise HTTPException(422, f"Source path(s) not visible inside container: {', '.join(missing)}")
    job = _queue_backup(db, repo.id, payload.sources, payload.archive_name)
    asyncio.create_task(execute_backup(job.id, payload.compression, payload.excludes))
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
    proc = running_processes.get(job_id)
    if proc and getattr(proc, "returncode", None) is None:
        proc.terminate()
    job.status = "cancelled"
    job.finished_at = datetime.utcnow()
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
            raise BorgError(err or out)
        return json.loads(out or "{}").get("archives", [])
    except (BorgError, json.JSONDecodeError) as exc:
        raise HTTPException(502, str(exc)) from exc


@app.get("/api/repositories/{repo_id}/archives/{archive}/files")
async def archive_files(repo_id: int, archive: str, db: Session = Depends(get_db)):
    repo = get_repo(db, repo_id)
    try:
        code, out, err = await run_capture(["borg", "list", "--json-lines", f"{repo.location}::{archive}"], repo.encrypted_passphrase)
        if code:
            raise BorgError(err or out)
        return [json.loads(line) for line in out.splitlines() if line.strip()]
    except (BorgError, json.JSONDecodeError) as exc:
        raise HTTPException(502, str(exc)) from exc


@app.post("/api/restore")
async def restore(payload: RestoreCreate, db: Session = Depends(get_db)):
    repo = get_repo(db, payload.repository_id)
    try:
        target = safe_restore_target(payload.target)
        args = ["borg", "extract", f"{repo.location}::{payload.archive}", *payload.paths]
        code, out, err = await run_capture(args, repo.encrypted_passphrase, cwd=str(target))
        if code:
            raise BorgError(err or out)
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
    config = {"sources": payload.sources, "compression": payload.compression, "excludes": payload.excludes}
    schedule = Schedule(name=payload.name, repository_id=payload.repository_id, cron=payload.cron, sources_json=json.dumps(config), enabled=payload.enabled)
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
    if not MOCK_BORG:
        missing = [source for source in cfg["sources"] if not Path(source).exists()]
        if missing:
            raise HTTPException(422, f"Source path(s) not visible inside container: {', '.join(missing)}")
    archive = f"{schedule.name.replace(' ', '-')}-{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}"
    job = _queue_backup(db, schedule.repository_id, cfg["sources"], archive)
    asyncio.create_task(execute_backup(job.id, cfg["compression"], cfg["excludes"]))
    return job
