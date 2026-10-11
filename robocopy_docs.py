"""
Reference text for the Robocopy manual and command-map tabs.

Switches match `robocopy /?` on current Windows, plus the Microsoft docs
and the flags this app already wraps.
"""

from __future__ import annotations

from typing import TypedDict


class ToolCommand(TypedDict):
    id: str
    switch: str
    title: str
    syntax: str
    group: str
    status: str  # ready | planned
    danger: bool
    summary: str
    details: str
    examples: list[str]


GROUPS = [
    "Copy shape",
    "Selection",
    "Exclude",
    "Performance",
    "Security & metadata",
    "Logging",
    "Jobs",
]


OVERVIEW_MD = """
### What `robocopy` is

**`robocopy.exe`** (Robust File Copy) is the built-in Windows command for
**bulk folder copy and sync**. It is not Explorer, and it is not a backup
program with versions. It copies a **source tree into a destination tree**
with precise rules about *which* files move, *how many* threads, and
*what to log*.

- Faster than Explorer for “update this folder”: it can skip files that are
  already the same, use **`/MT`** (multi-thread), and write a **`/LOG`**.
- Default in this app is **update newer files** (`/E /XO /FFT /R:1 /W:1`),
  not “make both folders identical”.
- The Copy / Compare tab starts with **three** source → destination rows.
  **+** adds more. Empty rows are skipped. Several pairs run **in order**,
  one `robocopy.exe` at a time.
- Help text is often **localized**. Switches stay in English (`/E`, `/XO`,
  `/MIR`, …). This app does not parse Robocopy banners; it reads the log
  and exit code.

### Golden rules

1. **Preview the exact command** before a real copy. Dry-run (`/L`) and
   **Compare only** copy nothing.
2. **`/XO` does not equalize folders.** Extra files on the destination stay
   there unless you tick **Delete dest extras (`/PURGE`)** or **Mirror
   (`/MIR`)**. Both can **delete** and need an extra confirmation.
3. Robocopy exit codes **0–7** are the success family. **Exit 1 after a
   real update is normal.** ≥ 8 is a failure.
4. Only **one** `robocopy.exe` at a time from this app. Several folder pairs
   run one after another. Closing the Python window used to leave the copy
   running; new jobs are tied to this process.
"""


QUICKSTART_MD = """
### 1. Update newer files only (the app default)

Fill one or more source → destination rows (three are ready; **+** adds
more). Empty rows are skipped. One **Run** walks every filled pair in order.
Use **Save as…** for a named project (for example **My games**). That stores
the folders **and** the Options. Load restores both. **New** starts a
blank project on **Update newer only**.

```bat
robocopy "C:\\Path\\Source" "D:\\Path\\Destination" /E /XO /FFT /R:1 /W:1 /MT:8 /LOG:"C:\\logs\\robocopy.log" /TEE
```

`/E` walks subfolders (including empty). `/XO` skips destination files that
are already newer or the same. `/FFT` uses 2-second times (USB / mixed FS).

### 2. Dry-run first

```bat
robocopy SRC DST /E /XO /FFT /L
```

`/L` lists what would happen. Nothing is copied or deleted.

### 3. Compare two folders (no copy)

In this app: **Compare only**. That is `/L`, no `/MIR`, no `/XO` (so both
Newer and Older show up), and a `compare_YYYYMMDD_HHMMSS.log`. Then open
**See reports**.

### 4. Mirror (destructive — dry-run first)

```bat
robocopy SRC DST /MIR /L
```

`/MIR` is `/E` + `/PURGE`: destination extras that are not in source are
**deleted**. The Mirror preset in this app forces dry-run on.

### 5. Projects, Purge, excludes

See the chapters below: **Projects**, **Purge vs Mirror**,
**More flags**, and **Filters and metadata**.

### 6. Read the result

- Exit **0**: nothing to copy (already up to date), or a compare with no diffs.
- Exit **1**: files copied (or would copy, on a dry-run).
- Exit **2–7**: extras / mismatches — usually OK; read the log.
- Exit **≥ 8**: some files failed.
"""


SAFETY_MD = """
### Safe to run anytime

| Command | Why |
|---|---|
| `robocopy /?` | Help only |
| `robocopy SRC DST /L` | List only — no copy, no delete |
| **Compare only** in this app | `/L`, never `/MIR`, writes `compare_*.log` |

### Needs a clear intent

| Command | What it changes |
|---|---|
| `/E` + `/XO` | Copies newer/new files; leaves dest extras alone |
| Full copy (no `/XO`) | Overwrites when Robocopy’s size/time rules say so |
| `/MT:n` | Parallel copy (I/O load) |
| `/LOG:` | Overwrites that log file (this app uses a **new timestamped** file per run) |

### Dangerous if you do not mean it

| Command | Why |
|---|---|
| `/MIR` | Makes dest a clone of source — **deletes** extra dest files |
| `/PURGE` | Deletes dest files that vanished from source (without the rest of `/MIR`) |
| `/MOV` `/MOVE` | Copy then **delete from source** |

This app still offers **Mirror**, behind a confirmation (and the preset
starts as a dry-run). `/PURGE` is a separate checkbox with the same extra
confirm. `/MOVE` stays documented until wired.
"""


LISTS_MD = """
A **project** is a recipe: the source → destination rows **and** the Options
on Copy / Compare (presets, `/XO`, `/PURGE`, More flags, Filters). The
**Log location** field stays a global app setting; it is not stored in the
project.

**New** starts a blank project: three empty rows and **Update newer only**
(`/E /XO /FFT`, extra flags off). Saved projects are not deleted.

| Action | What it stores |
|---|---|
| **New** | Clear rows, reset Options to Update newer only. Saved projects stay. |
| **Save as…** | New name. Folders + current Options. That project becomes active. |
| **Save project** | Overwrite the selected name with the current folders + Options. |
| **Save settings** | Writes `user_config.json`. If a project is active, its Options snapshot is updated (and its folders too when at least one pair is filled). |
| **Run** | Same snapshot as Save settings, then starts the jobs. |
| Pick a name in **Project** | Loads that recipe immediately. The previous working set is **not** auto-saved first. |
| Trash button | Deletes the selected named project. |

Typical flow: fill the rows, set Options, **Save as…** → **My games**. Next
session, pick **My games**. A toast confirms *folders and options restored*.
After you change folders or Options, click **Save project**.

An older project saved before Options existed still loads its folders and
falls back to Update newer only. **Save project** once to store Options
with it.

Empty extra rows are trimmed on save. Empty rows on screen are skipped at
run time.
"""


PURGE_MD = """
**Update newer only** (`/XO`) copies new and newer files. Extra files that
exist only on the destination **stay**. Tick **Delete dest extras (`/PURGE`)**
on the **More flags** card when you also want those dest-only files
removed — for example you deleted a game on the source and want the backup
copy gone too.

| Mode | Copies | Dest-only files | Dest files that are newer |
|---|---|---|---|
| Update newer only | New / newer | Left alone | Left alone (`/XO`) |
| Update + `/PURGE` | New / newer | **Deleted** | Left alone (`/XO`) |
| Mirror (`/MIR`) | Whole tree (`/E` + `/PURGE`) | **Deleted** | Overwritten if Robocopy’s rules say so |

`/MIR` already includes `/PURGE`. While Mirror is on, the app does not add
a second `/PURGE` to the command. The Update, Full copy, and Compare
presets turn Purge off.

**Compare only** never purges and never mirrors.

A **real** Purge or Mirror run (not Dry run / Compare) needs the extra
acknowledgement in the confirm dialog — the same extra step used for
drive roots. Dry-run first (`/L`) to list what would be deleted. There is
no undo.
"""


EXTRA_FLAGS_MD = """
Optional checkboxes on the **More flags** card. They apply to every filled
folder pair, and they travel with a project when you save it.

| Switch | In this app | What it does |
|---|---|---|
| `/PURGE` | **Delete dest extras (`/PURGE`)** | Remove destination files that vanished from source. Extra confirm on a real run. Skipped while Mirror is on. |
| `/XJD` | **Don’t follow directory junctions (`/XJD`)** | Skip junction points that point at other volumes. Safer for backups. |
| `/SEC` | **Copy NTFS ACLs (`/SEC`)** | Copy security descriptors with the files. |
| `/DST` | **Compensate DST 1-hour shift (`/DST`)** | Treat a 1-hour time gap as the same file (USB / NAS around DST). |
| `/J` | **Unbuffered I/O for large files (`/J`)** | Often better for large video/ISO files. |
| `/Z` | **Restartable network copy (`/Z`)** | Resume a large copy over a flaky share. |

`/COPYALL` on **Filters and metadata** already includes ACLs, so the `/SEC`
checkbox is disabled while `/COPYALL` is on.
"""


FILTERS_MD = """
The **Filters and metadata** card on Copy / Compare. Use it when the tree
has junk to skip, when you only want recent files, or when you need more
than default NTFS data. Values apply to every filled pair and are stored
with a project.

**Exclude folders (`/XD`)** — names or paths, separated by spaces or commas.
Typical: `node_modules .git __pycache__`.

**Exclude files (`/XF`)** — names or patterns. Typical: `*.tmp *.log Thumbs.db`.

Each token is one Robocopy argument. No extra quotes in the field; the app
passes them as a list.

**Max age days (`/MAXAGE`)** — `7` means only files from the last 7 days.
`0` means no age limit (`/MAXAGE` is omitted from the command).

**Copy all NTFS info (`/COPYALL`)** — data, attributes, timestamps, ACLs,
owner, and auditing. May need extra privileges. Default Robocopy copy is
data + attributes + timestamps only (`/COPY:DAT`). Tick **Copy NTFS ACLs
(`/SEC`)** on More flags when you only need ACLs. `/COPYALL` turns that
checkbox off because it already includes them.
"""


ROADMAP_MD = """
This version ships three surfaces:

1. **Copy / Compare** — fill one or more source → destination rows
   (three by default, **+** to add more), choose a preset, optional Purge /
   excludes / extra flags, preview the exact `robocopy` command(s), confirm,
   and run. Named projects store folders **and** those options. Pairs run in
   order. Live output, timestamped logs, and **See reports**.
2. **Robocopy manual** — what the tool is, quick start, safety, how it
   differs from Explorer.
3. **Command map** — every interesting switch, with a badge:
   **In this app** vs **UI later**. Destructive switches stay documented.

Wired today: `/E`, `/XO`, `/FFT`, `/R`, `/W`, `/MT`, `/L`, `/MIR`, `/PURGE`,
`/XJD`, `/SEC`, `/COPYALL`, `/DST`, `/J`, `/Z`, `/XD`, `/XF`, `/MAXAGE`,
`/LOG`, `/TEE`, plus `/NP` `/NDL` `/BYTES` for cleaner logs.

Later toggles worth adding: `/MINAGE` and size filters, `/EFSRAW`, `/DCOPY`.
"""


NOT_THE_SAME_MD = """
- **Windows Explorer copy** — Explorer enumerates the destination in a way
  that often feels slow on large trees. Robocopy is built for bulk sync and
  can skip unchanged files without that “Properties → Contains” wait.
- **`xcopy`** — older, fewer filters, no `/MT`, weaker logging.
- **File History / OneDrive / backup software** — those keep versions.
  Robocopy overwrites or skips; it does not snapshot.
- **`cipher` / EFS** — encryption of files. Robocopy can copy EFS files
  (`/EFSRAW` is documented here but not wired).
- **`/XO` vs identical folders** — skipping older files does **not** delete
  extras on the destination and does **not** force equal file counts.
- **Explorer Properties while a copy is still running** — the destination
  count is incomplete. Wait for the job to finish, then recheck.
"""


COMMANDS: list[ToolCommand] = [
    {
        "id": "copy_tree",
        "switch": "/E",
        "title": "Copy subfolders, including empty",
        "syntax": "robocopy src dst /E",
        "group": "Copy shape",
        "status": "ready",
        "danger": False,
        "summary": "Walk the whole tree, including empty directories. Default in this app.",
        "details": (
            "This is the usual “keep the folder shape” switch. `/S` is the same "
            "except it skips empty folders. Wired to **Include subfolders (/E)**."
        ),
        "examples": [r'robocopy C:\Src D:\Dst /E'],
    },
    {
        "id": "subdirs_nonempty",
        "switch": "/S",
        "title": "Copy subfolders, skip empty",
        "syntax": "robocopy src dst /S",
        "group": "Copy shape",
        "status": "planned",
        "danger": False,
        "summary": "Slightly lighter than /E when empty directories do not matter.",
        "details": "Not offered as a toggle yet; the app uses /E when subfolders are on.",
        "examples": [r"robocopy C:\Src D:\Dst /S"],
    },
    {
        "id": "levels",
        "switch": "/LEV:n",
        "title": "Only the first n levels",
        "syntax": "robocopy src dst /E /LEV:2",
        "group": "Copy shape",
        "status": "planned",
        "danger": False,
        "summary": "Cap how deep the walk goes (“copy only the top two levels”).",
        "details": "Useful on huge trees when you only want the top of the hierarchy.",
        "examples": [r"robocopy C:\Src D:\Dst /E /LEV:2"],
    },
    {
        "id": "mirror",
        "switch": "/MIR",
        "title": "Mirror (copy + purge dest extras)",
        "syntax": "robocopy src dst /MIR",
        "group": "Copy shape",
        "status": "ready",
        "danger": True,
        "summary": "/E plus /PURGE: make dest identical to source. Can delete extra dest files.",
        "details": (
            "The Mirror preset in this app forces dry-run on. A real /MIR run "
            "needs an extra confirmation. Wrong source/dest is the usual disaster. "
            "Compare only never uses /MIR."
        ),
        "examples": [
            r"robocopy C:\Src D:\Dst /MIR /L",
            r"robocopy C:\Src D:\Dst /MIR",
        ],
    },
    {
        "id": "purge",
        "switch": "/PURGE",
        "title": "Delete dest files that vanished from source",
        "syntax": "robocopy src dst /E /PURGE",
        "group": "Copy shape",
        "status": "ready",
        "danger": True,
        "summary": "Cleanup without the full /MIR packaging. Still deletes.",
        "details": (
            "Wired as **Delete dest extras (/PURGE)**. A real run needs an extra "
            "confirmation. Compare only never uses /PURGE. /MIR already includes "
            "this delete, so the app skips a separate /PURGE while Mirror is on."
        ),
        "examples": [r"robocopy C:\Src D:\Dst /E /PURGE /L"],
    },
    {
        "id": "move",
        "switch": "/MOV /MOVE",
        "title": "Copy then delete from source",
        "syntax": "robocopy src dst /E /MOVE",
        "group": "Copy shape",
        "status": "planned",
        "danger": True,
        "summary": "True move of files (/MOV) or the whole tree (/MOVE).",
        "details": "Not wired. Easy to empty the source if paths are wrong. Dry-run first if a future tab adds this.",
        "examples": [r"robocopy C:\Src D:\Dst /E /MOVE /L"],
    },
    {
        "id": "create",
        "switch": "/CREATE",
        "title": "Create tree with 0-byte files",
        "syntax": "robocopy src dst /E /CREATE",
        "group": "Copy shape",
        "status": "planned",
        "danger": False,
        "summary": "Pre-create structure / test paths without copying data.",
        "details": "Handy for rehearsing a destination layout. Not in the UI yet.",
        "examples": [r"robocopy C:\Src D:\Dst /E /CREATE"],
    },
    {
        "id": "exclude_older",
        "switch": "/XO",
        "title": "Exclude older destination files",
        "syntax": "robocopy src dst /E /XO",
        "group": "Selection",
        "status": "ready",
        "danger": False,
        "summary": "Skip if dest is newer or the same. Daily “update folder” mode.",
        "details": (
            "Does **not** delete extras on dest and does **not** force equal file "
            "counts. Compare only turns /XO off so both Newer and Older appear."
        ),
        "examples": [r"robocopy C:\Src D:\Dst /E /XO"],
    },
    {
        "id": "exclude_newer",
        "switch": "/XN",
        "title": "Exclude new files",
        "syntax": "robocopy src dst /E /XN",
        "group": "Selection",
        "status": "planned",
        "danger": False,
        "summary": "Do not add files that exist only on the source. Refresh existing files only.",
        "details": "Narrow jobs. Not wired.",
        "examples": [r"robocopy C:\Src D:\Dst /E /XN"],
    },
    {
        "id": "exclude_changed",
        "switch": "/XC",
        "title": "Exclude changed files",
        "syntax": "robocopy src dst /E /XC",
        "group": "Selection",
        "status": "planned",
        "danger": False,
        "summary": "Skip files Robocopy considers changed. Rare.",
        "details": "Not wired.",
        "examples": [r"robocopy C:\Src D:\Dst /E /XC"],
    },
    {
        "id": "exclude_extra",
        "switch": "/XX",
        "title": "Do not report extra dest files",
        "syntax": "robocopy src dst /E /XX",
        "group": "Selection",
        "status": "planned",
        "danger": False,
        "summary": "Quieter logs when dest-only files are not interesting.",
        "details": "Does not delete extras. Not wired.",
        "examples": [r"robocopy C:\Src D:\Dst /E /XX"],
    },
    {
        "id": "exclude_lonely",
        "switch": "/XL",
        "title": "Exclude lonely files",
        "syntax": "robocopy src dst /E /XL",
        "group": "Selection",
        "status": "planned",
        "danger": False,
        "summary": "Only touch files present on both sides.",
        "details": "Not wired.",
        "examples": [r"robocopy C:\Src D:\Dst /E /XL"],
    },
    {
        "id": "include_same",
        "switch": "/IS",
        "title": "Include same files",
        "syntax": "robocopy src dst /E /IS",
        "group": "Selection",
        "status": "planned",
        "danger": False,
        "summary": "Force re-copy even when Robocopy thinks the files are identical.",
        "details": "Not wired.",
        "examples": [r"robocopy C:\Src D:\Dst /E /IS"],
    },
    {
        "id": "include_tweaked",
        "switch": "/IT",
        "title": "Include tweaked files",
        "syntax": "robocopy src dst /E /IT",
        "group": "Selection",
        "status": "planned",
        "danger": False,
        "summary": "Subtle attribute/time edge cases.",
        "details": "Not wired.",
        "examples": [r"robocopy C:\Src D:\Dst /E /IT"],
    },
    {
        "id": "include_modified",
        "switch": "/IM",
        "title": "Include modified (different mtime)",
        "syntax": "robocopy src dst /E /IM",
        "group": "Selection",
        "status": "planned",
        "danger": False,
        "summary": "Copy when size matches but modify time differs.",
        "details": "Not wired.",
        "examples": [r"robocopy C:\Src D:\Dst /E /IM"],
    },
    {
        "id": "fat_times",
        "switch": "/FFT",
        "title": "FAT-style 2-second times",
        "syntax": "robocopy src dst /E /XO /FFT",
        "group": "Selection",
        "status": "ready",
        "danger": False,
        "summary": "Assume 2-second time granularity. Helps USB / FAT / mixed drives.",
        "details": "On by default. Cross-filesystem copies otherwise look “different” every run.",
        "examples": [r"robocopy C:\Src E:\USB\Dst /E /XO /FFT"],
    },
    {
        "id": "dst",
        "switch": "/DST",
        "title": "Compensate 1-hour DST shifts",
        "syntax": "robocopy src dst /E /XO /DST",
        "group": "Selection",
        "status": "ready",
        "danger": False,
        "summary": "Seasonal “everything looks 1h off” noise.",
        "details": "Wired as **Compensate DST 1-hour shift (/DST)**.",
        "examples": [r"robocopy C:\Src D:\Dst /E /XO /DST"],
    },
    {
        "id": "maxage",
        "switch": "/MAXAGE /MINAGE",
        "title": "Filter by file age",
        "syntax": "robocopy src dst /E /MAXAGE:7",
        "group": "Selection",
        "status": "ready",
        "danger": False,
        "summary": "Only files newer/older than n days (or a date).",
        "details": (
            "Wired as **Max age days (/MAXAGE)**. 0 means no limit. "
            "/MINAGE and size filters stay for later."
        ),
        "examples": [
            r"robocopy C:\Src D:\Dst /E /MAXAGE:7",
            r"robocopy C:\Src D:\Dst /E /MIN:1048576",
        ],
    },
    {
        "id": "exclude_files",
        "switch": "/XF /XD",
        "title": "Exclude files and folders by name",
        "syntax": "robocopy src dst /E /XD node_modules .git /XF *.tmp",
        "group": "Exclude",
        "status": "ready",
        "danger": False,
        "summary": "Skip noise: node_modules, .git, Thumbs.db, *.log, …",
        "details": (
            "Wired as **Exclude folders (/XD)** and **Exclude files (/XF)**. "
            "Separate names with spaces or commas. /XA /IA /A /M stay for later."
        ),
        "examples": [
            r"robocopy C:\proj D:\backup\proj /E /XO /XD node_modules .git __pycache__ /XF *.log",
        ],
    },
    {
        "id": "retries",
        "switch": "/R /W",
        "title": "Retries and wait",
        "syntax": "robocopy src dst /R:1 /W:1",
        "group": "Performance",
        "status": "ready",
        "danger": False,
        "summary": "How many times to retry a locked file, and how long to wait.",
        "details": (
            "Robocopy’s built-in default is brutal (~1 million retries, 30s wait). "
            "This app defaults to /R:1 /W:1 so locked files fail fast."
        ),
        "examples": [r"robocopy C:\Src D:\Dst /E /R:1 /W:1"],
    },
    {
        "id": "multithread",
        "switch": "/MT",
        "title": "Multi-threaded copy",
        "syntax": "robocopy src dst /E /MT:8",
        "group": "Performance",
        "status": "ready",
        "danger": False,
        "summary": "Parallel copy. Default 8 in this app, max 128. 1 = off.",
        "details": "Great for many small files. Tune down on a live server disk. Incompatible with /IPG.",
        "examples": [r"robocopy C:\Src D:\Dst /E /XO /MT:8"],
    },
    {
        "id": "unbuffered",
        "switch": "/J",
        "title": "Unbuffered I/O",
        "syntax": "robocopy src dst /E /J",
        "group": "Performance",
        "status": "ready",
        "danger": False,
        "summary": "Often better for very large files.",
        "details": "Wired as **Unbuffered I/O for large files (/J)**.",
        "examples": [r"robocopy C:\Video D:\Backup\Video /E /XO /J /MT:4"],
    },
    {
        "id": "restartable",
        "switch": "/Z /ZB /B",
        "title": "Restartable / backup mode",
        "syntax": "robocopy src dst /E /Z",
        "group": "Performance",
        "status": "ready",
        "danger": False,
        "summary": "/Z resumes large interrupted network copies. /B needs backup privileges.",
        "details": (
            "Wired as **Restartable network copy (/Z)**. /B and /ZB stay for later."
        ),
        "examples": [r"robocopy \\\\server\\share D:\\Local /E /XO /Z"],
    },
    {
        "id": "throttle",
        "switch": "/IPG /IoRate",
        "title": "Throttle I/O",
        "syntax": "robocopy src dst /E /IPG:50",
        "group": "Performance",
        "status": "planned",
        "danger": False,
        "summary": "Protect a slow link or a live server disk from being saturated.",
        "details": "/IPG is incompatible with /MT. /COMPRESS requests SMB compression.",
        "examples": [r"robocopy C:\Src \\\\nas\\Dst /E /XO /IPG:50"],
    },
    {
        "id": "copy_data",
        "switch": "/COPY /SEC /COPYALL",
        "title": "What metadata to copy",
        "syntax": "robocopy src dst /E /COPYALL",
        "group": "Security & metadata",
        "status": "ready",
        "danger": False,
        "summary": "Default is data + attributes + timestamps. /SEC adds ACLs; /COPYALL adds owner and auditing.",
        "details": (
            "Wired as **Copy NTFS ACLs (/SEC)** and **Copy all NTFS info (/COPYALL)**. "
            "/COPYALL implies ACLs, so /SEC is disabled while it is on. Also /XJD as "
            "**Don’t follow directory junctions**. /DCOPY, /EFSRAW, /SL stay for later."
        ),
        "examples": [
            r"robocopy C:\Src D:\Dst /E /COPYALL /DCOPY:DAT",
            r"robocopy C:\Src D:\Dst /E /XO /XJD",
        ],
    },
    {
        "id": "list_only",
        "switch": "/L",
        "title": "List only (dry-run)",
        "syntax": "robocopy src dst /E /L",
        "group": "Logging",
        "status": "ready",
        "danger": False,
        "summary": "No copy, no delete. Best friend of /MIR. Compare only always uses this.",
        "details": (
            "Wired as **Dry run** and as **Compare only**. Compare also writes "
            "compare_*.log and turns off /XO so both newer and older diffs show."
        ),
        "examples": [
            r"robocopy C:\Src D:\Dst /E /XO /L",
            r"robocopy C:\Src D:\Dst /E /FFT /L",
        ],
    },
    {
        "id": "log",
        "switch": "/LOG /TEE",
        "title": "Write a log and still show output",
        "syntax": 'robocopy src dst /E /LOG:"C:\\logs\\job.log" /TEE',
        "group": "Logging",
        "status": "ready",
        "danger": False,
        "summary": "This app writes a new timestamped log per run so jobs never overwrite each other.",
        "details": (
            "/LOG: overwrites; /LOG+: appends; /UNILOG: is Unicode. The UI also "
            "passes /NP /NDL /BYTES for a cleaner live stream and parseable sizes. "
            "Open **See reports** to table the log."
        ),
        "examples": [
            r'robocopy C:\Src D:\Dst /E /XO /LOG:"C:\logs\robocopy.log" /TEE',
        ],
    },
    {
        "id": "verbose",
        "switch": "/V /ETA /TS /FP",
        "title": "Richer console output",
        "syntax": "robocopy src dst /E /L /V",
        "group": "Logging",
        "status": "planned",
        "danger": False,
        "summary": "Verbose skipped files, ETA, timestamps, full paths.",
        "details": "Not toggled yet. The app currently prefers /NP /NDL for a readable live log.",
        "examples": [r"robocopy C:\Src D:\Dst /E /L /V"],
    },
    {
        "id": "jobs",
        "switch": "/SAVE /JOB /QUIT",
        "title": "Save and reload a job file",
        "syntax": "robocopy src dst /E /XO /SAVE:nightly",
        "group": "Jobs",
        "status": "planned",
        "danger": False,
        "summary": "Persist a command line as a job; /QUIT parses flags and exits (debug).",
        "details": "Not wired. Recurring recipes are still “copy the preview” or Task Scheduler.",
        "examples": [
            r"robocopy C:\Src D:\Dst /E /XO /SAVE:nightly",
            r"robocopy /JOB:nightly",
        ],
    },
    {
        "id": "monitor",
        "switch": "/MON /MOT /RH",
        "title": "Watch and re-run / run in a time window",
        "syntax": "robocopy src dst /E /XO /MOT:5",
        "group": "Jobs",
        "status": "planned",
        "danger": False,
        "summary": "Re-run after source changes, or only between given hours.",
        "details": "Not wired. Easy to leave a long-lived robocopy.exe. Prefer Task Scheduler for nightly jobs.",
        "examples": [
            r"robocopy C:\Src D:\Dst /E /XO /MOT:5",
            r"robocopy C:\Src D:\Dst /MIR /RH:2200-0600",
        ],
    },
]


STATUS_LABEL = {"ready": "In this app", "planned": "UI later"}
STATUS_COLOR = {"ready": "positive", "planned": "grey"}


def commands_by_group() -> list[tuple[str, list[ToolCommand]]]:
    grouped: dict[str, list[ToolCommand]] = {g: [] for g in GROUPS}
    for cmd in COMMANDS:
        grouped.setdefault(cmd["group"], []).append(cmd)
    return [(g, grouped[g]) for g in GROUPS if grouped.get(g)]
