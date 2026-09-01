import asyncio
import json
import os
import shutil
from pathlib import Path
from typing import AsyncIterator
from .security import decrypt_secret

MOCK_BORG = os.getenv("MOCK_BORG", "0") == "1"
RESTORE_ROOT = Path(os.getenv("RESTORE_ROOT", "/restore")).resolve()
BORG_REPO_CONTAINER_ROOT = Path(os.getenv("BORG_REPO_CONTAINER_ROOT", "/repos"))
BORG_REPO_HOST_ROOT_RAW = os.getenv("BORG_REPO_HOST_ROOT", "")


class BorgError(RuntimeError):
    pass


def _env(encrypted_passphrase: str | None) -> dict[str, str]:
    env = os.environ.copy()
    passphrase = decrypt_secret(encrypted_passphrase)
    if passphrase:
        env["BORG_PASSPHRASE"] = passphrase
    env["BORG_UNKNOWN_UNENCRYPTED_REPO_ACCESS_IS_OK"] = "yes"
    env["BORG_RELOCATED_REPO_ACCESS_IS_OK"] = "yes"
    env["BORG_CHECK_I_KNOW_WHAT_I_AM_DOING"] = "YES"
    return env


def borg_available() -> bool:
    return shutil.which("borg") is not None


def _is_remote_repository(value: str) -> bool:
    if value.startswith(("ssh://", "sftp://")):
        return True
    # Borg's scp-like syntax, e.g. user@example.org:/backup/repo
    return ":" in value and not value.startswith("/")


def _map_repository_path(value: str) -> str:
    """Map a host-visible repository path to the writable /repos bind mount.

    Docker exposes backup source data under /host read-only. The repository
    storage configured with BORG_REPO_ROOT is exposed separately under /repos
    read/write. Users may nevertheless paste either the real host path
    (/mnt/backup/borg/repo1) or its /host view
    (/host/mnt/backup/borg/repo1). This function transparently maps both forms
    to /repos/repo1 before Borg is executed.
    """
    if not value or _is_remote_repository(value):
        return value

    archive_suffix = ""
    base = value
    if "::" in value:
        base, archive = value.split("::", 1)
        archive_suffix = f"::{archive}"

    if not base.startswith("/"):
        return value

    base_path = Path(base)
    try:
        base_path.relative_to(BORG_REPO_CONTAINER_ROOT)
        return value
    except ValueError:
        if base_path == BORG_REPO_CONTAINER_ROOT:
            return value

    if not BORG_REPO_HOST_ROOT_RAW or not os.path.isabs(BORG_REPO_HOST_ROOT_RAW):
        return value

    host_root = Path(BORG_REPO_HOST_ROOT_RAW).resolve()

    # /host is the read-only view of the Docker host. Translate it back to a
    # real host path for comparison with BORG_REPO_HOST_ROOT.
    if base == "/host":
        host_candidate = Path("/")
    elif base.startswith("/host/"):
        host_candidate = Path("/" + base[len("/host/"):])
    else:
        host_candidate = Path(base)

    try:
        relative = host_candidate.relative_to(host_root)
    except ValueError:
        return value

    mapped = BORG_REPO_CONTAINER_ROOT / relative
    return f"{mapped}{archive_suffix}"


def _normalize_borg_args(args: list[str]) -> list[str]:
    return [_map_repository_path(arg) for arg in args]


async def run_capture(
    args: list[str],
    encrypted_passphrase: str | None = None,
    cwd: str | None = None,
) -> tuple[int, str, str]:
    args = _normalize_borg_args(args)
    if MOCK_BORG:
        await asyncio.sleep(0.1)
        if "info" in args and "--json" in args:
            return 0, json.dumps({"repository": {"id": "mock", "location": args[-1]}}), ""
        if "list" in args and "--json" in args:
            return 0, json.dumps({"archives": [{"name": "demo-2026-09-01_10-00", "time": "2026-09-01T10:00:00"}]}), ""
        if "list" in args and "--json-lines" in args:
            return 0, "\n".join([
                json.dumps({"path": "home/user/Documents/report.pdf", "size": 245760}),
                json.dumps({"path": "home/user/Pictures/photo.jpg", "size": 1853000}),
            ]), ""
        return 0, "", ""
    if not borg_available():
        raise BorgError("borg executable not found in backend container")
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=_env(encrypted_passphrase),
        cwd=cwd,
    )
    out, err = await proc.communicate()
    return proc.returncode, out.decode(errors="replace"), err.decode(errors="replace")


async def stream_process(
    args: list[str],
    encrypted_passphrase: str | None = None,
) -> AsyncIterator[tuple[str, asyncio.subprocess.Process | None]]:
    args = _normalize_borg_args(args)
    if MOCK_BORG:
        for line in ["Preparing files…", "Creating archive…", "42.3 GB processed", "Backup completed successfully"]:
            await asyncio.sleep(0.35)
            yield line, None
        return
    if not borg_available():
        raise BorgError("borg executable not found in backend container")
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        env=_env(encrypted_passphrase),
    )
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
