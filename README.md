# Borg Backup on Haiku

A simple, automated BorgBackup setup for Haiku.

This setup creates hourly, deduplicated backups of the Haiku home directory to an external drive. It uses only BorgBackup, a shell script and cron — no Docker, server or additional backup management software is required.

## Tested setup

This configuration was tested with:

* Haiku x86_64
* BorgBackup 1.4.4
* Haiku system on an SSD
* Backup drive mounted as `/data`
* BeFS on the backup drive
* Borg repository at `/data/borg-haiku`
* Source directory `/boot/home`

The backup drive in the tested setup is a conventional HDD connected via USB 2.0, while Haiku itself runs from an SSD connected through a USB 3.0 SATA adapter.

## Backup strategy

The setup creates hourly snapshots of:

```text
/boot/home
```

and stores them in:

```text
/data/borg-haiku
```

Retention policy:

```text
24 hourly backups
 7 daily backups
 4 weekly backups
12 monthly backups
```

Borg's deduplication means that every archive represents a complete snapshot without storing unchanged files again.

## Install BorgBackup

Install Borg using Haiku's package manager:

```sh
pkgman install borgbackup
```

Check the installed version:

```sh
borg --version
```

The tested version was:

```text
borg 1.4.4
```

## Prepare the backup drive

In this example, the external backup drive is mounted at:

```text
/data
```

The tested drive uses BeFS.

Make sure that `/data` really points to the intended backup drive before continuing.

## Create the Borg repository

This example uses an unencrypted repository:

```sh
borg init --encryption=none /data/borg-haiku
```

If the backup drive can be lost, stolen or accessed by other people, consider using Borg repository encryption instead.

## Optional initial full-system archive

An initial archive of the complete Haiku volume can be useful as a baseline:

```sh
borg create \
    --verbose \
    --stats \
    --progress \
    /data/borg-haiku::haiku-{now:%Y-%m-%d_%H-%M} \
    /boot
```

The regular automated backups described below only back up `/boot/home`.

Using different archive prefixes keeps the initial full backup separate from automatically pruned home backups.

For example:

```text
haiku-2026-08-26_21-57
home-2026-08-26_23-04-52
```

## Install the backup script

Create:

```text
/boot/home/config/non-packaged/bin/haiku-backup
```

Make it executable:

```sh
chmod +x /boot/home/config/non-packaged/bin/haiku-backup
```

The script is included in this repository.

It performs the following operations:

```text
Check Borg
     ↓
Check backup repository
     ↓
Prevent parallel backup jobs
     ↓
borg create
     ↓
borg prune
     ↓
borg compact every 7 days
     ↓
write log
```

## Cron configuration

Edit the user's crontab:

```sh
crontab -e
```

Add:

```cron
0 * * * * /boot/home/config/non-packaged/bin/haiku-backup
```

This starts a backup at the beginning of every hour.

Check the configuration with:

```sh
crontab -l
```

## Borg and cron environment

Cron has a more limited environment than an interactive Haiku Terminal.

The script therefore explicitly sets:

```sh
export HOME="/boot/home"
export PATH="/boot/system/bin:/boot/home/config/non-packaged/bin:/bin"
```

For the unencrypted repository used in this example it also contains:

```sh
export BORG_UNKNOWN_UNENCRYPTED_REPO_ACCESS_IS_OK=yes
```

Without this setting, Borg may stop an automated cron job with:

```text
Warning: Attempting to access a previously unknown unencrypted repository!
Do you want to continue? [yN]
```

Do not blindly use this setting for repositories whose identity or location you do not trust.

## Logging

The script writes its log to:

```text
/boot/home/config/non-packaged/var/log/borg-backup.log
```

Watch a running backup with:

```sh
tail -f /boot/home/config/non-packaged/var/log/borg-backup.log
```

## List available backups

```sh
borg list /data/borg-haiku
```

Example:

```text
haiku-2026-08-26_21-57
home-2026-08-26_23-04-52
```

Only archives beginning with `home-` are automatically pruned.

The initial `haiku-*` archive therefore remains untouched.

## Deduplication

A test after the initial full backup showed Borg's deduplication working as expected.

The subsequent `/boot/home` archive contained approximately:

```text
Original size:       20.08 GB
Compressed size:     15.84 GB
Deduplicated size:    9.93 MB
```

Although the home directory contained about 20 GB, only around 10 MB of new data had to be added to the repository because most data was already present in the initial `/boot` archive.

## Restore a directory

First find the path inside an archive:

```sh
borg list /data/borg-haiku::home-2026-08-26_23-04-52 | grep Downloads
```

The Haiku Downloads directory appears inside the archive as:

```text
boot/home/Downloads
```

To restore it directly to its original location, change to the filesystem root:

```sh
cd /
```

Then extract:

```sh
borg extract \
    --progress \
    /data/borg-haiku::home-2026-08-26_23-04-52 \
    boot/home/Downloads
```

This restores:

```text
/boot/home/Downloads
```

A real restore test was performed by deleting `/boot/home/Downloads` and restoring it using this procedure.

## Repository check

Check repository consistency with:

```sh
borg check /data/borg-haiku
```

A more thorough data verification can be performed occasionally with:

```sh
borg check --verify-data /data/borg-haiku
```

On the tested BeFS backup drive Borg displayed:

```text
Failed to securely erase old repository config file (hardlinks not supported).
Old repokey data, if any, might persist on physical storage.
```

The tested repository uses `--encryption=none`, so there is no repository encryption key stored by Borg.

This warning should nevertheless be considered when deciding whether BeFS is appropriate for encrypted Borg repositories.

## BeFS

The tested Borg repository resides on a BeFS filesystem.

The following operations were successfully tested:

* `borg init`
* `borg create`
* deduplication
* `borg prune`
* `borg compact`
* `borg list`
* `borg extract`
* restoring files to their original location

This does not constitute a guarantee that every Borg feature or failure scenario is fully supported on BeFS.

## Important backup consideration

If `/data` is also used for ordinary files, remember:

```text
/data/borg-haiku
/data/photos
/data/archive
```

The Borg repository protects `/boot/home`.

Files that exist **only on `/data` are not protected by the Borg repository located on the same drive**.

Important data stored exclusively on `/data` should therefore have another independent backup.

## Why this setup?

The goal is to keep backup infrastructure on Haiku simple:

```text
Haiku / BeFS
     │
     │ /boot/home
     ▼
BorgBackup
     │
     ▼
External HDD / BeFS
     │
     ├── hourly snapshots
     ├── deduplication
     ├── automatic retention
     └── tested restore
```

No Docker, Linux VM or backup server is necessary.

The result is a small, transparent backup solution that fits well with a portable Haiku workstation.
