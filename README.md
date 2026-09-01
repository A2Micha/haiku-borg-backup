# Borg Manager MVP

Independent BorgBackup web UI prototype inspired by the workflow of Borg UI, implemented from scratch.

## Included

- Dark responsive dashboard
- Local or SSH Borg repository records
- Encrypted-at-rest repository passphrases (Fernet key derived from `APP_SECRET`)
- Start Borg 1.x `create` jobs
- Job status and persisted logs
- Archive listing
- Archive file browsing
- Full or selective restores restricted to `/restore`
- Schedule records/API (execution scheduler is intentionally not wired yet)
- Docker Compose deployment
- Mock mode for UI/API testing without touching backups

## Safety choices

- Borg is launched with `create_subprocess_exec`; user input is never interpolated into a shell command.
- Restore destinations are path-confined to `RESTORE_ROOT`.
- Passphrases are not returned by the API and are encrypted before database storage.
- Backup source access is constrained by Docker mounts. The sample compose exposes `./demo-sources` read-only as `/sources`.

## Start

```bash
cp .env.example .env
# Change APP_SECRET before storing real credentials.
docker compose up --build
```

Open http://localhost:8081

### Quick safe demo

Set `MOCK_BORG=1` in `.env`, then:

1. Add repository `Demo` with location `/repos/demo`.
2. Run a backup with source `/sources`.
3. Open Archives and load the demo repository.

Mock mode does not create or restore real Borg data.

## Real Borg flow

Set `MOCK_BORG=0`. Create/init a Borg 1.x repository first, for example on the host or in an equivalent Borg environment:

```bash
borg init --encryption=repokey-blake2 ./repositories/main
```

Then add `/repos/main` in the web UI and use `/sources` as a source.

For SSH repositories, use a Borg-compatible location such as `user@server:/path/to/repo` and ensure the required SSH key/known_hosts entries are available. The sample compose mounts `${HOME}/.ssh` read-only for an MVP convenience; a production version should manage dedicated keys instead.

## API surfaces

- `GET /api/dashboard`
- `GET|POST|DELETE /api/repositories`
- `POST /api/backups`
- `GET /api/jobs`
- `POST /api/jobs/{id}/cancel`
- `GET /api/repositories/{id}/archives`
- `GET /api/repositories/{id}/archives/{archive}/files`
- `POST /api/restore`
- `GET|POST /api/schedules`

## Before production

This is a functional MVP, not a hardened production backup appliance. The next production pass should add authentication/RBAC, CSRF/security headers, dedicated SSH-key management and host-key verification UI, durable process ownership/cancellation across restarts, repository init/check/prune/compact operations, an actual cron scheduler, retention policies, notifications, DB migrations, tests, pagination for huge archives, and Borg 2 compatibility.

## License note

This implementation does not copy Borg UI source code. Borg UI itself is AGPL-3.0; if you later copy or combine its source code, review the resulting license obligations separately.
