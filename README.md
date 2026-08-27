# BorgBackup on Haiku

A simple, automated and tested BorgBackup setup for Haiku.

This project provides a small shell script for creating hourly, deduplicated backups of a Haiku user's home directory to an external drive.

It deliberately keeps the setup simple:

* BorgBackup
* a shell script
* cron
* an external backup drive

No Docker, Linux VM, backup server or additional backup management software is required.

## Encrypted backups

This repository also contains an encrypted version of the backup setup.

The encrypted configuration uses a Borg `repokey-blake2` repository and
`BORG_PASSCOMMAND` for unattended cron backups without storing the actual
passphrase inside the backup script.

See:

[Encrypted BorgBackup setup](docs/ENCRYPTION.md)

## Tested configuration

This setup has been tested with:

* Haiku x86_64
* BorgBackup 1.4.4
* Haiku installed on an SSD
* external backup HDD mounted at `/data`
* BeFS on the backup HDD
* Borg repository at `/data/borg-haiku`
* backup source `/boot/home`
* hourly execution via cron

In the original test setup, Haiku runs from an SSD connected through a USB 3.0 SATA adapter. The backup repository resides on a conventional HDD connected via USB 2.0.

The setup has been tested not only for backup creation, but also for an actual restore.

## Overview

The basic setup looks like this:

```text
Haiku SSD
│
└── /boot/home
        │
        │ BorgBackup
        ▼
External HDD mounted as /data
│
└── borg-haiku
        │
        ├── hourly snapshots
        ├── deduplication
        ├── automatic pruning
        └── periodic compaction
```

## Backup strategy

The automated backup covers:

```text
/boot/home
```

The Borg repository is stored at:

```text
/data/borg-haiku
```

The retention policy is:

```text
24 hourly backups
 7 daily backups
 4 weekly backups
12 monthly backups
```

Borg uses deduplication, so each archive behaves like a complete snapshot while unchanged data does not have to be stored again.

There is no need to start over with a new full backup every few weeks or months.

## 1. Install BorgBackup

Install BorgBackup using Haiku's package manager:

```sh
pkgman install borgbackup
```

Check the installed version:

```sh
borg --version
```

This setup was tested with:

```text
borg 1.4.4
```

## 2. Prepare the backup drive

The examples in this documentation assume that the external backup drive is mounted at:

```text
/data
```

The tested backup drive uses BeFS.

Before creating the repository, make sure `/data` really points to the intended external drive.

For example:

```sh
df -h
```

The repository used by the script will be:

```text
/data/borg-haiku
```

## 3. Create the Borg repository

The tested setup uses an unencrypted Borg repository:

```sh
borg init --encryption=none /data/borg-haiku
```

### About encryption

`--encryption=none` was deliberately used for the tested setup.

If your backup drive may be lost, stolen or accessed by other people, you should consider using Borg repository encryption instead.

If you choose encryption, you will also need a suitable method for making the Borg passphrase available to unattended cron jobs.

The script in this repository is currently configured for an intentionally unencrypted repository.

## 4. Optional initial full-system backup

Before switching to automated `/boot/home` backups, you may optionally create one initial archive of the complete Haiku volume:

```sh
borg create \
    --verbose \
    --stats \
    --progress \
    /data/borg-haiku::haiku-{now:%Y-%m-%d_%H-%M} \
    /boot
```

This is not required for the hourly backup system.

It can, however, be useful as an initial baseline.

The automated script uses a different archive prefix:

```text
haiku-2026-08-26_21-57
home-2026-08-26_23-04-52
home-2026-08-27_00-15-53
```

This distinction is important because automatic pruning only operates on archives beginning with:

```text
home-
```

Therefore an initial `haiku-*` full-system archive is not affected by the automated retention policy.

## 5. Install the backup script

Place the included `haiku-backup` script at:

```text
/boot/home/config/non-packaged/bin/haiku-backup
```

Make it executable:

```sh
chmod +x /boot/home/config/non-packaged/bin/haiku-backup
```

The script performs the following sequence:

```text
Check Borg
     │
     ▼
Check repository
     │
     ▼
Acquire local script lock
     │
     ▼
borg create
     │
     ▼
borg prune
     │
     ▼
borg compact
(if 7 days have passed)
     │
     ▼
Write result to log
```

## 6. Borg environment under cron

Cron does not necessarily provide the same environment as an interactive Haiku Terminal.

The script therefore explicitly sets:

```sh
export HOME="/boot/home"
export PATH="/boot/system/bin:/boot/home/config/non-packaged/bin:/bin"

export BORG_CONFIG_DIR="/boot/home/config/settings/borg"
export BORG_CACHE_DIR="/boot/home/config/cache/borg"
export BORG_SECURITY_DIR="/boot/home/config/settings/borg/security"
```

The script automatically creates the required directories:

```sh
mkdir -p "$LOGDIR"
mkdir -p "$BORG_CONFIG_DIR"
mkdir -p "$BORG_CACHE_DIR"
mkdir -p "$BORG_SECURITY_DIR"
```

You therefore do not normally need to create these directories manually.

If you want to prepare them yourself, the relevant directories can be created with:

```sh
mkdir -p /boot/home/config/settings/borg/security
mkdir -p /boot/home/config/cache/borg
```

Using persistent Borg cache and security directories is important for unattended cron operation.

Without a consistent Borg environment, Borg may consider the repository previously unknown or rebuild its local cache.

## 7. Unencrypted repository warning

When an unencrypted repository is accessed from a previously unknown Borg environment, Borg may display:

```text
Warning: Attempting to access a previously unknown unencrypted repository!
Do you want to continue? [yN]
```

An interactive terminal can answer this question manually, but a cron job cannot.

Because this setup deliberately uses `--encryption=none`, the script contains:

```sh
export BORG_UNKNOWN_UNENCRYPTED_REPO_ACCESS_IS_OK=yes
```

This allows the unattended backup to continue.

> **Important:** Do not blindly use this setting for repositories whose identity or location you do not trust. It is used here specifically because the repository was intentionally created without encryption on a known local backup drive.

## 8. Test the script manually

Before enabling cron, run the script manually:

```sh
/boot/home/config/non-packaged/bin/haiku-backup
```

The script writes its output to the log rather than the terminal.

Watch the log with:

```sh
tail -f /boot/home/config/non-packaged/var/log/borg-backup.log
```

A successful run should end with messages similar to:

```text
Backup erfolgreich.
Prune gestartet.
Prune abgeschlossen.
Compact nicht erforderlich.
Backup vollständig abgeschlossen.
```

The script also records its process ID:

```text
2026-08-27 00:15:53 [PID 7204] Backup gestartet.
```

This makes it easier to diagnose duplicate or overlapping backup processes.

## 9. Enable hourly backups with cron

Edit the user's crontab:

```sh
crontab -e
```

Add:

```cron
0 * * * * /boot/home/config/non-packaged/bin/haiku-backup
```

This starts the backup at the beginning of every hour.

Verify the crontab:

```sh
crontab -l
```

It should contain:

```cron
0 * * * * /boot/home/config/non-packaged/bin/haiku-backup
```

## 10. Protection against overlapping backups

The script creates a local lock directory:

```text
/tmp/haiku-borg-backup.lock
```

If another instance of the script is already running, a second instance exits instead of starting another backup.

This is useful if a backup takes longer than expected and the next cron interval is reached.

Borg itself also protects its repository using its own repository locking mechanism.

## 11. Archive naming

Automated backups use names such as:

```text
home-2026-08-27_00-15-53
```

List the available archives with:

```sh
borg list /data/borg-haiku
```

Example:

```text
haiku-2026-08-26_21-57
home-2026-08-26_23-04-52
home-2026-08-27_00-15-53
```

The `haiku-*` archive is an optional manually created full-system backup.

The `home-*` archives are generated automatically.

## 12. Automatic pruning

After every successful backup, the script runs:

```sh
borg prune \
    --list \
    /data/borg-haiku \
    --glob-archives 'home-*' \
    --keep-hourly 24 \
    --keep-daily 7 \
    --keep-weekly 4 \
    --keep-monthly 12
```

This means Borg keeps approximately:

```text
last 24 hours  → hourly snapshots
last 7 days    → daily snapshots
last 4 weeks   → weekly snapshots
last 12 months → monthly snapshots
```

Borg does not simply keep every hourly archive forever.

It automatically thins out older `home-*` snapshots according to the retention policy.

For example, if several test backups are created within the same hour, pruning may keep only the most recent archive representing that hour.

The optional `haiku-*` full-system archive is not affected because pruning is explicitly restricted to:

```text
home-*
```

## 13. Repository compaction

With Borg 1.4.x, pruning removes archives from the backup history, but repository compaction is used to reclaim unused repository space.

Running `borg compact` after every hourly backup would be unnecessary, especially on a slower external HDD.

The script therefore keeps a timestamp at:

```text
/boot/home/config/non-packaged/var/log/last-compact
```

and runs:

```sh
borg compact /data/borg-haiku
```

only when at least seven days have passed since the previous successful compaction.

The hourly cron configuration remains simple:

```cron
0 * * * * /boot/home/config/non-packaged/bin/haiku-backup
```

No separate cron job for `compact` is required.

## 14. Deduplication test

The initial full `/boot` backup in the tested setup contained approximately:

```text
Original size:       84.20 GB
Compressed size:     67.89 GB
Deduplicated size:   63.16 GB
```

A subsequent `/boot/home` archive contained approximately:

```text
Original size:       20.08 GB
Compressed size:     15.84 GB
Deduplicated size:    9.93 MB
```

Although `/boot/home` contained about 20 GB, only around 10 MB of new data had to be added to the repository during that test.

This happened because most of the data was already present in the initial `/boot` archive.

Later hourly snapshots similarly only need to store new or changed chunks.

## 15. Restore a directory

A backup is only useful if it can actually be restored.

The restore procedure was therefore tested by deleting `/boot/home/Downloads` and restoring it from a Borg archive.

First find the path in the archive:

```sh
borg list /data/borg-haiku::home-2026-08-26_23-04-52 | grep Downloads
```

The directory appears as:

```text
boot/home/Downloads
```

To restore it directly to its original location, change to the filesystem root:

```sh
cd /
```

Then run:

```sh
borg extract \
    --progress \
    /data/borg-haiku::home-2026-08-26_23-04-52 \
    boot/home/Downloads
```

Because Borg extracts relative to the current working directory, running the command from `/` restores the directory to:

```text
/boot/home/Downloads
```

The restored directory was verified successfully after the test.

### Safer restore testing

If you do not want to overwrite or modify existing files, restore into a temporary directory instead:

```sh
mkdir -p /boot/home/restore-test
cd /boot/home/restore-test
```

Then extract the desired path there.

This allows you to inspect the restored files before copying anything back to the original location.

## 16. Repository integrity check

A basic repository consistency check can be performed with:

```sh
borg check /data/borg-haiku
```

A more thorough verification can occasionally be performed with:

```sh
borg check --verify-data /data/borg-haiku
```

`--verify-data` reads and verifies repository data and can therefore take considerably longer on a large repository or a slow external drive.

It should not be run every hour.

## 17. BeFS note

The tested Borg repository resides on a BeFS filesystem.

The following operations have been successfully tested on this setup:

* `borg init`
* `borg create`
* deduplication
* `borg prune`
* `borg compact`
* `borg list`
* `borg extract`
* restoring a deleted directory to its original location
* `borg check`

During `borg check`, Borg displayed:

```text
Failed to securely erase old repository config file (hardlinks not supported).
Old repokey data, if any, might persist on physical storage.
```

The tested repository uses:

```text
--encryption=none
```

so it does not contain a Borg repository encryption key.

The warning is nevertheless worth documenting, particularly for anyone considering an encrypted Borg repository on BeFS.

Successful testing of this setup does not constitute a guarantee that every Borg feature, filesystem failure mode or recovery scenario is fully supported on BeFS.

## 18. Log file

The backup log is stored at:

```text
/boot/home/config/non-packaged/var/log/borg-backup.log
```

Show the last 50 lines:

```sh
tail -50 /boot/home/config/non-packaged/var/log/borg-backup.log
```

Watch it live:

```sh
tail -f /boot/home/config/non-packaged/var/log/borg-backup.log
```

The current script uses German status messages in the log, but this does not affect Borg operation.

## 19. Troubleshooting

### Repository is reported as unknown

If cron reports:

```text
Warning: Attempting to access a previously unknown unencrypted repository!
```

make sure the script contains the persistent Borg environment:

```sh
export BORG_CONFIG_DIR="/boot/home/config/settings/borg"
export BORG_CACHE_DIR="/boot/home/config/cache/borg"
export BORG_SECURITY_DIR="/boot/home/config/settings/borg/security"
```

and that these directories are writable by the user running the cron job.

### Repository lock error

If Borg reports:

```text
Failed to create/acquire the lock /data/borg-haiku/lock.exclusive
```

first check whether another Borg process is still running:

```sh
ps | grep borg
```

Do **not** break a Borg repository lock while another Borg operation is still active.

A lock error can be completely normal if another backup, check, prune or compact operation is currently using the repository.

### Backup drive is disconnected

If `/data/borg-haiku` does not exist, the script does not attempt a backup.

Instead it writes a message to the log and exits.

This is useful for portable Haiku installations where the backup HDD may not always be connected.

### Check whether cron is configured

```sh
crontab -l
```

The expected entry is:

```cron
0 * * * * /boot/home/config/non-packaged/bin/haiku-backup
```

## 20. Important: `/data` itself is not backed up

The Borg repository protects:

```text
/boot/home
```

If the `/data` drive is also used for ordinary storage, for example:

```text
/data/borg-haiku
/data/photos
/data/archive
/data/projects
```

files stored only in `/data/photos`, `/data/archive`, `/data/projects`, etc. are **not** protected by the Borg repository residing on that same drive.

If the `/data` HDD fails, both those files and the Borg repository on that HDD may be lost.

Important data that exists exclusively on `/data` should therefore have another independent backup.

## 21. Sync is not the same as backup

If parts of `/boot/home` are also synchronized to a service such as Nextcloud, keeping them in the Borg backup can still be useful.

Synchronization and backup solve different problems.

A synchronization service may propagate deletions or unwanted changes, while Borg provides historical snapshots from which older versions can be restored.

For important data, using both can therefore provide useful additional protection.

## 22. Why this setup?

The goal of this project is not to build a large backup infrastructure.

It is to provide a small and understandable backup solution that fits the Haiku philosophy well:

```text
Haiku
  │
  └── /boot/home
          │
          ▼
      BorgBackup
          │
          ▼
    External HDD
          │
          ├── deduplicated snapshots
          ├── automatic retention
          ├── periodic compaction
          └── tested restore
```

No Docker.

No Linux VM.

No dedicated backup server.

Just BorgBackup, cron and an external drive.

## Disclaimer

This configuration works on the system on which it was tested, but backups should never be trusted solely because a command completed successfully.

Test restores yourself.

Check your repository periodically.

Keep additional independent copies of irreplaceable data.

A backup that has never been restored is an untested backup.
