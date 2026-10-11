"""
Windows Robocopy helpers for Explorer Advance.

Command building, process control, and log parsing live here.
No UI code.
"""

from __future__ import annotations

import ctypes
import os
import re
import subprocess
import sys
from ctypes import wintypes
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, NamedTuple, Optional

APP_DIR = Path(__file__).resolve().parent
LOGS_DIR = APP_DIR / "logs"
DEFAULT_LOG = str(LOGS_DIR / "robocopy.log")
DEFAULT_PAIR_ROWS = 3

# Robocopy: exit codes 0–7 are the "success" family; 8+ indicate failure.
ROBOCOPY_SUCCESS_MAX = 7

ROBOCOPY_EXIT_MEANING = {
    0: "No files copied (already up to date).",
    1: "Files copied successfully.",
    2: "Extra files/dirs in destination (not an error).",
    3: "Files copied + extra files/dirs present.",
    4: "Mismatched files/dirs detected.",
    5: "Copied + mismatched.",
    6: "Extra + mismatched.",
    7: "Copied + extra + mismatched.",
    8: "Some files/dirs could not be copied (failure).",
}


@dataclass
class FolderPair:
    """One source → destination job. Empty rows are skipped at run time."""

    source: str = ""
    destination: str = ""


@dataclass
class FolderList:
    """A named project: folder pairs plus the job options used with them."""

    name: str = ""
    pairs: list[FolderPair] = field(default_factory=list)
    options: dict[str, Any] = field(default_factory=dict)


@dataclass
class SyncOptions:
    source: str = ""
    destination: str = ""
    # All source → destination rows (includes row 1). Empty rows are skipped.
    pairs: list[FolderPair] = field(default_factory=list)
    saved_lists: list[FolderList] = field(default_factory=list)
    active_list: str = ""
    # Preferred log location (file or folder). Each run writes a new timestamped
    # file in this folder so concurrent/sequential jobs never overwrite each other.
    log_path: str = field(default_factory=lambda: DEFAULT_LOG)
    copy_subdirs: bool = True  # /E
    exclude_older: bool = True  # /XO — only copy if source is newer
    fat_file_times: bool = True  # /FFT — 2s granularity (USB / mixed FS)
    retries: int = 1  # /R:n
    wait_seconds: int = 1  # /W:n
    multi_thread: int = 8  # /MT:n (1 = off)
    dry_run: bool = False  # /L — list only, no copy
    # Compare mode: force list-only, no mirror, show time/size diffs (no /XO),
    # write a compare_*.log for visual confirmation without copying.
    compare_only: bool = False
    mirror: bool = False  # /MIR — EXTRA deletes dest files not in source
    tee_console: bool = True  # /TEE
    write_log: bool = True  # /LOG:
    purge: bool = False  # /PURGE — delete dest extras; extra confirm
    skip_junctions: bool = False  # /XJD
    copy_security: bool = False  # /SEC
    dst_adjust: bool = False  # /DST
    unbuffered: bool = False  # /J
    restartable: bool = False  # /Z
    exclude_dirs: str = ""  # /XD names, space or comma separated
    exclude_files: str = ""  # /XF names, space or comma separated
    max_age_days: int = 0  # /MAXAGE:n ; 0 = off
    copy_all: bool = False  # /COPYALL


# Options stored with a named project (everything except paths and the project list).
JOB_OPTION_FIELDS = (
    "copy_subdirs",
    "exclude_older",
    "fat_file_times",
    "retries",
    "wait_seconds",
    "multi_thread",
    "dry_run",
    "compare_only",
    "mirror",
    "tee_console",
    "write_log",
    "purge",
    "skip_junctions",
    "copy_security",
    "dst_adjust",
    "unbuffered",
    "restartable",
    "exclude_dirs",
    "exclude_files",
    "max_age_days",
    "copy_all",
)


def _as_folder_pair(item: Any) -> Optional[FolderPair]:
    if isinstance(item, FolderPair):
        return FolderPair(source=item.source or "", destination=item.destination or "")
    if isinstance(item, dict):
        return FolderPair(
            source=str(item.get("source") or ""),
            destination=str(item.get("destination") or ""),
        )
    if isinstance(item, (list, tuple)) and len(item) >= 2:
        return FolderPair(source=str(item[0] or ""), destination=str(item[1] or ""))
    return None


def coerce_pairs(
    raw: Any,
    *,
    source: str = "",
    destination: str = "",
    pad_to_default: bool = False,
) -> list[FolderPair]:
    """Turn config JSON / legacy source+dest into a list of folder pairs."""
    pairs: list[FolderPair] = []
    if isinstance(raw, list):
        for item in raw:
            parsed = _as_folder_pair(item)
            if parsed is not None:
                pairs.append(parsed)
    if not pairs:
        pairs = [FolderPair(source=source or "", destination=destination or "")]
        pad_to_default = True
    if pad_to_default:
        while len(pairs) < DEFAULT_PAIR_ROWS:
            pairs.append(FolderPair())
    return pairs


def ensure_pairs(opts: SyncOptions, *, pad_to_default: bool = False) -> SyncOptions:
    opts.pairs = coerce_pairs(
        opts.pairs,
        source=opts.source,
        destination=opts.destination,
        pad_to_default=pad_to_default,
    )
    sync_legacy_paths(opts)
    return opts


def sync_legacy_paths(opts: SyncOptions) -> None:
    """Keep source/destination in sync with the first pair (older configs, reports)."""
    if opts.pairs:
        opts.source = opts.pairs[0].source
        opts.destination = opts.pairs[0].destination


def trim_pairs_for_save(pairs: list[FolderPair]) -> list[FolderPair]:
    """Drop trailing empty extra rows, but keep at least one row."""
    trimmed = [FolderPair(source=p.source, destination=p.destination) for p in pairs]
    while len(trimmed) > DEFAULT_PAIR_ROWS:
        last = trimmed[-1]
        if last.source.strip() or last.destination.strip():
            break
        trimmed.pop()
    if not trimmed:
        trimmed = [FolderPair() for _ in range(DEFAULT_PAIR_ROWS)]
    return trimmed


def clone_pairs(pairs: list[FolderPair]) -> list[FolderPair]:
    return [FolderPair(source=p.source or "", destination=p.destination or "") for p in pairs]


def snapshot_job_options(opts: SyncOptions) -> dict[str, Any]:
    return {name: getattr(opts, name) for name in JOB_OPTION_FIELDS}


def coerce_job_options(raw: Any) -> dict[str, Any]:
    """Full option set from stored JSON. Missing keys use SyncOptions defaults."""
    defaults = SyncOptions()
    out: dict[str, Any] = {name: getattr(defaults, name) for name in JOB_OPTION_FIELDS}
    if not isinstance(raw, dict):
        return out
    for name in JOB_OPTION_FIELDS:
        if name not in raw:
            continue
        default = getattr(defaults, name)
        val = raw[name]
        try:
            if isinstance(default, bool):
                out[name] = bool(val)
            elif isinstance(default, int):
                out[name] = int(val)
            else:
                out[name] = str(val or "")
        except (TypeError, ValueError):
            out[name] = default
    return out


def apply_job_options(opts: SyncOptions, raw: Any) -> bool:
    """Restore project options onto the working set.

    Missing keys use SyncOptions defaults (Update newer only). Returns True
    when the project actually stored an options dict.
    """
    had = isinstance(raw, dict) and bool(raw)
    stored = raw if isinstance(raw, dict) else {}
    for name, value in coerce_job_options(stored).items():
        setattr(opts, name, value)
    return had


def default_job_options() -> dict[str, Any]:
    """Basic Update newer only — the fresh-project defaults."""
    return snapshot_job_options(SyncOptions())


def reset_working_project(opts: SyncOptions) -> None:
    """Empty folder rows + Update newer only. Saved projects and log_path stay."""
    opts.active_list = ""
    opts.pairs = [FolderPair() for _ in range(DEFAULT_PAIR_ROWS)]
    apply_job_options(opts, default_job_options())
    sync_legacy_paths(opts)


def attach_active_list_snapshot(opts: SyncOptions) -> None:
    """Copy current folders + options onto the active named project (in memory)."""
    item = find_saved_list(opts, opts.active_list)
    if item is None:
        return
    if any(p.source.strip() and p.destination.strip() for p in opts.pairs):
        item.pairs = trim_pairs_for_save(clone_pairs(opts.pairs))
    item.options = snapshot_job_options(opts)


def split_robocopy_names(raw: str) -> list[str]:
    """Tokens for /XD and /XF. Spaces or commas; each token is one argv item."""
    text = (raw or "").replace(",", " ")
    return [tok for tok in text.split() if tok]


def coerce_saved_lists(raw: Any) -> list[FolderList]:
    """Turn config JSON into named projects. Duplicate names keep the first."""
    out: list[FolderList] = []
    if not isinstance(raw, list):
        return out
    seen: set[str] = set()
    for item in raw:
        name = ""
        pairs_raw: Any = None
        options_raw: Any = None
        if isinstance(item, FolderList):
            name = (item.name or "").strip()
            pairs_raw = item.pairs
            options_raw = item.options
        elif isinstance(item, dict):
            name = str(item.get("name") or "").strip()
            pairs_raw = item.get("pairs")
            options_raw = item.get("options")
        if not name:
            continue
        key = name.casefold()
        if key in seen:
            continue
        seen.add(key)
        pairs = coerce_pairs(pairs_raw, pad_to_default=False)
        options = (
            coerce_job_options(options_raw) if isinstance(options_raw, dict) else {}
        )
        out.append(
            FolderList(name=name, pairs=trim_pairs_for_save(pairs), options=options)
        )
    return out


def find_saved_list(opts: SyncOptions, name: str) -> Optional[FolderList]:
    key = (name or "").strip().casefold()
    if not key:
        return None
    for item in opts.saved_lists:
        if item.name.strip().casefold() == key:
            return item
    return None


def upsert_saved_list(
    opts: SyncOptions,
    name: str,
    pairs: list[FolderPair],
) -> Optional[str]:
    """Store current rows under `name`. Returns an error string, or None on success."""
    name = (name or "").strip()
    if not name:
        return "Enter a name for this project."
    snapshot = trim_pairs_for_save(clone_pairs(pairs))
    if not any(p.source.strip() and p.destination.strip() for p in snapshot):
        return "Fill at least one source and destination before saving a project."
    stored = snapshot_job_options(opts)
    existing = find_saved_list(opts, name)
    if existing:
        existing.pairs = snapshot
        existing.options = stored
        opts.active_list = existing.name
    else:
        opts.saved_lists.append(
            FolderList(name=name, pairs=snapshot, options=stored)
        )
        opts.active_list = name
    return None


def apply_saved_list(opts: SyncOptions, name: str) -> Optional[str]:
    """Replace the working folder rows and options with a named project."""
    item = find_saved_list(opts, name)
    if item is None:
        return f"No saved project named “{name}”."
    opts.pairs = clone_pairs(item.pairs)
    while len(opts.pairs) < DEFAULT_PAIR_ROWS:
        opts.pairs.append(FolderPair())
    opts.active_list = item.name
    apply_job_options(opts, item.options)
    sync_legacy_paths(opts)
    return None


def delete_saved_list(opts: SyncOptions, name: str) -> Optional[str]:
    item = find_saved_list(opts, name)
    if item is None:
        return "That saved project was not found."
    opts.saved_lists = [entry for entry in opts.saved_lists if entry is not item]
    if opts.active_list.strip().casefold() == item.name.strip().casefold():
        opts.active_list = ""
    return None


def filled_pairs(opts: SyncOptions) -> list[FolderPair]:
    """Complete source+destination rows that will actually run."""
    rows = opts.pairs or []
    if not rows and (opts.source.strip() or opts.destination.strip()):
        rows = [FolderPair(source=opts.source, destination=opts.destination)]
    return [
        FolderPair(source=p.source.strip(), destination=p.destination.strip())
        for p in rows
        if p.source.strip() and p.destination.strip()
    ]


def pairs_include_drive_root(opts: SyncOptions) -> bool:
    for pair in filled_pairs(opts):
        if is_drive_root(pair.source) or is_drive_root(pair.destination):
            return True
    return False


class RobocopyProcess(NamedTuple):
    pid: int
    memory: str


def is_windows() -> bool:
    return sys.platform == "win32"


def creationflags_no_window() -> int:
    if is_windows():
        return int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return 0


def is_drive_root(path: str) -> bool:
    if not path or not str(path).strip():
        return False
    abs_path = os.path.abspath(str(path).strip())
    _drive, tail = os.path.splitdrive(abs_path)
    return tail in {"\\", "/", ""}


def pick_folder(start: str = "", title: str = "Select folder") -> Optional[str]:
    """Native Windows folder dialog (tkinter). Returns None if cancelled."""
    try:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        initial = start if start and Path(start).is_dir() else str(Path.home())
        path = filedialog.askdirectory(initialdir=initial, title=title)
        root.destroy()
        return path or None
    except Exception:
        return None


def open_in_explorer(path: str) -> None:
    """Reveal `path` in Explorer. Folders open in place; files are selected."""
    if not path or not str(path).strip():
        raise ValueError("No path to open.")
    target = Path(str(path).strip())
    if not target.exists():
        parent = target.parent
        if parent.exists():
            os.startfile(str(parent))  # type: ignore[attr-defined]
            return
        raise FileNotFoundError(str(target))
    if target.is_dir():
        os.startfile(str(target))  # type: ignore[attr-defined]
        return
    subprocess.Popen(["explorer", "/n", "/select," + str(target)])


def log_directory_from_opts(opts: SyncOptions) -> Path:
    """Folder where timestamped run logs are written."""
    raw = (opts.log_path or "").strip() or DEFAULT_LOG
    path = Path(raw)
    try:
        if path.is_dir():
            return path
    except OSError:
        pass
    if path.suffix:
        parent = path.parent
        return parent if str(parent) not in {"", "."} else LOGS_DIR
    return path if str(path) else LOGS_DIR


def folder_for_new_session_logs(raw: str) -> str:
    """Drop a previous run’s .log filename so a new session does not reopen it."""
    text = (raw or "").strip()
    if not text:
        return str(LOGS_DIR)
    path = Path(text)
    if path.suffix.lower() == ".log":
        parent = path.parent
        if str(parent) not in {"", "."}:
            return str(parent)
        return str(LOGS_DIR)
    return str(path)


def make_run_log_path(
    opts: SyncOptions,
    when: Optional[datetime] = None,
    *,
    job_index: Optional[int] = None,
) -> str:
    """Unique log file path for one Robocopy run."""
    directory = log_directory_from_opts(opts)
    directory.mkdir(parents=True, exist_ok=True)
    stamp = (when or datetime.now()).strftime("%Y%m%d_%H%M%S")
    prefix = "compare" if opts.compare_only else "robocopy"
    extra = f"_{job_index}" if job_index is not None else ""
    candidate = directory / f"{prefix}_{stamp}{extra}.log"
    n = 2
    while candidate.exists():
        candidate = directory / f"{prefix}_{stamp}{extra}_{n}.log"
        n += 1
    return str(candidate)


def effective_dry_run(opts: SyncOptions) -> bool:
    """True when no files will be copied (dry-run or compare-only)."""
    return bool(opts.dry_run or opts.compare_only)


def will_write(opts: SyncOptions) -> bool:
    """True when Robocopy may copy or delete files."""
    return not effective_dry_run(opts)


def find_running_robocopy() -> list[RobocopyProcess]:
    """Return other robocopy.exe processes (Windows). Empty if none / not Windows."""
    if not is_windows():
        return []
    try:
        out = subprocess.check_output(
            [
                "tasklist",
                "/FI",
                "IMAGENAME eq robocopy.exe",
                "/FO",
                "CSV",
                "/NH",
            ],
            creationflags=creationflags_no_window(),
            text=True,
            errors="replace",
            timeout=10,
        )
    except (subprocess.SubprocessError, OSError, FileNotFoundError):
        return []

    found: list[RobocopyProcess] = []
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        low = line.lower()
        if low.startswith("info:") or low.startswith("information"):
            continue
        if "robocopy.exe" not in low:
            continue
        parts = [p.strip().strip('"') for p in line.split('","')]
        if len(parts) < 2:
            parts = [p.strip().strip('"') for p in line.split(",")]
        name = (parts[0] if parts else "").lower()
        if name not in {"robocopy.exe", "robocopy"}:
            continue
        try:
            pid = int(parts[1])
        except (IndexError, ValueError):
            continue
        mem = parts[4] if len(parts) > 4 else "?"
        found.append(RobocopyProcess(pid=pid, memory=mem))
    return found


def stop_robocopy_processes(pids: Optional[list[int]] = None) -> tuple[list[int], list[str]]:
    """Force-stop robocopy.exe processes. Returns (stopped_pids, error_messages)."""
    targets = list(pids) if pids is not None else [p.pid for p in find_running_robocopy()]
    stopped: list[int] = []
    errors: list[str] = []
    if not is_windows():
        return stopped, ["Stop is only implemented on Windows."]
    for pid in targets:
        try:
            completed = subprocess.run(
                ["taskkill", "/PID", str(pid), "/F"],
                capture_output=True,
                text=True,
                errors="replace",
                timeout=10,
                creationflags=creationflags_no_window(),
            )
        except (subprocess.SubprocessError, OSError, FileNotFoundError) as exc:
            errors.append(f"PID {pid}: {exc}")
            continue
        if completed.returncode == 0:
            stopped.append(pid)
        else:
            detail = (completed.stderr or completed.stdout or "").strip() or f"exit {completed.returncode}"
            errors.append(f"PID {pid}: {detail}")
    return stopped, errors


# Job object so robocopy.exe dies if the Python window is closed.
_kill_job_handle = None


def _windows_kill_on_close_job():
    """Windows job with KILL_ON_JOB_CLOSE — children exit with this process."""
    global _kill_job_handle
    if not is_windows():
        return None
    if _kill_job_handle:
        return _kill_job_handle
    try:
        class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", wintypes.LARGE_INTEGER),
                ("PerJobUserTimeLimit", wintypes.LARGE_INTEGER),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class _IO_COUNTERS(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount", ctypes.c_uint64),
                ("WriteOperationCount", ctypes.c_uint64),
                ("OtherOperationCount", ctypes.c_uint64),
                ("ReadTransferCount", ctypes.c_uint64),
                ("WriteTransferCount", ctypes.c_uint64),
                ("OtherTransferCount", ctypes.c_uint64),
            ]

        class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", _JOBOBJECT_BASIC_LIMIT_INFORMATION),
                ("IoInfo", _IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.SetInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            wintypes.LPVOID,
            wintypes.DWORD,
        ]
        handle = kernel32.CreateJobObjectW(None, None)
        if not handle:
            return None
        info = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        ok = kernel32.SetInformationJobObject(
            handle,
            9,  # JobObjectExtendedLimitInformation
            ctypes.byref(info),
            ctypes.sizeof(info),
        )
        if not ok:
            kernel32.CloseHandle(handle)
            return None
        _kill_job_handle = handle
        return handle
    except Exception:
        return None


def assign_pid_to_kill_job(pid: int) -> None:
    """Attach a child PID to the kill-on-close job (best-effort)."""
    job = _windows_kill_on_close_job()
    if not job or not is_windows():
        return
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        handle = kernel32.OpenProcess(0x0100 | 0x0001, False, int(pid))
        if not handle:
            return
        try:
            kernel32.AssignProcessToJobObject(job, handle)
        finally:
            kernel32.CloseHandle(handle)
    except Exception:
        pass


def build_robocopy_args(
    opts: SyncOptions,
    *,
    source: Optional[str] = None,
    destination: Optional[str] = None,
    run_log: Optional[str] = None,
    preview: bool = False,
    job_index: Optional[int] = None,
) -> list[str]:
    """
    Build robocopy argv.

    - preview=True: use a placeholder timestamp in /LOG so the command line
      shows the naming pattern (no file is created yet).
    - run_log: concrete path for an actual run (unique per job).
    - source / destination: override opts.source / opts.destination for one pair.
    - job_index: suffix on the preview log name when several pairs will run.
    - compare_only: forces /L (no copy), never /MIR, and omits /XO so older
      and newer differences both appear in the log for visual confirmation.
    """
    compare = bool(opts.compare_only)
    src = (opts.source if source is None else source).strip()
    dst = (opts.destination if destination is None else destination).strip()
    args = [
        "robocopy",
        src,
        dst,
    ]
    if opts.copy_subdirs:
        args.append("/E")
    if opts.exclude_older and not compare:
        args.append("/XO")
    if opts.fat_file_times:
        args.append("/FFT")
    args.append(f"/R:{max(0, int(opts.retries))}")
    args.append(f"/W:{max(0, int(opts.wait_seconds))}")
    mt = int(opts.multi_thread)
    if mt > 1:
        args.append(f"/MT:{min(128, mt)}")
    if effective_dry_run(opts):
        args.append("/L")
    if opts.mirror and not compare:
        args.append("/MIR")
    elif opts.purge and not compare:
        args.append("/PURGE")
    if opts.skip_junctions:
        args.append("/XJD")
    if opts.copy_all:
        args.append("/COPYALL")
    elif opts.copy_security:
        args.append("/SEC")
    if opts.dst_adjust:
        args.append("/DST")
    if opts.unbuffered:
        args.append("/J")
    if opts.restartable:
        args.append("/Z")
    try:
        max_age = int(opts.max_age_days or 0)
    except (TypeError, ValueError):
        max_age = 0
    if max_age > 0:
        args.append(f"/MAXAGE:{max_age}")
    xd = split_robocopy_names(opts.exclude_dirs)
    if xd:
        args.append("/XD")
        args.extend(xd)
    xf = split_robocopy_names(opts.exclude_files)
    if xf:
        args.append("/XF")
        args.extend(xf)
    write_log = opts.write_log or compare
    if write_log:
        if run_log:
            log = run_log
            Path(log).parent.mkdir(parents=True, exist_ok=True)
        elif preview:
            prefix = "compare" if compare else "robocopy"
            tag = f"_{job_index}" if job_index is not None else ""
            log = str(log_directory_from_opts(opts) / f"{prefix}_YYYYMMDD_HHMMSS{tag}.log")
        elif opts.log_path.strip():
            log = make_run_log_path(opts)
        else:
            log = None
        if log:
            args.append(f"/LOG:{log}")
            if opts.tee_console:
                args.append("/TEE")
    args.append("/NP")
    args.append("/NDL")
    args.append("/BYTES")
    return args


def preview_all_commands(opts: SyncOptions) -> str:
    """One robocopy command line per filled folder pair (preview timestamps)."""
    pairs = filled_pairs(opts)
    if not pairs:
        return "(add at least one source → destination pair)"
    multi = len(pairs) > 1
    lines: list[str] = []
    for i, pair in enumerate(pairs, start=1):
        args = build_robocopy_args(
            opts,
            source=pair.source,
            destination=pair.destination,
            preview=True,
            job_index=i if multi else None,
        )
        lines.append(format_command_preview(args))
    return "\n".join(lines)


def format_command_preview(args: list[str]) -> str:
    """Quote paths for display (cmd/PowerShell-friendly)."""
    parts: list[str] = []
    for a in args:
        if a.startswith("/LOG:"):
            path = a[5:].replace('"', "")
            parts.append(f'/LOG:"{path}"')
            continue
        if not a.startswith("/") and (re.search(r"\s", a) or re.match(r"^[A-Za-z]:\\", a)):
            parts.append('"' + a.replace('"', "") + '"')
        else:
            parts.append(a)
    return " ".join(parts)


def validate_pair(source: str, destination: str) -> Optional[str]:
    src = source.strip()
    dst = destination.strip()
    if not src:
        return "Please set a source folder."
    if not dst:
        return "Please set a destination folder."
    if not Path(src).is_dir():
        return f"Source folder does not exist:\n{src}"
    parent = Path(dst).parent
    if dst and not Path(dst).exists() and parent != Path(dst) and not parent.exists():
        return f"Destination parent folder does not exist:\n{parent}"
    if os.path.normcase(os.path.abspath(src)) == os.path.normcase(os.path.abspath(dst)):
        return "Source and destination must be different folders."
    return None


def validate_opts(opts: SyncOptions) -> Optional[str]:
    rows = opts.pairs or []
    if not rows:
        rows = [FolderPair(source=opts.source, destination=opts.destination)]
    many = len(rows) > 1
    complete = 0
    for i, pair in enumerate(rows, start=1):
        src = pair.source.strip()
        dst = pair.destination.strip()
        if not src and not dst:
            continue
        if not src or not dst:
            return (
                f"Row {i} has only a source or only a destination. "
                "Fill both sides, or clear the row."
            )
        err = validate_pair(src, dst)
        if err:
            return f"Row {i}: {err}" if many else err
        complete += 1
    if complete == 0:
        return "Add at least one source and destination folder."
    return None


# ---------------------------------------------------------------------------
# Robocopy log → human report
# ---------------------------------------------------------------------------

_ACTION_RULES: list[tuple[re.Pattern[str], str, str]] = [
    (re.compile(r"nouveau\s+fichier|new\s+file", re.I), "transferred", "New file"),
    (re.compile(r"plus\s+r[eé]cent|\bnewer\b", re.I), "transferred", "Newer"),
    (re.compile(r"modifi[eé]|\bmodified\b", re.I), "transferred", "Modified"),
    (re.compile(r"plus\s+ancien|\bolder\b", re.I), "skipped", "Older"),
    (re.compile(r"identique|\bsame\b", re.I), "skipped", "Same (skipped)"),
    (re.compile(r"suppl\.?|extra\s+file|\*?\s*extra\b", re.I), "extra", "Extra on dest"),
    (re.compile(r"isol[eé]|\blonely\b", re.I), "lonely", "Lonely"),
    (re.compile(r"nouveau\s+r[eé]p|new\s+dir", re.I), "dir", "New folder"),
    (re.compile(r"error|erreur|0x[0-9a-f]{8}", re.I), "error", "Error"),
]

_PATH_START = re.compile(r"(?<![A-Za-z0-9_])([A-Za-z]:\\|\\\\)")
_SIZE_TAIL = re.compile(
    r"(?P<size>\d+(?:[.,]\d+)?(?:\s*[kKmMgGtT][bB]?)?)\s*$"
)
_SIZE_VALUE = re.compile(
    r"^\s*(?P<num>\d+(?:[.,]\d+)?)\s*(?P<unit>[kKmMgGtT][bB]?)?\s*$"
)
_UNIT_TO_BYTES = {
    "": 1,
    "k": 1024,
    "kb": 1024,
    "m": 1024**2,
    "mb": 1024**2,
    "g": 1024**3,
    "gb": 1024**3,
    "t": 1024**4,
    "tb": 1024**4,
}
_HEADER_SOURCE = re.compile(r"^\s*Source\s*:\s*(.+?)\s*$", re.I)
_HEADER_DEST = re.compile(r"^\s*Dest(?:ination)?\s*:\s*(.+?)\s*$", re.I)
_HEADER_OPTIONS = re.compile(r"^\s*Options\s*:\s*(.+?)\s*$", re.I)
_HEADER_STARTED = re.compile(
    r"^\s*(?:Started|D[eé]marrage)\s*:?\s*(.+?)\s*$", re.I
)
_HEADER_ENDED = re.compile(r"^\s*(?:Ended|Fin(?:i|e)?)\s*:?\s*(.+?)\s*$", re.I)


@dataclass
class LogFileEntry:
    kind: str  # transferred | extra | skipped | error | dir | lonely | other
    label: str
    size: str
    path: str
    raw_status: str


def parse_size_bytes(size: str) -> Optional[int]:
    """Turn a Robocopy log size field into an integer byte count."""
    if not size:
        return None
    m = _SIZE_VALUE.match(size.replace("\u00a0", " "))
    if not m:
        return None
    raw = m.group("num").replace(",", ".")
    try:
        value = float(raw)
    except ValueError:
        return None
    unit = (m.group("unit") or "").lower()
    mult = _UNIT_TO_BYTES.get(unit)
    if mult is None:
        return None
    return int(value * mult)


def format_bytes(n: int) -> str:
    """Human-readable size for report badges (1024-based)."""
    if n < 0:
        n = 0
    if n < 1024:
        return f"{n} B"
    for unit, exp in (("KB", 1), ("MB", 2), ("GB", 3), ("TB", 4)):
        div = float(1024**exp)
        if n < 1024 ** (exp + 1) or unit == "TB":
            val = n / div
            if val >= 100:
                return f"{val:.0f} {unit}"
            if val >= 10:
                return f"{val:.1f} {unit}"
            return f"{val:.2f} {unit}"
    return f"{n} B"


def sum_entry_bytes(entries: list[LogFileEntry]) -> int:
    total = 0
    for entry in entries:
        n = parse_size_bytes(entry.size)
        if n is not None:
            total += n
    return total


@dataclass
class RobocopyReport:
    log_path: str
    source: str = ""
    destination: str = ""
    options: str = ""
    started: str = ""
    ended: str = ""
    entries: list[LogFileEntry] = field(default_factory=list)
    summary_block: list[str] = field(default_factory=list)
    parse_notes: list[str] = field(default_factory=list)
    incomplete: bool = False

    @property
    def transferred(self) -> list[LogFileEntry]:
        return [e for e in self.entries if e.kind == "transferred"]

    @property
    def extras(self) -> list[LogFileEntry]:
        return [e for e in self.entries if e.kind == "extra"]

    @property
    def extras_bytes(self) -> int:
        return sum_entry_bytes(self.extras)

    @property
    def errors(self) -> list[LogFileEntry]:
        return [e for e in self.entries if e.kind == "error"]

    @property
    def skipped(self) -> list[LogFileEntry]:
        return [e for e in self.entries if e.kind == "skipped"]

    @property
    def differences(self) -> list[LogFileEntry]:
        """Files that differ between trees (not identical/skipped-same noise)."""
        out: list[LogFileEntry] = []
        for e in self.entries:
            if e.kind in {"transferred", "extra", "lonely", "error", "dir"}:
                out.append(e)
            elif e.kind == "skipped" and re.search(
                r"older|plus\s+ancien", e.raw_status, re.I
            ):
                out.append(e)
        return out


def is_compare_log(report: RobocopyReport) -> bool:
    """Detect list-only / compare runs from log path or Options line."""
    name = Path(report.log_path).name.lower()
    if name.startswith("compare_"):
        return True
    opts_line = report.options or ""
    return bool(re.search(r"(?:^|\s)/L(?:\s|$)", opts_line))


def format_compare_verdict(
    code: int,
    report: Optional[RobocopyReport] = None,
) -> tuple[bool, str, str, list[str]]:
    """
    Human-readable compare outcome.

    Returns:
        folders_match, short_headline, one_line_status, multi-line banner for log/UI
    """
    if code > ROBOCOPY_SUCCESS_MAX:
        headline = "COMPARE FAILED — CHECK THE LOG"
        detail = (
            f"The comparison did not finish cleanly (Robocopy exit code {code}). "
            "Open the log for error details. No reliable same/different conclusion."
        )
        banner = [
            "=" * 72,
            "COMPARE RESULT: FAILED",
            detail,
            "No files were copied or deleted by Compare only.",
            "=" * 72,
        ]
        return False, headline, detail, banner

    if code == 0:
        headline = "ALL FILES ARE THE SAME"
        detail = (
            "Compare finished: for this scan, source and destination look the same. "
            "No new, newer, older, modified, or extra files were listed. "
            "Nothing needs to be copied. (Exit code 0.)"
        )
        banner = [
            "=" * 72,
            "COMPARE RESULT: ALL FILES ARE THE SAME",
            "",
            "Source and destination match for this comparison.",
            "Robocopy listed no differences (exit code 0).",
            "No files were copied or deleted.",
            "",
            "You can trust this message: if everything matched, you should see",
            "this exact line — not a short technical exit code alone.",
            "=" * 72,
        ]
        return True, headline, detail, banner

    n_xfer = len(report.transferred) if report else None
    n_extra = len(report.extras) if report else None
    n_diff = len(report.differences) if report else None
    n_err = len(report.errors) if report else None

    count_bits: list[str] = []
    if n_diff is not None:
        count_bits.append(f"{n_diff} difference line(s)")
    if n_xfer is not None:
        count_bits.append(f"{n_xfer} would-copy / different file(s)")
    if n_extra is not None:
        count_bits.append(f"{n_extra} extra on destination")
    if n_err is not None and n_err:
        count_bits.append(f"{n_err} error(s)")
    counts = (" Parsed from log: " + ", ".join(count_bits) + ".") if count_bits else ""
    parse_hint = ""
    if report is not None and not report.differences and not report.errors:
        parse_hint = (
            " Exit code alone shows differences even if the file table looks empty "
            "— open the raw log to confirm."
        )

    headline = "CHECK THE LOG — THERE ARE DIFFERENCES"
    detail = (
        "Compare finished: the two folders are NOT the same."
        f"{counts}{parse_hint} "
        "Open “See reports” or the compare_*.log file for the full list. "
        f"No files were copied or deleted. (Exit code {code}.)"
    )
    banner = [
        "=" * 72,
        "COMPARE RESULT: CHECK THE LOG — THERE ARE DIFFERENCES",
        "",
        "Source and destination are NOT identical for this comparison.",
        f"Robocopy exit code: {code} (differences and/or extras/mismatches).",
        counts.strip() or "Open the log or “See reports” for which files differ.",
        "No files were copied or deleted by Compare only.",
        "",
        "How to read this:",
        "  • ALL FILES ARE THE SAME  → only when exit code is 0 and this banner says so",
        "  • CHECK THE LOG — THERE ARE DIFFERENCES  → folders differ (this message)",
        "",
        "Tip: use See reports → filter “Differences” for a clear table.",
        "=" * 72,
    ]
    return False, headline, detail, banner


def append_compare_verdict_to_log(log_path: str, banner_lines: list[str]) -> None:
    """Append a clear same/different footer to the compare log file."""
    path = Path(log_path)
    if not path.is_file():
        return
    try:
        existing = path.read_bytes()
        enc = "utf-8"
        for candidate in ("utf-8-sig", "cp850", "cp1252", "latin-1"):
            try:
                existing.decode(candidate)
                enc = candidate
                break
            except UnicodeDecodeError:
                continue
        footer = "\r\n" + "\r\n".join(banner_lines) + "\r\n"
        with path.open("a", encoding=enc, errors="replace", newline="") as fh:
            fh.write(footer)
    except OSError:
        pass


def decode_robocopy_bytes(data: bytes) -> str:
    """Decode Robocopy log bytes (ANSI / UTF-8 / UTF-16)."""
    if not data:
        return ""
    if data.startswith(b"\xff\xfe"):
        return data.decode("utf-16-le", errors="replace")
    if data.startswith(b"\xfe\xff"):
        return data.decode("utf-16-be", errors="replace")
    if data.startswith(b"\xef\xbb\xbf"):
        return data.decode("utf-8-sig")
    sample = data[:64]
    if b"\x00" in sample and len(sample) > 3 and sample[1:2] == b"\x00":
        return data.decode("utf-16-le", errors="replace")
    for enc in ("utf-8", "cp850", "cp1252"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1", errors="replace")


def read_log_text(path: Path) -> str:
    """Read a Robocopy log trying common Windows encodings."""
    return decode_robocopy_bytes(path.read_bytes())


def last_log_lines(path: str | Path, *, limit: int = 250) -> list[str]:
    """Last non-empty lines of a Robocopy log, for the Output terminal."""
    file_path = Path(path)
    if not file_path.is_file():
        return []
    try:
        text = read_log_text(file_path)
    except OSError:
        return []
    lines = [ln.rstrip("\r") for ln in text.splitlines() if ln.strip()]
    if len(lines) > limit:
        omitted = len(lines) - limit
        return [f"(… {omitted} earlier log lines omitted …)"] + lines[-limit:]
    return lines


class LogFileTailer:
    """Read new lines from a log Robocopy still has open for write."""

    def __init__(self, path: str | Path, *, max_chunk: int = 400_000) -> None:
        self.path = Path(path)
        self.offset = 0
        self.carry = b""
        self.max_chunk = max_chunk

    def poll(self) -> list[str]:
        if not self.path.is_file():
            return []
        try:
            size = self.path.stat().st_size
            if size < self.offset:
                self.offset = 0
                self.carry = b""
            if size == self.offset:
                return []
            with self.path.open("rb") as fh:
                fh.seek(self.offset)
                chunk = fh.read()
            self.offset += len(chunk)
        except OSError:
            return []
        data = self.carry + chunk
        omitted = 0
        if len(data) > self.max_chunk:
            omitted = len(data) - self.max_chunk
            data = data[-self.max_chunk :]
            nl = data.find(b"\n")
            if nl >= 0:
                data = data[nl + 1 :]
        if data.endswith(b"\n") or data.endswith(b"\r"):
            complete, self.carry = data, b""
        else:
            nl = max(data.rfind(b"\n"), data.rfind(b"\r"))
            if nl < 0:
                self.carry = data
                lines: list[str] = []
                if omitted:
                    lines.append(f"(… {omitted} bytes of earlier log omitted …)")
                return lines
            complete, self.carry = data[: nl + 1], data[nl + 1 :]
        text = decode_robocopy_bytes(complete).replace("\r\n", "\n").replace("\r", "\n")
        lines = [ln for ln in text.split("\n") if ln.strip()]
        if omitted:
            lines.insert(0, f"(… {omitted} bytes of earlier log omitted …)")
        return lines


def format_copy_result(
    code: int,
    meaning: str,
    report: Optional[RobocopyReport] = None,
    *,
    log_path: Optional[str] = None,
) -> tuple[str, list[str]]:
    """Headline for the Output banner, plus lines to append to the live log."""
    bits: list[str] = []
    if report is not None:
        bits.append(f"{len(report.transferred)} copied")
        bits.append(f"{len(report.skipped)} skipped")
        if report.extras:
            bits.append(f"{len(report.extras)} extra on dest")
        if report.errors:
            bits.append(f"{len(report.errors)} error(s)")
        xfer_bytes = sum_entry_bytes(report.transferred)
        if xfer_bytes:
            bits.append(format_bytes(xfer_bytes))
    counts = " · ".join(bits) if bits else meaning
    if code > ROBOCOPY_SUCCESS_MAX:
        headline = f"Failed (exit {code}) — {meaning}"
    else:
        headline = f"Done (exit {code}) — {counts}"

    lines = [
        "",
        "=" * 60,
        f"RESULT: {meaning}",
        f"Exit code: {code}",
    ]
    if bits:
        lines.append("This run: " + ", ".join(bits) + ".")
    if report and report.summary_block:
        lines.append("")
        lines.extend(report.summary_block)
    if log_path:
        lines.append("")
        lines.append(f"Log saved to: {log_path}")
        lines.append("Open See reports for the file table.")
    lines.append("=" * 60)
    return headline, lines


def _classify_status(status: str) -> tuple[str, str]:
    cleaned = status.strip().lstrip("*").strip()
    if not cleaned:
        return "other", "Other"
    for pattern, kind, label in _ACTION_RULES:
        if pattern.search(cleaned):
            return kind, label
    if re.search(r"ERROR|0x[0-9A-Fa-f]{8}", status, re.I):
        return "error", "Error"
    return "other", cleaned[:40] or "Other"


def _parse_file_line(line: str) -> Optional[LogFileEntry]:
    """Parse one Robocopy file-status line into action / size / path."""
    if not line or not line.strip():
        return None
    stripped = line.strip()
    if stripped.startswith("---") or stripped.startswith("==="):
        return None
    if re.match(r"^(ROBOCOPY|Total|Dirs|Files|Bytes|Times|Vitesse|Speed)\b", stripped, re.I):
        return None

    m = _PATH_START.search(line)
    if not m:
        if re.search(r"ERROR|0x[0-9A-Fa-f]{8}", line, re.I):
            return LogFileEntry(
                kind="error",
                label="Error",
                size="",
                path=stripped[:500],
                raw_status="ERROR",
            )
        return None

    path = line[m.start() :].strip()
    left = line[: m.start()].rstrip()
    size = ""
    sm = _SIZE_TAIL.search(left)
    if sm:
        size = sm.group("size").strip()
        status = left[: sm.start()].strip()
    else:
        status = left.strip()

    if not status and not size:
        return None

    kind, label = _classify_status(status)
    if kind == "other" and not status:
        return None
    return LogFileEntry(
        kind=kind,
        label=label,
        size=size,
        path=path,
        raw_status=status.strip(),
    )


def parse_robocopy_log(log_path: str | Path) -> RobocopyReport:
    """Parse a Robocopy /LOG file into a structured transfer report."""
    path = Path(log_path)
    report = RobocopyReport(log_path=str(path))
    if not path.is_file():
        report.parse_notes.append("Log file not found.")
        return report

    try:
        text = read_log_text(path)
    except OSError as exc:
        report.parse_notes.append(f"Could not read log: {exc}")
        return report

    lines = text.splitlines()
    in_summary = False
    for line in lines:
        if not report.source:
            m = _HEADER_SOURCE.match(line)
            if m:
                report.source = m.group(1).strip()
                continue
        if not report.destination:
            m = _HEADER_DEST.match(line)
            if m:
                report.destination = m.group(1).strip()
                continue
        if not report.options:
            m = _HEADER_OPTIONS.match(line)
            if m:
                report.options = m.group(1).strip()
                continue
        if not report.started:
            m = _HEADER_STARTED.match(line)
            if m and ":" in line:
                report.started = m.group(1).strip()
                continue
        m_end = _HEADER_ENDED.match(line)
        if m_end:
            report.ended = m_end.group(1).strip()
            continue

        if re.search(r"\bTotal\b", line) and re.search(
            r"Copi|Copied|Ignor|Skipped|Extras|FAILED|ECHEC|ÉCHEC", line, re.I
        ):
            in_summary = True
            report.summary_block.append(line.rstrip())
            continue
        if in_summary:
            if not line.strip():
                if len(report.summary_block) >= 2:
                    in_summary = False
                continue
            if line.strip().startswith("---"):
                if len(report.summary_block) >= 2:
                    in_summary = False
                continue
            report.summary_block.append(line.rstrip())
            if len(report.summary_block) > 12:
                in_summary = False
            continue

        entry = _parse_file_line(line)
        if entry:
            report.entries.append(entry)

    has_xo = bool(re.search(r"(?:^|\s)/XO(?:\s|$)", report.options or "", re.I))
    if not has_xo:
        for e in report.entries:
            if e.kind == "skipped" and re.search(
                r"older|plus\s+ancien", e.raw_status, re.I
            ):
                e.kind = "transferred"
                e.label = "Older"
    else:
        for e in report.entries:
            if e.kind == "skipped" and re.search(
                r"older|plus\s+ancien", e.raw_status, re.I
            ):
                e.label = "Older (skipped)"

    if not report.ended and not report.summary_block:
        report.incomplete = True
        report.parse_notes.append(
            "No end/summary found — job may still be running or the log was truncated."
        )
    if (
        not report.transferred
        and not report.extras
        and not report.errors
        and not report.differences
    ):
        report.parse_notes.append(
            "No file actions parsed. If this is an old/unusual log format, open the raw file."
        )
    return report


def list_available_logs(
    opts: SyncOptions,
    extra: Optional[str] = None,
    limit: int = 40,
    *,
    session_paths: Optional[list[str]] = None,
) -> list[Path]:
    """Newest-first log files.

    When session_paths is set, only those files (this app session). Otherwise
    scan the log folder — used for disk cleanup / open-folder, not the picker.
    """
    found: dict[str, Path] = {}

    def add_file(raw: str | Path) -> None:
        try:
            path = Path(raw)
            if path.is_file() and path.suffix.lower() == ".log":
                found[str(path.resolve())] = path
        except OSError:
            pass

    if session_paths is not None:
        for raw in session_paths:
            add_file(raw)
        if extra:
            add_file(extra)
        logs = sorted(found.values(), key=lambda p: p.stat().st_mtime, reverse=True)
        return logs[:limit]

    dirs: set[Path] = {LOGS_DIR}
    try:
        dirs.add(log_directory_from_opts(opts))
    except Exception:
        pass
    if opts.log_path:
        p = Path(opts.log_path)
        if p.suffix.lower() == ".log" and p.parent:
            dirs.add(p.parent)
        if p.is_file():
            dirs.add(p.parent)

    for d in dirs:
        try:
            if not d.is_dir():
                continue
            for f in d.glob("*.log"):
                if f.is_file():
                    found[str(f.resolve())] = f
        except OSError:
            continue

    for candidate in (opts.log_path, extra):
        if candidate:
            add_file(candidate)

    logs = sorted(found.values(), key=lambda p: p.stat().st_mtime, reverse=True)
    return logs[:limit]


def format_report_transfer_list(report: RobocopyReport, *, max_lines: int = 5000) -> str:
    """Plain-text list of transferred files or differences (for clipboard)."""
    compare = is_compare_log(report)
    title = "Folder compare report" if compare else "Robocopy report"
    would = "Would copy / different" if compare else "Transferred"
    lines = [
        f"{title} — {report.log_path}",
        f"Source: {report.source}",
        f"Dest:   {report.destination}",
        f"Started: {report.started or '—'}",
        f"Ended:   {report.ended or ('(incomplete)' if report.incomplete else '—')}",
        f"{would}: {len(report.transferred)}",
        f"Extras on dest: {len(report.extras)}",
        f"Errors: {len(report.errors)}",
    ]
    if compare:
        lines.append(f"All differences: {len(report.differences)}")
    lines.append("")
    lines.append(f"=== {would} ===")
    for e in report.transferred[:max_lines]:
        lines.append(f"{e.label} | {e.size or '—'} | {e.path}")
    if len(report.transferred) > max_lines:
        lines.append(f"... and {len(report.transferred) - max_lines} more")
    if report.extras:
        lines.append("")
        lines.append("=== Extra on destination (not in source) ===")
        for e in report.extras[:max_lines]:
            lines.append(f"{e.label} | {e.size or '—'} | {e.path}")
    older = [
        e
        for e in report.skipped
        if re.search(r"older|plus\s+ancien", e.raw_status, re.I)
    ]
    if compare and older:
        lines.append("")
        lines.append("=== Older on destination (source would overwrite without /XO) ===")
        for e in older[:max_lines]:
            lines.append(f"{e.label} | {e.size or '—'} | {e.path}")
    if report.errors:
        lines.append("")
        lines.append("=== Errors ===")
        for e in report.errors[:max_lines]:
            lines.append(f"{e.path}")
    return "\n".join(lines)
