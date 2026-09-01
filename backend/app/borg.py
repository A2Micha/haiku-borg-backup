import asyncio
import json
import os
import shutil
from pathlib import Path
from typing import AsyncIterator
from .security import decrypt_secret

MOCK_BORG = os.getenv("MOCK_BORG", "0") == "1"
RESTORE_ROOT = Path(os.getenv("RESTORE_ROOT", "/restore")).resolve()

class BorgError(RuntimeError):
    pass

def _env(encrypted_passphrase: str | None) -> dict[str, str]:
    env = os.environ.copy()
    passphrase = decrypt_secret(encrypted_passphrase)
    if passphrase:
        env["BORG_PASSPHRASE"] = passphrase
    env["BORG_UNKNOWN_UNENCRYPTED_REPO_ACCESS_IS_OK"] = "yes"
    return env

def borg_available() -> bool:
    return shutil.which("borg") is not None

async def run_capture(args: list[str], encrypted_passphrase: str | None = None, cwd: str | None = None) -> tuple[int, str, str]:
    if MOCK_BORG:
        await asyncio.sleep(0.1)
        if "list" in args and "--json" in args:
            return 0, json.dumps({"archives": [{"name": "demo-2026-09-01_10-00", "time": "2026-09-01T10:00:00"}]}), ""
        if "list" in args and "--json-lines" in args:
            return 0, '\n'.join([
                json.dumps({"path": "home/user/Documents/report.pdf", "size": 245760}),
                json.dumps({"path": "home/user/Pictures/photo.jpg", "size": 1853000}),
            ]), ""
        return 0, "", ""
    if not borg_available():
        raise BorgError("borg executable not found")
    proc = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=_env(encrypted_passphrase), cwd=cwd)
    out, err = await proc.communicate()
    return proc.returncode, out.decode(errors="replace"), err.decode(errors="replace")

async def stream_process(args: list[str], encrypted_passphrase: str | None = None) -> AsyncIterator[tuple[str, asyncio.subprocess.Process | None]]:
    if MOCK_BORG:
        for line in ["Preparing files…", "Creating archive…", "42.3 GB processed", "Backup completed successfully"]:
            await asyncio.sleep(0.35)
            yield line, None
        return
    if not borg_available():
        raise BorgError("borg executable not found")
    proc = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, env=_env(encrypted_passphrase))
    assert proc.stdout
    async for raw in proc.stdout:
        yield raw.decode(errors="replace").rstrip(), proc
    await proc.wait()
    if proc.returncode != 0:
        raise BorgError(f"borg exited with code {proc.returncode}")

def safe_restore_target(target: str) -> Path:
    resolved = (RESTORE_ROOT / target.lstrip("/")).resolve()
    if RESTORE_ROOT != resolved and RESTORE_ROOT not in resolved.parents:
        raise BorgError("Restore target must stay inside RESTORE_ROOT")
    resolved.mkdir(parents=True, exist_ok=True)
    return resolved
