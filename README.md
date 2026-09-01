# Borg Manager

Independent BorgBackup web UI implemented from scratch, inspired by the workflow of Borg UI.

## Current capabilities

- Dark responsive dashboard with Borg/scheduler health
- Create a **real local or SSH Borg repository** with `borg init`
- Attach an existing repository only after Borg can open it
- Encrypted-at-rest repository passphrases (Fernet key derived from `APP_SECRET`)
- Run real Borg 1.x `create` jobs
- Validate source paths before queueing a backup
- Persist job status and Borg output/logs
- Cancel running jobs
- List archives and browse archive files
- Full or selective restores restricted to `/restore`
- Persistent cron schedule definitions plus a live APScheduler executor
- Run scheduled backups immediately from the UI
- Repository integrity check with `borg check --repository-only`
- Docker Compose deployment
- Optional mock mode for UI testing

## Important: real host data in Docker

The backend sees the host source mount at `/host`. Configure which host directory is exposed in `.env`:

```env
MOCK_BORG=0
TZ=Europe/Berlin
HOST_ROOT=/
```

`HOST_ROOT=/` exposes the whole host filesystem **read-only** as `/host` inside the backend. For tighter access use a narrower value such as:

```env
HOST_ROOT=/home
```

Then `/host` inside Borg Manager represents `/home` on the Docker host.

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

For an existing installation:

```bash
cd /etc/docker/containers/borg-manager
git pull origin borg-manager-mvp
```

Make sure your existing `.env` contains at least:

```env
MOCK_BORG=0
TZ=Europe/Berlin
HOST_ROOT=/
```

Then rebuild because the backend gained scheduler dependencies:

```bash
docker compose down
docker compose build --no-cache
docker compose up -d
docker compose ps
```

## Create a local repository

In **Repositories** choose **Create new** and use a location such as:

```text
/repos/main
```

Select `repokey-blake2`, enter a passphrase and submit. Borg Manager executes a real `borg init`; the repository record is only saved if that succeeds.

To attach an existing repository choose **Connect existing**. Borg Manager runs `borg info --json` first and rejects unreachable/invalid repositories.

## Run a real backup

With `HOST_ROOT=/`, examples of source paths in the UI are:

```text
/host/home
/host/etc
/host/var/lib
```

These map to `/home`, `/etc`, and `/var/lib` on the Docker host. Borg runs inside the backend container and writes the archive to the selected repository.

## Scheduler

Schedules use standard five-field cron syntax. Example:

```text
0 2 * * *
```

runs every day at 02:00 in the timezone configured with `TZ`. Schedules are loaded from SQLite whenever the backend starts and registered with APScheduler. The **Run now** button queues the same real Borg backup immediately.

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

## Safety choices

- Borg is executed with `create_subprocess_exec`; user input is not interpolated into a shell command.
- Host backup data is mounted read-only.
- Restore destinations are confined to `RESTORE_ROOT`.
- Passphrases are not returned by the API and are encrypted before database storage.
- Deleting a repository in the UI removes the Borg Manager record, not the actual Borg repository data.

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

## Still not production-complete

The project now performs real Borg operations and real scheduling, but it is not yet a hardened multi-user backup appliance. Remaining production work includes authentication/RBAC, dedicated SSH key/host-key management, durable recovery of running jobs after container restarts, retention/prune/compact policies, notifications, migrations, automated integration tests with real Borg repositories, pagination for huge archives, and Borg 2 compatibility.

## License note

This implementation does not copy Borg UI source code. Borg UI itself is AGPL-3.0; if you later copy or combine its source code, review the resulting license obligations separately.
