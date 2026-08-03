---
name: backup-operations
description: How to operate incremental_backup/restore_from_backup safely — the passphrase custody trade-off, what never needs backing up, and what to check before trusting a restore.
---

# Backup operations

## Passphrase custody — read this before enabling backups

The backup is encrypted with a key derived from the `backup_passphrase`
secret via scrypt. **There is no recovery mechanism if that passphrase is
lost** in this first version of the package (no `wrap_escrow`-style second
key yet, unlike the multi-wrap design sketched for PawFlow's own
encryption-at-rest module). This is the correct guarantee for
confidentiality (nobody — including PawFlow or the cloud provider — can
decrypt without the passphrase), but it means the backup is the exact same
single point of failure as the passphrase itself. Tell the user this in
plain terms before they enable nightly-backup for real data, not only in
this file: "if you lose this passphrase, the backup becomes permanently
unrecoverable, with nothing PawFlow or the cloud provider can do about it."
Recommend writing the passphrase down somewhere durable and offline (or
sharing it with a second trusted person at the firm) rather than storing it
only in one person's head or one password manager entry.

## What never needs backing up

Anything already republished as open data by a public source — e.g. the
content reachable through the `legal-kb` MCP (justicelibre.org) if the
`firm.legal-assistant` package is also installed — should never be pointed
at by `source_path`. It is already durably published elsewhere; backing it
up here only wastes storage and upload time for zero recovery value.

## What to check before trusting a restore

1. `restore_from_backup` reports `files_restored` and `files_failed`
   explicitly — do not declare a restore successful without checking
   `files_failed` is empty.
2. A decrypt failure on the manifest itself (wrong passphrase, or
   `destination_root` pointing at a backup written by a different
   passphrase/package) fails loudly with an explicit error rather than
   silently producing garbage — if you see that error, stop and confirm the
   passphrase and destination path with the user rather than retrying with
   guesses.
3. `manifest_timestamp` lets you restore an older point in time instead of
   the latest state — useful when the most recent backup captured a mistake
   (e.g. a file deleted by accident) that an earlier manifest still
   references.

## Restoring

Call `restore_from_backup` with the same `destination_service` and
`destination_root` the backup was written to, the same passphrase (bound as
the same `backup_passphrase` secret), and a `target_path` to restore into —
never restore directly on top of `/workspace` without the user's explicit
confirmation that overwriting current files is intended.
