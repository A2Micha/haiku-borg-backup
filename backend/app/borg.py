import asyncio
import json
import os
import shutil
from pathlib import Path
from typing import AsyncIterator

from .security import SecretError, decrypt_secret

MOCK_BORG = os.getenv("MOCK_BORG", "0") == "1"
RESTORE_ROOT = Path(os.getenv("RESTORE_ROOT", "/restore")).resolve()
BORG_REPO_CONTAINER_ROOT = Path(os.getenv("BORG_REPO_CONTAINER_ROOT", "/repos")).resolve()
BORG_REPO_HOST_ROOT_RAW = os.getenv("BORG_REPO_HOST_ROOT", "")
HOST_CONTAINER_ROOT = Path(os.getenv("HOST_CONTAINER_ROOT", "/host")).resolve()
HOST_SOURCE_ROOT_RAW = os.getenv("HOST_SOURCE_ROOT", "")
BORG_LOCK_WAIT = os.getenv("BORG_LOCK_WAIT", "60")


class BorgError(RuntimeError):
    pass


def _env(encrypted_passphrase: str | None) -> dict[str, str]:
    env = os.environ.copy()
    try:
        passphrase = decrypt_secret(encrypted_passphrase)
    except SecretError as exc:
        raise BorgError(str(exc)) from exc
    if passphrase:
        env["BORG_PASSPHRASE"] = passphrase
    env["BORG_UNKNOWN_UNENCRYPTED_REPO_ACCESS_IS_OK"] = "yes"
    env["BORG_RELOCATED_REPO_ACCESS_IS_OK"] = "yes"
    env["BORG_LOCK_WAIT"] = BORG_LOCK_WAIT
    return env


def borg_available() -> bool:
    return shutil.which("borg") is not None


def is_remote_repository(value: str) -> bool:
    if value.startswith(("ssh://", "sftp://")):
        return True
    # Borg's scp-like syntax, e.g. user@example.org:/backup/repo
    return ":" in value and not value.startswith("/")


def _split_archive(value: str) -> tuple[str, str]:
    if "::" not in value:
        return value, ""
    base, archive = value.split("::", 1)
    return base, f"::{archive}"


def map_repository_path(value: str) -> str:
    """Map a host-visible local repository path to the writable /repos mount.

    Accepted local forms are:
      * /repos/name (container path)
      * <BORG_REPO_ROOT>/name (real host path)
      * /host/<BORG_REPO_ROOT>/name (read-only host view)

    Remote Borg repository strings are returned unchanged.
    """
    if not value or is_remote_repository(value):
        return value

    base, archive_suffix = _split_archive(value)
    if not base.startswith("/"):
        return value

    base_path = Path(base)
    try:
        base_path.relative_to(BORG_REPO_CONTAINER_ROOT)
        return f"{base_path}{archive_suffix}"
    except ValueError:
        pass

    if not BORG_REPO_HOST_ROOT_RAW or not os.path.isabs(BORG_REPO_HOST_ROOT_RAW):
        return value

    host_root = Path(BORG_REPO_HOST_ROOT_RAW).resolve()
    if base == str(HOST_CONTAINER_ROOT):
        host_candidate = Path("/")
    elif base.startswith(str(HOST_CONTAINER_ROOT) + "/"):
        host_candidate = Path("/" + base[len(str(HOST_CONTAINER_ROOT)) + 1 :])
    else:
        host_candidate = Path(base)

    try:
        relative = host_candidate.resolve(strict=False).relative_to(host_root)
    except ValueError:
        return value

    mapped = BORG_REPO_CONTAINER_ROOT / relative
    return f"{mapped}{archive_suffix}"


def local_repository_container_path(value: str) -> Path | None:
    """Return the safe container path for a local repo, or None for remote repos."""
    if is_remote_repository(value):
        return None
    base, _ = _split_archive(map_repository_path(value))
    if not base.startswith("/"):
        raise BorgError("Local repository paths must be absolute.")
    resolved = Path(base).resolve(strict=False)
    try:
        resolved.relative_to(BORG_REPO_CONTAINER_ROOT)
    except ValueError as exc:
        host_hint = BORG_REPO_HOST_ROOT_RAW or "the configured BORG_REPO_ROOT"
        raise BorgError(
            f"Local repositories must be inside {host_hint} on the host "
            f"(mounted as {BORG_REPO_CONTAINER_ROOT} in the container)."
        ) from exc
    return resolved


def ensure_repository_parent(value: str) -> None:
    path = local_repository_container_path(value)
    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)


def normalize_source_path(value: str) -> str:
    """Map a host path to the read-only /host bind mount when possible."""
    value = value.strip()
    if not value:
        raise BorgError("Backup source paths must not be empty.")

    candidate = Path(value)
    if value == str(HOST_CONTAINER_ROOT) or value.startswith(str(HOST_CONTAINER_ROOT) + "/"):
        return str(candidate.resolve(strict=False))
    if value == "/sources" or value.startswith("/sources/"):
        return str(candidate.resolve(strict=False))
    if not candidate.is_absolute():
        raise BorgError(f"Backup source path must be absolute: {value}")

    if HOST_SOURCE_ROOT_RAW and os.path.isabs(HOST_SOURCE_ROOT_RAW):
        host_root = Path(HOST_SOURCE_ROOT_RAW).resolve()
        try:
            relative = candidate.resolve(strict=False).relative_to(host_root)
            mapped = HOST_CONTAINER_ROOT if str(relative) == "." else HOST_CONTAINER_ROOT / relative
            return str(mapped)
        except ValueError:
            pass

    raise BorgError(
        f"Backup source is outside HOST_ROOT and is not visible to the container: {value}"
    )


def validate_source_paths(values: list[str], require_exists: bool = True) -> list[str]:
    normalized: list[str] = []
    for value in values:
        source = normalize_source_path(value)
        path = Path(source).resolve(strict=False)
        allowed = False
        for root in (HOST_CONTAINER_ROOT, Path("/sources").resolve()):
            if path == root or root in path.parents:
                allowed = True
                break
        if not allowed:
            raise BorgError(f"Backup source is not inside an allowed read-only source mount: {value}")
        if require_exists and not path.exists():
            raise BorgError(f"Backup source does not exist or is not visible inside the container: {value}")
        normalized.append(str(path))
    return normalized


def friendly_borg_error(out: str, err: str, fallback: str) -> str:
    text = (err or out or fallback).strip()
    if "Read-only file system" in text:
        return (
            "Repository destination is read-only. Choose a local repository below the configured "
            "BORG_REPO_ROOT (mounted as /repos), not below /host."
        )
    if "No space left on device" in text:
        return "The repository filesystem is full (No space left on device)."
    if "Permission denied" in text:
        return "Permission denied while accessing the Borg repository or backup source."
    lines = [line for line in text.splitlines() if line.strip()]
    return "\n".join(lines[-12:])[-4000:] if lines else fallback


def _normalize_borg_args(args: list[str]) -> list[str]:
    return [map_repository_path(arg) for arg in args]


async def run_capture(
    args: list[str],
    encrypted_passphrase: str | None = None,
    cwd: str | None = None,
) -> tuple[int, str, str]:
    args = _normalize_borg_args(args)
    if MOCK_BORG:
        await asyncio.sleep(0.05)
        if "info" in args and "--json" in args:
            return 0, json.dumps({"repository": {"id": "mock", "location": args[-1]}}), ""
        if "list" in args and "--json" in args:
            return 0, json.dumps({"archives": [{"name": "demo-2026-09-01_10-00", "time": "2026-09-01T10:00:00"}]}), ""
        if "list" in args and "--json-lines" in args:
            return 0, "\n".join(
                [
                    json.dumps({"path": "home/user/Documents/report.pdf", "size": 245760}),
                    json.dumps({"path": "home/user/Pictures/photo.jpg", "size": 1853000}),
                ]
            ), ""
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
        for line in [
            "Preparing files…",
            "Creating archive…",
            "42.3 GB processed",
            "Backup completed successfully",
        ]:
            await asyncio.sleep(0.2)
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
    # Yield the process immediately so callers can cancel even before Borg prints output.
    yield "", proc
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
