import asyncio
import json
from datetime import datetime
from pathlib import Path
from fastapi import FastAPI, Depends, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select, desc
from sqlalchemy.orm import Session

from .database import Base, engine, SessionLocal, get_db
from .models import Repository, BackupJob, Schedule
from .schemas import RepositoryCreate, RepositoryOut, BackupCreate, BackupJobOut, RestoreCreate, ScheduleCreate
from .security import encrypt_secret
from .borg import run_capture, stream_process, safe_restore_target, BorgError, MOCK_BORG

Base.metadata.create_all(bind=engine)
app = FastAPI(title="Borg Manager API", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

subscribers: dict[int, set[WebSocket]] = {}
running_processes: dict[int, object] = {}

async def publish(job_id: int, line: str):
    dead = []
    for ws in subscribers.get(job_id, set()):
        try:
            await ws.send_json({"job_id": job_id, "line": line})
        except Exception:
            dead.append(ws)
    for ws in dead:
        subscribers.get(job_id, set()).discard(ws)

def get_repo(db: Session, repo_id: int) -> Repository:
    repo = db.get(Repository, repo_id)
    if not repo:
        raise HTTPException(404, "Repository not found")
    return repo

@app.get("/api/health")
def health():
    return {"ok": True, "mock_borg": MOCK_BORG}

@app.get("/api/dashboard")
def dashboard(db: Session = Depends(get_db)):
    repos = db.scalars(select(Repository)).all()
    jobs = db.scalars(select(BackupJob).order_by(desc(BackupJob.id)).limit(8)).all()
    successful = sum(1 for j in jobs if j.status == "success")
    failed = sum(1 for j in jobs if j.status == "failed")
    return {"repository_count": len(repos), "recent_jobs": [BackupJobOut.model_validate(j) for j in jobs], "successful_recent": successful, "failed_recent": failed}

@app.get("/api/repositories", response_model=list[RepositoryOut])
def list_repositories(db: Session = Depends(get_db)):
    return db.scalars(select(Repository).order_by(Repository.name)).all()

@app.post("/api/repositories", response_model=RepositoryOut)
def create_repository(payload: RepositoryCreate, db: Session = Depends(get_db)):
    existing = db.scalar(select(Repository).where(Repository.name == payload.name))
    if existing:
        raise HTTPException(409, "Repository name already exists")
    repo = Repository(name=payload.name, location=payload.location, encrypted_passphrase=encrypt_secret(payload.passphrase))
    db.add(repo); db.commit(); db.refresh(repo)
    return repo

@app.delete("/api/repositories/{repo_id}", status_code=204)
def delete_repository(repo_id: int, db: Session = Depends(get_db)):
    repo = get_repo(db, repo_id)
    db.delete(repo); db.commit()

async def execute_backup(job_id: int, compression: str, excludes: list[str]):
    db = SessionLocal()
    try:
        job = db.get(BackupJob, job_id)
        repo = db.get(Repository, job.repository_id) if job else None
        if not job or not repo: return
        job.status = "running"; job.started_at = datetime.utcnow(); db.commit()
        sources = json.loads(job.sources_json)
        args = ["borg", "create", "--stats", "--progress", "--compression", compression]
        for pattern in excludes:
            args += ["--exclude", pattern]
        args += [f"{repo.location}::{job.archive_name}", *sources]
        try:
            async for line, proc in stream_process(args, repo.encrypted_passphrase):
                if proc: running_processes[job_id] = proc
                job.log = (job.log + line + "\n")[-200000:]
                db.commit()
                await publish(job_id, line)
            job.status = "success"; job.return_code = 0
        except asyncio.CancelledError:
            job.status = "cancelled"; job.return_code = -1
            raise
        except Exception as exc:
            job.status = "failed"; job.return_code = 1
            job.log = (job.log + f"ERROR: {exc}\n")[-200000:]
            await publish(job_id, f"ERROR: {exc}")
        finally:
            job.finished_at = datetime.utcnow(); db.commit(); running_processes.pop(job_id, None)
    finally:
        db.close()

@app.post("/api/backups", response_model=BackupJobOut)
async def start_backup(payload: BackupCreate, db: Session = Depends(get_db)):
    repo = get_repo(db, payload.repository_id)
    archive = payload.archive_name or datetime.now().strftime("backup-%Y-%m-%d_%H-%M-%S")
    job = BackupJob(repository_id=repo.id, archive_name=archive, sources_json=json.dumps(payload.sources), status="queued")
    db.add(job); db.commit(); db.refresh(job)
    asyncio.create_task(execute_backup(job.id, payload.compression, payload.excludes))
    return job

@app.get("/api/jobs", response_model=list[BackupJobOut])
def list_jobs(db: Session = Depends(get_db)):
    return db.scalars(select(BackupJob).order_by(desc(BackupJob.id)).limit(100)).all()

@app.get("/api/jobs/{job_id}", response_model=BackupJobOut)
def get_job(job_id: int, db: Session = Depends(get_db)):
    job = db.get(BackupJob, job_id)
    if not job: raise HTTPException(404, "Job not found")
    return job

@app.post("/api/jobs/{job_id}/cancel")
async def cancel_job(job_id: int, db: Session = Depends(get_db)):
    job = db.get(BackupJob, job_id)
    if not job: raise HTTPException(404, "Job not found")
    proc = running_processes.get(job_id)
    if proc and getattr(proc, "returncode", None) is None:
        proc.terminate()
    job.status = "cancelled"; job.finished_at = datetime.utcnow(); db.commit()
    return {"cancelled": True}

@app.websocket("/api/jobs/{job_id}/ws")
async def job_ws(websocket: WebSocket, job_id: int):
    await websocket.accept()
    subscribers.setdefault(job_id, set()).add(websocket)
    try:
        while True: await websocket.receive_text()
    except WebSocketDisconnect:
        subscribers.get(job_id, set()).discard(websocket)

@app.get("/api/repositories/{repo_id}/archives")
async def archives(repo_id: int, db: Session = Depends(get_db)):
    repo = get_repo(db, repo_id)
    try:
        code, out, err = await run_capture(["borg", "list", "--json", repo.location], repo.encrypted_passphrase)
        if code: raise BorgError(err)
        return json.loads(out or "{}").get("archives", [])
    except (BorgError, json.JSONDecodeError) as exc:
        raise HTTPException(502, str(exc))

@app.get("/api/repositories/{repo_id}/archives/{archive}/files")
async def archive_files(repo_id: int, archive: str, db: Session = Depends(get_db)):
    repo = get_repo(db, repo_id)
    try:
        code, out, err = await run_capture(["borg", "list", "--json-lines", f"{repo.location}::{archive}"], repo.encrypted_passphrase)
        if code: raise BorgError(err)
        return [json.loads(line) for line in out.splitlines() if line.strip()]
    except (BorgError, json.JSONDecodeError) as exc:
        raise HTTPException(502, str(exc))

@app.post("/api/restore")
async def restore(payload: RestoreCreate, db: Session = Depends(get_db)):
    repo = get_repo(db, payload.repository_id)
    try:
        target = safe_restore_target(payload.target)
        args = ["borg", "extract", f"{repo.location}::{payload.archive}", *payload.paths]
        code, out, err = await run_capture(args, repo.encrypted_passphrase, cwd=str(target))
        if code: raise BorgError(err)
        return {"ok": True, "target": str(target), "output": out[-5000:]}
    except BorgError as exc:
        raise HTTPException(502, str(exc))

@app.get("/api/schedules")
def schedules(db: Session = Depends(get_db)):
    return db.scalars(select(Schedule).order_by(Schedule.name)).all()

@app.post("/api/schedules")
def create_schedule(payload: ScheduleCreate, db: Session = Depends(get_db)):
    get_repo(db, payload.repository_id)
    schedule = Schedule(name=payload.name, repository_id=payload.repository_id, cron=payload.cron, sources_json=json.dumps(payload.sources), enabled=payload.enabled)
    db.add(schedule); db.commit(); db.refresh(schedule)
    return {"id": schedule.id, "name": schedule.name, "repository_id": schedule.repository_id, "cron": schedule.cron, "sources": payload.sources, "enabled": schedule.enabled}
