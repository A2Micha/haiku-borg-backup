# Borg Manager

Independent BorgBackup web UI implemented from scratch, inspired by the workflow of Borg UI.

## Current capabilities

- Graphical dark dashboard with repository size, archive history, deduplication and job health
- Create a **real local or SSH Borg repository** with `borg init`
- Attach an existing repository only after Borg can open it
- Encrypted-at-rest repository passphrases (Fernet key derived from `APP_SECRET`)
- Run real Borg 1.x `create` jobs
- Validate and map host source paths before queueing a backup
- Persist job status and Borg output/logs in SQLite
- Serialize Borg operations per repository to reduce lock contention
- Cancel running jobs
- Recover stale queued/running jobs as `interrupted` after a backend restart
- List archives and browse archive files
- Full or selective restores restricted to `/restore`
- Persistent schedules plus a live APScheduler executor
- Fine-grained scheduler UI: minute/hour intervals, daily, weekdays, weekend, selected weekdays, weekly and monthly
- Run scheduled backups immediately from the UI
- Repository integrity check with `borg check --repository-only`
- Docker healthchecks and persistent database volume
- Reproducible frontend dependencies via `package-lock.json` / `npm ci`
- CI smoke tests for Docker startup, API flow, host-path mapping, real Borg `init/create/list`, and restart persistence
- Optional mock mode for UI testing

## Docker host mounts

Borg Manager intentionally separates **backup source access** from **repository write access**.

### Backup sources: read-only

Configure which host directory is exposed read-only as `/host` inside the backend:

```env
HOST_ROOT=/
```

`HOST_ROOT=/` exposes the complete host filesystem read-only. A narrower example is:

```env
HOST_ROOT=/home
```

You may enter normal host paths in the UI. With `HOST_ROOT=/`, entering:

```text
/etc
/home/michael
```

is mapped internally to:

```text
/host/etc
/host/home/michael
```

The older `/host/...` form is still accepted, but using real host paths is easier to understand.

### Local Borg repositories: read/write on the host

Configure the host directory or mounted backup disk where Borg repositories may be created:

```env
BORG_REPO_ROOT=/mnt/backup/borg
```

Docker mounts that directory read/write as `/repos` inside the backend.

You may use the real host path in the web UI:

```text
/mnt/backup/borg/server-main
```

Borg Manager maps it internally to:

```text
/repos/server-main
```

and the repository data physically remains at:

```text
/mnt/backup/borg/server-main
```

The explicit container form `/repos/server-main` is also accepted.

This is deliberately safer than mounting the complete host filesystem read/write. Docker cannot write to arbitrary host paths that have not been exposed to the container. If repositories should live on another disk, mount that disk on the host and set `BORG_REPO_ROOT` to its mount point.

## Persistent Borg Manager database

The application database is stored at `/data/borg-manager.db` inside the backend and is persisted in the Docker named volume `borg_manager_data`.

Do **not** use this command on a normal update:

```bash
docker compose down -v
```

`-v` deletes named volumes and therefore deletes the Borg Manager database. It does not delete Borg repositories stored below `BORG_REPO_ROOT`, but it would remove Borg Manager configuration/history.

## Install / update

```bash
git clone --branch borg-manager-mvp --single-branch \
  https://github.com/A2Micha/haiku-borg-backup.git borg-manager
cd borg-manager
cp .env.example .env
nano .env
docker compose up -d --build
```

Open `http://SERVER-IP:8081`.

Example `.env` for a real installation:

```env
PORT=8081
APP_SECRET=use-a-long-random-secret-and-never-change-it-later
MOCK_BORG=0
TZ=Europe/Berlin
HOST_ROOT=/
BORG_REPO_ROOT=/mnt/backup/borg
BORG_LOCK_WAIT=60
METRICS_TTL=300
METRICS_CONCURRENCY=2
```

Generate a secret once, for example:

```bash
openssl rand -hex 32
```

**Keep the existing `APP_SECRET` on updates.** Stored repository passphrases are encrypted with it. Changing it prevents Borg Manager from decrypting existing stored passphrases.

Create the host repository directory if necessary:

```bash
sudo mkdir -p /mnt/backup/borg
```

For an existing installation:

```bash
cd /etc/docker/containers/borg-manager
git pull origin borg-manager-mvp
docker compose up -d --build
docker compose ps
```

There is normally no need for `docker compose down` during a regular update.

Verify the important mounts and services:

```bash
docker compose exec backend borg --version
docker compose exec backend sh -lc 'touch /repos/.write-test && rm /repos/.write-test && echo "repo storage writable"'
docker compose exec backend sh -lc 'test -r /host/etc && echo "host source readable"'
docker compose ps
```

## Create a local repository

In **Repositories** choose **Create new**. With:

```env
BORG_REPO_ROOT=/mnt/backup/borg
```

you can enter the real host path:

```text
/mnt/backup/borg/main
```

or the container path:

```text
/repos/main
```

Both refer to the same physical repository directory on the Docker host. Select `repokey-blake2`, enter a passphrase and submit. Borg Manager executes a real `borg init`; the repository record is only saved if that succeeds.

To attach an existing local repository, make sure it is below `BORG_REPO_ROOT`. For SSH repositories use Borg-compatible SSH syntax.

## Run a real backup

With `HOST_ROOT=/`, source paths can be entered as normal host paths:

```text
/home
/etc
/var/lib
```

They are mapped internally to the read-only `/host` mount before Borg is started.

## Scheduler

The normal scheduler UI does **not** require Cron knowledge. It offers selections for:

- every 5 / 10 / 15 / 20 / 30 minutes
- every 1 / 2 / 3 / 4 / 6 / 8 / 12 hours
- daily at a selected time
- Monday to Friday
- weekend only
- selected weekdays
- weekly
- monthly

Internally Borg Manager stores a standard five-field Cron expression because APScheduler uses it. The technical expression is generated automatically by the UI.

Schedules are stored in SQLite and registered again whenever the backend starts. The **Run now** button queues the same backup immediately.

## Mock mode

`MOCK_BORG=1` is intentionally fake and must only be used for UI testing. When active the UI displays a warning banner and Borg operations are simulated.

For actual backups use:

```env
MOCK_BORG=0
```

## SSH repositories

Use a Borg-compatible remote location such as:

```text
user@server:/path/to/repo
```

The current Docker Compose mounts `${HOME}/.ssh` read-only into the backend. For production use a dedicated backup SSH key and proper `known_hosts` management.

## Safety / stability choices

- Borg is executed with `create_subprocess_exec`; user input is not interpolated into a shell command.
- Host backup source data is mounted read-only.
- Local Borg repository storage is writable only below configured `BORG_REPO_ROOT`.
- Local source and repository paths are validated and mapped server-side.
- Borg operations on the same repository are serialized in the application.
- Borg waits for repository locks for a configurable period (`BORG_LOCK_WAIT`).
- Restore destinations are confined to `RESTORE_ROOT`.
- Passphrases are not returned by the API and are encrypted before database storage.
- Changing `APP_SECRET` results in a clear decryption error instead of a raw cryptography exception.
- SQLite runs with WAL mode and a busy timeout.
- Stale jobs are marked `interrupted` after restart instead of remaining permanently `running`.
- Deleting a repository in the UI removes the Borg Manager record, not the actual Borg repository data.
- Docker healthchecks prevent the frontend from being considered ready before the backend is healthy.

## CI verification

Every push to `borg-manager-mvp` builds the complete Docker stack and performs automated smoke tests for:

1. backend and frontend health
2. host repository path mapping
3. host source path mapping
4. repository API creation
5. backup job lifecycle
6. scheduler registration
7. metrics endpoint
8. real Borg binary availability
9. real Borg `init`, `create`, and `list`
10. SQLite persistence and scheduler recovery after backend restart

This does not replace testing against your exact filesystem/NAS/SSH environment, but it catches significantly more than a compile-only build.

## Still not production-complete

The current branch is substantially more defensive than the original MVP, but it is **not yet a hardened public/multi-user backup appliance**. Remaining work includes:

- authentication and RBAC
- CSRF/session hardening once login exists
- schema migrations (Alembic) instead of `create_all` only
- dedicated SSH key and `known_hosts` management UI
- retention / prune / compact policies
- notifications
- pagination/virtualization for very large archive file lists
- Borg 2 compatibility
- browser-level UI/end-to-end tests

Until authentication exists, expose the UI only on a trusted LAN/VPN/reverse proxy with access control; do not publish port 8081 directly to the Internet.

## API highlights

- `GET /api/health`
- `GET /api/dashboard`
- `GET|POST|DELETE /api/repositories`
- `POST /api/repositories/{id}/check`
- `POST /api/backups`
- `GET /api/jobs`
- `POST /api/jobs/{id}/cancel`
- `GET /api/repositories/{id}/archives`
- `GET /api/repositories/{id}/archives/{archive}/files`
- `POST /api/restore`
- `GET|POST /api/schedules`
- `POST /api/schedules/{id}/run`
- `DELETE /api/schedules/{id}`

## License note

This implementation does not copy Borg UI source code. Borg UI itself is AGPL-3.0; if you later copy or combine its source code, review the resulting license obligations separately.
