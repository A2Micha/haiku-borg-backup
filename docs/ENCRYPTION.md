# Encrypted BorgBackup on Haiku

This document describes how to use the Haiku BorgBackup setup with an **encrypted Borg repository** and unattended backups via cron.

It is an extension of the standard setup documented in the main README.

The encrypted setup has the same basic design:

```text
/boot/home
     │
     │ BorgBackup
     ▼
/data/borg-haiku-encrypted
```

The important difference is that all data stored inside the Borg repository is encrypted.

The Borg passphrase is provided automatically to Borg so that hourly cron backups can run unattended.

## Tested environment

This setup was tested with:

* Haiku x86_64
* BorgBackup 1.4.4
* source directory `/boot/home`
* external backup drive mounted at `/data`
* BeFS backup filesystem
* encrypted Borg repository
* automatic backups via cron

## Why use encryption?

The standard setup can use:

```sh
borg init --encryption=none /data/borg-haiku
```

This is simple, but anyone with access to the backup drive can potentially access the backed-up data.

For an external or portable backup drive, encryption provides an important additional layer of protection.

The encrypted setup uses a separate repository:

```text
/data/borg-haiku-encrypted
```

The original unencrypted repository does not have to be deleted.

## 1. Create a Borg passphrase

Choose a long and unique passphrase.

Do not use your normal Haiku password or another password that you already use elsewhere.

The passphrase will protect the Borg repository encryption key.

If the passphrase is lost, recovering the encrypted backup may become impossible.

## 2. Create the passphrase file

For automatic cron backups, Borg needs access to the passphrase without interactive input.

Create the Borg configuration directory:

```sh
mkdir -p /boot/home/config/settings/borg
```

Create the passphrase file:

```sh
nano /boot/home/config/settings/borg/backup-passphrase
```

The file should contain only the passphrase:

```text
your-long-and-unique-passphrase
```

Protect the file:

```sh
chmod 600 /boot/home/config/settings/borg/backup-passphrase
```

Check the permissions:

```sh
ls -l /boot/home/config/settings/borg/backup-passphrase
```

The passphrase file should only be readable and writable by its owner.

## 3. Configure Borg to read the passphrase

Borg supports `BORG_PASSCOMMAND` for unattended operation.

Set:

```sh
export BORG_PASSCOMMAND="cat /boot/home/config/settings/borg/backup-passphrase"
```

Borg will execute this command whenever it needs the repository passphrase.

This is preferable to storing the actual passphrase directly inside the backup script.

The backup script therefore contains the command:

```sh
export BORG_PASSCOMMAND="cat /boot/home/config/settings/borg/backup-passphrase"
```

but **not the passphrase itself**.

## 4. Create the encrypted repository

Create the new repository with:

```sh
borg init \
    --encryption=repokey-blake2 \
    /data/borg-haiku-encrypted
```

The repository is now separate from the unencrypted repository:

```text
/data/
├── borg-haiku
└── borg-haiku-encrypted
```

The first repository can remain available while the encrypted setup is being tested.

## 5. Test access

Test the repository:

```sh
borg list /data/borg-haiku-encrypted
```

If `BORG_PASSCOMMAND` is still set in the current Terminal environment, Borg should obtain the passphrase automatically.

For a completely new Terminal session, either set `BORG_PASSCOMMAND` again or use the provided backup script, which sets it automatically.

## 6. Export the Borg repository key

This is extremely important.

Export the Borg key:

```sh
borg key export \
    /data/borg-haiku-encrypted \
    /boot/home/borg-haiku-encrypted-key-backup
```

Store another copy of this key somewhere safe and independent from the backup HDD.

For example:

```text
Haiku SSD
Backup HDD
Separate USB stick
        ↑
        └── Borg key backup
```

Do not rely solely on the copy stored with your normal system.

The repository key and passphrase are critical recovery information.

## 7. Install the encrypted backup script

Copy `haiku-backup-encrypted` to:

```text
/boot/home/config/non-packaged/bin/haiku-backup-encrypted
```

Make it executable:

```sh
chmod +x /boot/home/config/non-packaged/bin/haiku-backup-encrypted
```

The script backs up:

```text
/boot/home
```

to:

```text
/data/borg-haiku-encrypted
```

## 8. Borg environment

The script explicitly configures the Borg environment:

```sh
export HOME="/boot/home"
export PATH="/boot/system/bin:/boot/home/config/non-packaged/bin:/bin"

export BORG_CONFIG_DIR="/boot/home/config/settings/borg"
export BORG_CACHE_DIR="/boot/home/config/cache/borg"
export BORG_SECURITY_DIR="/boot/home/config/settings/borg/security"

export BORG_PASSCOMMAND="cat /boot/home/config/settings/borg/backup-passphrase"
```

The required directories are automatically created by the script:

```sh
mkdir -p "$LOGDIR"
mkdir -p "$BORG_CONFIG_DIR"
mkdir -p "$BORG_CACHE_DIR"
mkdir -p "$BORG_SECURITY_DIR"
```

This gives interactive Borg sessions and cron jobs a consistent Borg environment.

## 9. Test the backup manually

Before configuring cron, run:

```sh
/boot/home/config/non-packaged/bin/haiku-backup-encrypted
```

The first backup into a new repository will take considerably longer than subsequent backups.

This is expected.

A new Borg repository cannot use the deduplicated chunks stored in another Borg repository.

Therefore the first encrypted backup has to write the complete initial dataset.

## 10. Watch the backup

The encrypted backup uses its own log file:

```text
/boot/home/config/non-packaged/var/log/borg-backup-encrypted.log
```

Watch it live:

```sh
tail -f /boot/home/config/non-packaged/var/log/borg-backup-encrypted.log
```

Pressing `Ctrl+C` stops `tail`, not the running Borg backup.

Check whether Borg is running with:

```sh
ps | grep borg
```

## 11. Retention policy

The encrypted script uses the same retention policy as the standard setup:

```text
24 hourly
 7 daily
 4 weekly
12 monthly
```

The script runs:

```sh
borg prune \
    --list \
    "$REPO" \
    --glob-archives 'home-*' \
    --keep-hourly 24 \
    --keep-daily 7 \
    --keep-weekly 4 \
    --keep-monthly 12
```

Borg automatically thins out older snapshots while retaining representative hourly, daily, weekly and monthly archives.

## 12. Repository compaction

The script does not run `borg compact` after every hourly backup.

Instead it records the last successful compaction and runs compact at most once every seven days.

The encrypted setup uses:

```text
/boot/home/config/non-packaged/var/log/last-compact-encrypted
```

for this purpose.

## 13. Configure cron

After the manual backup has completed successfully, edit the crontab:

```sh
crontab -e
```

For hourly encrypted backups:

```cron
0 * * * * /boot/home/config/non-packaged/bin/haiku-backup-encrypted
```

Verify:

```sh
crontab -l
```

## 14. List encrypted backups

The passphrase must be available to Borg.

For an interactive Terminal:

```sh
export BORG_PASSCOMMAND="cat /boot/home/config/settings/borg/backup-passphrase"
```

Then:

```sh
borg list /data/borg-haiku-encrypted
```

Example:

```text
home-2026-08-27_17-24-00
home-2026-08-27_18-00-00
home-2026-08-27_19-00-00
```

## 15. Restore from the encrypted repository

Restoring works exactly like with the unencrypted repository.

Set the passphrase command:

```sh
export BORG_PASSCOMMAND="cat /boot/home/config/settings/borg/backup-passphrase"
```

List the archive:

```sh
borg list /data/borg-haiku-encrypted
```

Inspect an archive:

```sh
borg list \
    /data/borg-haiku-encrypted::home-YYYY-MM-DD_HH-MM-SS
```

To restore `/boot/home/Downloads` to its original location:

```sh
cd /
```

Then:

```sh
borg extract \
    --progress \
    /data/borg-haiku-encrypted::home-YYYY-MM-DD_HH-MM-SS \
    boot/home/Downloads
```

Always verify your backups with an actual restore test.

## 16. Repository check

Basic check:

```sh
export BORG_PASSCOMMAND="cat /boot/home/config/settings/borg/backup-passphrase"

borg check /data/borg-haiku-encrypted
```

More thorough verification:

```sh
borg check --verify-data /data/borg-haiku-encrypted
```

The latter can take a long time on a large repository.

## Security considerations

### What encryption protects

If someone obtains only the external backup HDD, the Borg repository remains encrypted.

Without the repository key and passphrase, the backup contents should not be readable.

### What automatic unlocking changes

Automatic cron backups require the Haiku system to have access to the Borg passphrase.

In this setup it is stored at:

```text
/boot/home/config/settings/borg/backup-passphrase
```

with restrictive file permissions.

This creates an unavoidable trade-off:

```text
Backup HDD stolen
        │
        └── encrypted data
              ✓ protected

Haiku system + backup HDD stolen
        │
        └── passphrase file may also be available
              ⚠ weaker protection
```

File permissions prevent ordinary unauthorized access through the operating system, but they do not protect the passphrase against an attacker who obtains unrestricted access to the Haiku system or its storage.

This is the price of fully unattended backups.

### Do not store the passphrase in Git

Never commit:

```text
backup-passphrase
```

to this repository.

Never put a real Borg passphrase directly into the public backup script.

The GitHub repository should contain only:

```sh
export BORG_PASSCOMMAND="cat /boot/home/config/settings/borg/backup-passphrase"
```

### Keep recovery information separately

For important backups, keep the following somewhere safe:

```text
Borg repository
        +
repository key backup
        +
passphrase
```

Do not keep all three exclusively on the same physical device.

## Migrating from the unencrypted repository

The safest migration is to keep the existing unencrypted repository temporarily:

```text
/data/borg-haiku
```

while creating and testing:

```text
/data/borg-haiku-encrypted
```

Perform at least one successful backup and one successful restore from the encrypted repository.

Only after the encrypted backup has been verified should you consider deleting the old unencrypted repository.

There is no need to rush this step.

## Summary

The encrypted setup keeps the simplicity of the original Haiku BorgBackup solution:

```text
Haiku /boot/home
       │
       ▼
    BorgBackup
       │
       │ encryption
       ▼
External BeFS HDD
       │
       ├── hourly snapshots
       ├── deduplication
       ├── automatic pruning
       ├── weekly compaction
       └── encrypted backup data
```

It still requires no Docker, Linux VM or dedicated backup server.

The only major addition is secure management of the Borg repository passphrase and recovery key.

**Always test your restores. An encrypted backup that cannot be unlocked is not a useful backup.**
