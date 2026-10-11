# Security

Explorer Advance — Robocopy is a **local Windows desktop helper**. It is not a
network service and it does not send files anywhere.

## What the app can change

- **Copy** runs `robocopy.exe` and can create or overwrite files on the
  destination.
- **Purge** (`/PURGE`) and **Mirror** (`/MIR`) can **delete** files on the
  destination that are not in the source.

Those write actions show the exact `robocopy` command and require confirmation.
A real Purge, Mirror, or drive-root job needs an extra acknowledgement.
**Compare only** and **Dry run** (`/L`) copy and delete nothing.

There is no undo for a real copy, purge, or mirror.

## Localhost only

The UI listens on `127.0.0.1` so other machines on the LAN cannot open it.
Do not change the host to `0.0.0.0` unless you understand that anyone who
can reach that port can start Robocopy as the logged-in Windows user.

## Local files to keep private

`user_config.json` and `logs/` are created on this PC and listed in
`.gitignore`. They can contain folder paths and file names from this machine.
Do not commit them or attach them to issues.

## Reporting a problem

Open a GitHub issue. Do not attach personal folder lists or Robocopy logs.
