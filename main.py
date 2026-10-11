"""
Explorer Advance — Robocopy

NiceGUI front-end for bulk folder update / compare with the built-in
Windows `robocopy` command. Copy and compare stay behind a command
preview; Mirror and drive roots need an extra acknowledgement.
"""

from __future__ import annotations

import argparse
import asyncio
import html
import json
import os
import socket
import sys
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from nicegui import app, ui

import robocopy_docs
import robocopy_ops

APP_DIR = Path(__file__).resolve().parent
CONFIG_PATH = APP_DIR / "user_config.json"
APP_HOST = "127.0.0.1"
APP_PORT = 8765
APP_TITLE = "Explorer Advance — Robocopy"

# Logs written in this app session only (See reports ignores older files).
_last_run_log: Optional[str] = None
_session_logs: list[str] = []


def load_config() -> robocopy_ops.SyncOptions:
    if not CONFIG_PATH.is_file():
        cfg = robocopy_ops.SyncOptions()
        return robocopy_ops.ensure_pairs(cfg, pad_to_default=True)
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        known = {f.name for f in robocopy_ops.SyncOptions.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        pairs_raw = data.get("pairs")
        lists_raw = data.get("saved_lists")
        filtered = {
            k: v
            for k, v in data.items()
            if k in known and k not in {"pairs", "saved_lists"}
        }
        cfg = robocopy_ops.SyncOptions(**filtered)
        if not cfg.log_path:
            cfg.log_path = robocopy_ops.DEFAULT_LOG
        had_pairs = isinstance(pairs_raw, list)
        cfg.pairs = robocopy_ops.coerce_pairs(
            pairs_raw if had_pairs else None,
            source=cfg.source,
            destination=cfg.destination,
            pad_to_default=not had_pairs,
        )
        cfg.saved_lists = robocopy_ops.coerce_saved_lists(lists_raw)
        if cfg.active_list and robocopy_ops.find_saved_list(cfg, cfg.active_list) is None:
            cfg.active_list = ""
        robocopy_ops.sync_legacy_paths(cfg)
        cfg.log_path = robocopy_ops.folder_for_new_session_logs(cfg.log_path)
        return cfg
    except Exception:
        cfg = robocopy_ops.SyncOptions()
        return robocopy_ops.ensure_pairs(cfg, pad_to_default=True)


def save_config(cfg: robocopy_ops.SyncOptions) -> None:
    try:
        robocopy_ops.sync_legacy_paths(cfg)
        cfg.pairs = robocopy_ops.trim_pairs_for_save(cfg.pairs)
        robocopy_ops.attach_active_list_snapshot(cfg)
        CONFIG_PATH.write_text(
            json.dumps(asdict(cfg), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception:
        pass


def save_settings_clicked() -> None:
    save_config(opts)
    render_folder_pairs()
    refresh_list_select()
    if opts.active_list.strip():
        ui.notify(
            f"Saved settings and project “{opts.active_list}” (folders and options).",
            type="positive",
            timeout=6000,
        )
    else:
        ui.notify("Saved settings.", type="positive")


opts = load_config()
if not opts.log_path:
    opts.log_path = str(robocopy_ops.LOGS_DIR)
else:
    opts.log_path = robocopy_ops.folder_for_new_session_logs(opts.log_path)
robocopy_ops.ensure_pairs(opts)

output_view: ui.html
cmd_preview: ui.code
run_btn: ui.button
status_label: ui.label
progress: ui.linear_progress
output_card: ui.card
output_spinner: ui.spinner
output_badge: ui.badge
output_summary: ui.label
run_spinner: ui.spinner
run_clock: ui.timer
_running = False
_cancel_queue = False
_active_process: Optional[asyncio.subprocess.Process] = None
_run_started_at: Optional[datetime] = None
_run_status_base: str = "Ready. Preview the command, then confirm."
MAX_OUTPUT_LINES = 4000
OUTPUT_IDLE = (
    "Ready.\n"
    "\n"
    "Click Run Robocopy. This panel is the live terminal:\n"
    "  • the exact command\n"
    "  • Robocopy’s own lines as they are written\n"
    "  • a RESULT block when the job ends\n"
    "\n"
    "See reports opens a parsed table of the same log file."
)
_output_lines: list[str] = OUTPUT_IDLE.split("\n")
_output_last: str = ""

pairs_host: ui.column
pair_widgets: list[dict[str, Any]] = []
w_list_select: ui.select
_ignore_list_select = False
w_log: ui.input
w_copy_subdirs: ui.checkbox
w_exclude_older: ui.checkbox
w_fft: ui.checkbox
w_dry: ui.checkbox
w_compare: ui.checkbox
w_mirror: ui.checkbox
w_tee: ui.checkbox
w_write_log: ui.checkbox
w_retries: ui.number
w_wait: ui.number
w_mt: ui.number
w_purge: ui.checkbox
w_skip_junctions: ui.checkbox
w_copy_security: ui.checkbox
w_dst_adjust: ui.checkbox
w_unbuffered: ui.checkbox
w_restartable: ui.checkbox
w_exclude_dirs: ui.input
w_exclude_files: ui.input
w_max_age: ui.number
w_copy_all: ui.checkbox
preset_buttons: dict[str, ui.button] = {}


def _path_from_event(e: Any) -> str:
    args = getattr(e, "args", e)
    chunks = args if isinstance(args, list) else [args]
    for item in chunks:
        if isinstance(item, str):
            text = item.strip()
            if len(text) >= 2 and (text[1] == ":" or text.startswith("\\\\")):
                return text
        elif isinstance(item, dict):
            text = str(item.get("path") or "").strip()
            if text:
                return text
    return ""


def _safe_open_explorer(path: str) -> None:
    path = (path or "").strip()
    if not path:
        ui.notify("Could not read that row’s path.", type="warning")
        return
    try:
        robocopy_ops.open_in_explorer(path)
        ui.notify(f"Opened in Explorer:\n{path}", type="positive")
    except Exception as exc:
        ui.notify(f"Could not open Explorer: {exc}", type="negative")


def refresh_preview() -> None:
    try:
        cmd_preview.set_content(robocopy_ops.preview_all_commands(opts))
    except Exception as exc:
        cmd_preview.set_content(f"(preview error: {exc})")


def apply_preset(name: str) -> None:
    """Common workflows."""
    if name == "update":
        opts.copy_subdirs = True
        opts.exclude_older = True
        opts.fat_file_times = True
        opts.mirror = False
        opts.purge = False
        opts.dry_run = False
        opts.compare_only = False
        opts.retries = 1
        opts.wait_seconds = 1
        opts.multi_thread = 8
        ui.notify("Preset: Update newer files only (Explorer-like sync, faster)", type="positive")
    elif name == "full_copy":
        opts.copy_subdirs = True
        opts.exclude_older = False
        opts.fat_file_times = True
        opts.mirror = False
        opts.purge = False
        opts.dry_run = False
        opts.compare_only = False
        opts.multi_thread = 8
        ui.notify("Preset: Full copy (overwrite if different size/time rules apply)", type="info")
    elif name == "mirror":
        opts.copy_subdirs = True
        opts.exclude_older = False
        opts.mirror = True
        opts.purge = False
        opts.dry_run = True
        opts.compare_only = False
        opts.multi_thread = 8
        ui.notify(
            "Preset: Mirror — dry-run ON. Review output, then uncheck Dry run.",
            type="warning",
            timeout=6000,
        )
    elif name == "compare":
        opts.copy_subdirs = True
        opts.exclude_older = False
        opts.fat_file_times = True
        opts.mirror = False
        opts.purge = False
        opts.dry_run = True
        opts.compare_only = True
        opts.write_log = True
        opts.tee_console = True
        opts.retries = 1
        opts.wait_seconds = 1
        opts.multi_thread = 8
        ui.notify(
            "Preset: Compare only — no copy. Differences go to a compare_*.log "
            "(then open See reports).",
            type="info",
            timeout=8000,
        )
    elif name == "dry":
        opts.dry_run = True
        opts.compare_only = False
        ui.notify("Dry run enabled (/L) — no files will be copied", type="info")
    sync_widgets_from_opts()
    refresh_preview()
    update_run_button_label()
    refresh_preset_buttons()


def _elapsed_label() -> str:
    if _run_started_at is None:
        return "0s"
    secs = max(0, int((datetime.now() - _run_started_at).total_seconds()))
    hours, rem = divmod(secs, 3600)
    minutes, seconds = divmod(rem, 60)
    if hours:
        return f"{hours}h {minutes:02d}m {seconds:02d}s"
    if minutes:
        return f"{minutes}m {seconds:02d}s"
    return f"{seconds}s"


def set_run_status(text: str) -> None:
    global _run_status_base
    _run_status_base = text
    refresh_run_status()


def refresh_run_status() -> None:
    try:
        if status_label.is_deleted:
            return
    except Exception:
        return
    if _running:
        status_label.set_text(f"{_run_status_base}  ·  {_elapsed_label()}")
    else:
        status_label.set_text(_run_status_base)


def tick_run_clock() -> None:
    if _running:
        refresh_run_status()


def show_running_chrome(active: bool) -> None:
    """Spinner, bar, and Output highlight while robocopy.exe is working."""
    try:
        run_spinner.visible = active
        output_spinner.visible = active
        output_badge.visible = active
        progress.visible = active
        if active:
            progress.props("indeterminate color=primary")
            progress.value = None
            output_card.style("outline: 2px solid #1565c0; outline-offset: 2px;")
            status_label.classes(replace="text-body2 text-primary font-medium")
        else:
            progress.props(remove="indeterminate")
            progress.value = 0
            progress.visible = False
            output_card.style("outline: none;")
            status_label.classes(replace="text-caption text-grey-8")
        try:
            run_clock.active = active
        except Exception:
            pass
    except Exception:
        pass


async def focus_output_panel() -> None:
    """Scroll the page down to Output with a smooth transition."""
    card_id = getattr(output_card, "html_id", "") or ""
    script = f"""
    (function() {{
        function findEl() {{
            var el = document.getElementById('robocopy-output')
                || document.querySelector('.robocopy-output-card');
            if (!el && typeof getHtmlElement === 'function') {{
                try {{ el = getHtmlElement("{card_id}"); }} catch (e) {{ el = null; }}
            }}
            return el || null;
        }}
        function inView(el) {{
            var r = el.getBoundingClientRect();
            return r.top >= 0 && r.top < (window.innerHeight * 0.4);
        }}
        function scrollParents(el) {{
            var nodes = [document.scrollingElement, document.documentElement, document.body];
            var p = el.parentElement;
            while (p) {{
                var st = window.getComputedStyle(p);
                if (/(auto|scroll|overlay)/.test(st.overflowY) && p.scrollHeight > p.clientHeight + 8) {{
                    nodes.push(p);
                }}
                p = p.parentElement;
            }}
            document.querySelectorAll('.q-page, .nicegui-content, .q-page-container').forEach(
                function(n) {{ nodes.push(n); }}
            );
            return nodes;
        }}
        function go() {{
            var el = findEl();
            if (!el) return;
            try {{
                el.scrollIntoView({{ behavior: 'smooth', block: 'start', inline: 'nearest' }});
            }} catch (e) {{
                el.scrollIntoView(true);
            }}
            var rect = el.getBoundingClientRect();
            var pageY = rect.top + (window.pageYOffset || document.documentElement.scrollTop || 0) - 8;
            try {{
                window.scrollTo({{ top: Math.max(0, pageY), behavior: 'smooth' }});
            }} catch (e) {{}}
            scrollParents(el).forEach(function(node) {{
                if (!node) return;
                try {{
                    var top = el.getBoundingClientRect().top - node.getBoundingClientRect().top
                        + (node.scrollTop || 0) - 8;
                    if (node === window || node === document || node === document.scrollingElement) {{
                        window.scrollTo({{ top: Math.max(0, pageY), behavior: 'smooth' }});
                    }} else if (typeof node.scrollTo === 'function') {{
                        node.scrollTo({{ top: Math.max(0, top), behavior: 'smooth' }});
                    }}
                }} catch (e) {{}}
            }});
        }}
        go();
        setTimeout(function() {{
            var el = findEl();
            if (el && !inView(el)) go();
        }}, 450);
    }})();
    """
    try:
        await asyncio.wait_for(ui.run_javascript(script), timeout=1.0)
    except Exception:
        pass


def _terminal_html() -> str:
    text = "\n".join(_output_lines) if _output_lines else " "
    return '<pre class="robocopy-terminal-pre">' + html.escape(text) + "</pre>"


def _flush_output() -> None:
    try:
        output_view.set_content(_terminal_html())
    except Exception:
        pass


async def scroll_output() -> None:
    try:
        await asyncio.wait_for(
            ui.run_javascript(
                f"""
                (function() {{
                    var root = getHtmlElement("{output_view.html_id}");
                    if (!root) return;
                    root.scrollTop = root.scrollHeight;
                }})();
                """
            ),
            timeout=0.4,
        )
    except Exception:
        pass


def clear_output(*, idle: bool = False) -> None:
    global _output_lines, _output_last
    _output_last = ""
    if idle:
        _output_lines = OUTPUT_IDLE.split("\n")
        set_output_summary(
            "Run a job to fill this terminal. See reports is a parsed table of the log.",
            tone="info",
        )
    else:
        _output_lines = []
    _flush_output()


def push_output(line: str = "", *, tone: str = "", stamp: bool = False, flush: bool = True) -> None:
    """Append a line to the Output terminal (visible like a console)."""
    global _output_last, _output_lines
    text = "" if line is None else str(line)
    if stamp and text.strip():
        text = f"{datetime.now().strftime('%H:%M:%S')}  {text}"
    if text == _output_last and text.strip():
        return
    _output_last = text
    _output_lines.append(text)
    if len(_output_lines) > MAX_OUTPUT_LINES:
        extra = len(_output_lines) - MAX_OUTPUT_LINES
        _output_lines = _output_lines[-MAX_OUTPUT_LINES:]
        _output_lines[0] = f"(… {extra} earlier lines omitted …)"
    if flush:
        _flush_output()


def append_log_file_to_output(run_log: Optional[str]) -> None:
    """Copy the Robocopy log into the terminal so a short job still leaves a trace."""
    if not run_log:
        return
    dumped = robocopy_ops.last_log_lines(run_log, limit=400)
    if not dumped:
        return
    existing = {ln for ln in _output_lines if ln.strip()}
    new = [ln for ln in dumped if ln.strip() and ln not in existing]
    if not new:
        return
    push_output("— from log file —", stamp=True)
    for ln in new:
        push_output(ln, flush=False)
    _flush_output()


def notify_user(
    message: str,
    *,
    kind: str = "info",
    linger: bool = False,
    seconds: float = 8,
    position: str = "top",
) -> None:
    """One readable toast. Quasar timeout is milliseconds; linger stays until OK."""
    if linger:
        ui.notify(
            message,
            type=kind,
            multi_line=True,
            position=position,
            close_button="OK",
            timeout=0,
        )
        return
    ui.notify(
        message,
        type=kind,
        multi_line=True,
        position=position,
        timeout=int(max(1.0, seconds) * 1000),
    )


def set_output_summary(text: str, *, tone: str = "info") -> None:
    """Readable result strip above the dark log — survives empty live stdout."""
    colors = {
        "ok": "text-body1 text-weight-medium text-positive",
        "warn": "text-body1 text-weight-medium text-orange-9",
        "err": "text-body1 text-weight-medium text-negative",
        "run": "text-body1 text-weight-medium text-primary",
        "info": "text-body1 text-weight-medium text-grey-9",
    }
    try:
        output_summary.set_text(text)
        output_summary.visible = True
        output_summary.classes(replace=colors.get(tone, colors["info"]))
    except Exception:
        pass


def matching_preset() -> str:
    """Which preset button matches the current options."""
    if opts.compare_only:
        return "compare"
    if opts.mirror:
        return "mirror"
    if opts.dry_run:
        return "dry"
    if opts.exclude_older:
        return "update"
    return "full_copy"


def refresh_preset_buttons() -> None:
    """Fill the preset that matches Options; outline the rest."""
    if not preset_buttons:
        return
    active = matching_preset()
    colors = {
        "update": "primary",
        "full_copy": "primary",
        "mirror": "orange",
        "compare": "secondary",
        "dry": "primary",
    }
    for name, btn in preset_buttons.items():
        try:
            if btn.is_deleted:
                continue
        except Exception:
            continue
        color = colors.get(name, "primary")
        btn.props(remove="unelevated outline flat")
        if name == active:
            btn.props(f"unelevated color={color}")
        else:
            btn.props(f"outline color={color}")


def update_run_button_label() -> None:
    try:
        if opts.compare_only:
            run_btn.props("icon=difference color=secondary")
            run_btn.set_text("Compare folders")
        else:
            run_btn.props("icon=play_arrow color=primary")
            run_btn.set_text("Run Robocopy")
    except Exception:
        pass


def sync_widgets_from_opts() -> None:
    if len(pair_widgets) != len(opts.pairs):
        render_folder_pairs()
    else:
        for i, widgets in enumerate(pair_widgets):
            if i >= len(opts.pairs):
                break
            widgets["source"].value = opts.pairs[i].source
            widgets["dest"].value = opts.pairs[i].destination
    refresh_list_select()
    w_log.value = opts.log_path
    w_copy_subdirs.value = opts.copy_subdirs
    w_exclude_older.value = opts.exclude_older
    w_fft.value = opts.fat_file_times
    w_dry.value = opts.dry_run
    w_compare.value = opts.compare_only
    w_mirror.value = opts.mirror
    w_tee.value = opts.tee_console
    w_write_log.value = opts.write_log
    w_retries.value = opts.retries
    w_wait.value = opts.wait_seconds
    w_mt.value = opts.multi_thread
    w_purge.value = opts.purge
    w_skip_junctions.value = opts.skip_junctions
    w_copy_security.value = opts.copy_security
    w_dst_adjust.value = opts.dst_adjust
    w_unbuffered.value = opts.unbuffered
    w_restartable.value = opts.restartable
    w_exclude_dirs.value = opts.exclude_dirs
    w_exclude_files.value = opts.exclude_files
    w_max_age.value = opts.max_age_days
    w_copy_all.value = opts.copy_all
    refresh_copy_security_enabled()
    refresh_preset_buttons()


def bind_str(attr: str):
    def _on(e) -> None:
        setattr(opts, attr, e.value or "")
        refresh_preview()

    return _on


def bind_bool(attr: str):
    def _on(e) -> None:
        value = bool(e.value)
        setattr(opts, attr, value)
        if attr == "compare_only" and value:
            opts.dry_run = True
            opts.mirror = False
            opts.purge = False
            opts.write_log = True
            opts.exclude_older = False
            try:
                w_dry.value = True
                w_mirror.value = False
                w_purge.value = False
                w_write_log.value = True
                w_exclude_older.value = False
            except Exception:
                pass
        elif attr == "dry_run" and not value:
            opts.compare_only = False
            try:
                w_compare.value = False
            except Exception:
                pass
        elif attr == "mirror" and value:
            opts.compare_only = False
            try:
                w_compare.value = False
            except Exception:
                pass
        elif attr == "copy_all":
            refresh_copy_security_enabled()
        refresh_preview()
        update_run_button_label()
        refresh_preset_buttons()

    return _on


def refresh_copy_security_enabled() -> None:
    """ /COPYALL already includes ACLs, so /SEC is redundant while it is on. """
    try:
        if opts.copy_all:
            w_copy_security.disable()
        else:
            w_copy_security.enable()
    except Exception:
        pass


def bind_int(attr: str):
    def _on(e) -> None:
        try:
            setattr(opts, attr, int(e.value if e.value is not None else 0))
        except (TypeError, ValueError):
            setattr(opts, attr, 0)
        refresh_preview()

    return _on


def _set_pair_source(index: int, value: str) -> None:
    if 0 <= index < len(opts.pairs):
        opts.pairs[index].source = value or ""
        robocopy_ops.sync_legacy_paths(opts)
        refresh_preview()


def _set_pair_dest(index: int, value: str) -> None:
    if 0 <= index < len(opts.pairs):
        opts.pairs[index].destination = value or ""
        robocopy_ops.sync_legacy_paths(opts)
        refresh_preview()


def browse_pair_source(index: int) -> None:
    if index < 0 or index >= len(opts.pairs):
        return
    start = opts.pairs[index].source or str(Path.home())
    picked = robocopy_ops.pick_folder(start, title=f"Select source folder ({index + 1})")
    if picked:
        _set_pair_source(index, picked)
        if index < len(pair_widgets):
            pair_widgets[index]["source"].value = picked


def browse_pair_dest(index: int) -> None:
    if index < 0 or index >= len(opts.pairs):
        return
    start = opts.pairs[index].destination or str(Path.home())
    picked = robocopy_ops.pick_folder(
        start, title=f"Select destination folder ({index + 1})"
    )
    if picked:
        _set_pair_dest(index, picked)
        if index < len(pair_widgets):
            pair_widgets[index]["dest"].value = picked


def add_folder_pair() -> None:
    opts.pairs.append(robocopy_ops.FolderPair())
    render_folder_pairs()
    refresh_preview()


def remove_folder_pair(index: int) -> None:
    if len(opts.pairs) <= 1:
        ui.notify("Keep at least one folder pair.", type="info")
        return
    if 0 <= index < len(opts.pairs):
        opts.pairs.pop(index)
        robocopy_ops.sync_legacy_paths(opts)
        render_folder_pairs()
        refresh_preview()


def render_folder_pairs() -> None:
    """Draw source → destination rows. Three by default; + adds more."""
    global pair_widgets
    pair_widgets = []
    try:
        if pairs_host.is_deleted:
            return
    except Exception:
        return
    pairs_host.clear()
    with pairs_host:
        with ui.element("div").classes("w-full").style(
            "display: grid; "
            "grid-template-columns: 1.6rem minmax(12rem, 1fr) 2.2rem 1.6rem "
            "minmax(12rem, 1fr) 2.2rem 2.2rem; "
            "column-gap: 0.35rem; row-gap: 0.45rem; align-items: end;"
        ):
            ui.label("").classes("text-caption")
            ui.label("Source").classes("text-caption text-grey-8")
            ui.label("")
            ui.label("")
            ui.label("Destination").classes("text-caption text-grey-8")
            ui.label("")
            ui.label("")
            for index, pair in enumerate(opts.pairs):
                ui.label(str(index + 1)).classes("text-caption text-grey-6 pb-2")
                src = (
                    ui.input(value=pair.source, placeholder=r"C:\Path\Source")
                    .classes("w-full")
                    .props(f'dense clearable aria-label="Source folder {index + 1}"')
                    .on(
                        "update:model-value",
                        lambda e, i=index: _set_pair_source(i, e.value or ""),
                    )
                )
                ui.button(
                    icon="folder_open",
                    on_click=lambda _e, i=index: browse_pair_source(i),
                ).props("flat round dense").tooltip("Browse source…")
                ui.icon("arrow_forward").classes("text-grey-6 pb-2")
                dst = (
                    ui.input(value=pair.destination, placeholder=r"D:\Path\Destination")
                    .classes("w-full")
                    .props(
                        f'dense clearable aria-label="Destination folder {index + 1}"'
                    )
                    .on(
                        "update:model-value",
                        lambda e, i=index: _set_pair_dest(i, e.value or ""),
                    )
                )
                ui.button(
                    icon="folder_open",
                    on_click=lambda _e, i=index: browse_pair_dest(i),
                ).props("flat round dense").tooltip("Browse destination…")
                remove = (
                    ui.button(
                        icon="close",
                        on_click=lambda _e, i=index: remove_folder_pair(i),
                    )
                    .props("flat round dense")
                    .tooltip("Remove this pair")
                )
                if len(opts.pairs) <= 1:
                    remove.disable()
                pair_widgets.append({"source": src, "dest": dst})
        with ui.row().classes("w-full items-center gap-2 mt-1"):
            ui.button(icon="add", on_click=add_folder_pair).props(
                "round unelevated color=primary"
            ).tooltip("Add another source → destination pair")
            ui.label("Add another pair").classes("text-caption text-grey-7")


def saved_list_options() -> dict[str, str]:
    options = {"": "(unnamed project)"}
    for item in opts.saved_lists:
        name = item.name.strip()
        if name:
            options[name] = name
    return options


def refresh_list_select() -> None:
    global _ignore_list_select
    try:
        if w_list_select.is_deleted:
            return
    except Exception:
        return
    current = opts.active_list.strip()
    if current and robocopy_ops.find_saved_list(opts, current) is None:
        current = ""
        opts.active_list = ""
    _ignore_list_select = True
    try:
        w_list_select.set_options(saved_list_options(), value=current)
    except Exception:
        w_list_select.options = saved_list_options()
        w_list_select.value = current
    finally:
        _ignore_list_select = False


def on_saved_list_selected(e) -> None:
    if _ignore_list_select:
        return
    name = (getattr(e, "value", None) or "").strip()
    if not name:
        opts.active_list = ""
        return
    item = robocopy_ops.find_saved_list(opts, name)
    had_options = bool(item and item.options)
    err = robocopy_ops.apply_saved_list(opts, name)
    if err:
        ui.notify(err, type="warning")
        return
    sync_widgets_from_opts()
    render_folder_pairs()
    refresh_preview()
    update_run_button_label()
    if had_options:
        ui.notify(
            f"Loaded project “{name}” — folders and options restored.",
            type="positive",
            timeout=6000,
        )
    else:
        ui.notify(
            f"Loaded folders for “{name}”. No saved options — using Update newer only. "
            "Save project to keep options with it.",
            type="info",
            timeout=8000,
        )


def save_active_folder_list() -> None:
    if opts.active_list.strip() and robocopy_ops.find_saved_list(opts, opts.active_list):
        err = robocopy_ops.upsert_saved_list(opts, opts.active_list, opts.pairs)
        if err:
            ui.notify(err, type="warning", multi_line=True)
            return
        save_config(opts)
        refresh_list_select()
        ui.notify(
            f"Updated project “{opts.active_list}” with folders and options.",
            type="positive",
            timeout=6000,
        )
        return
    show_save_list_dialog()


def show_save_list_dialog() -> None:
    if not robocopy_ops.filled_pairs(opts):
        ui.notify(
            "Fill at least one source and destination before saving a project.",
            type="warning",
        )
        return

    with ui.dialog() as dialog, ui.card().classes("w-[28rem] max-w-[96vw]"):
        ui.label("Save project").classes("text-subtitle1 font-bold")
        ui.label(
            "Give this set of folders and Options a name, for example My games. "
            "Load restores both."
        ).classes("text-body2")
        w_name = ui.input(
            "Project name",
            value=opts.active_list or "",
            placeholder="My games",
        ).classes("w-full").props("autofocus")

        overwrite_hint = ui.label("").classes("text-caption text-orange-9")

        def update_hint() -> None:
            typed = (w_name.value or "").strip()
            if typed and robocopy_ops.find_saved_list(opts, typed):
                overwrite_hint.set_text(
                    f"A project named “{typed}” already exists. Save will replace its folders and options."
                )
            else:
                overwrite_hint.set_text("")

        w_name.on("update:model-value", lambda _e: update_hint())
        update_hint()

        def confirm() -> None:
            name = (w_name.value or "").strip()
            err = robocopy_ops.upsert_saved_list(opts, name, opts.pairs)
            if err:
                ui.notify(err, type="warning")
                return
            save_config(opts)
            refresh_list_select()
            ui.notify(
                f"Saved project “{opts.active_list}” with folders and options.",
                type="positive",
                timeout=6000,
            )
            dialog.close()

        with ui.row().classes("w-full justify-end gap-2"):
            ui.button("Cancel", on_click=dialog.close).props("flat")
            ui.button("Save project", icon="bookmark", on_click=confirm).props(
                "unelevated"
            )

    dialog.open()


def show_delete_list_dialog() -> None:
    name = opts.active_list.strip()
    if not name or robocopy_ops.find_saved_list(opts, name) is None:
        ui.notify("Select a saved project first.", type="info")
        return

    with ui.dialog() as dialog, ui.card().classes("w-[28rem] max-w-[96vw]"):
        ui.label("Delete project").classes("text-subtitle1 font-bold")
        ui.label(
            f"Remove “{name}” from saved projects? The folders on screen stay as they are."
        ).classes("text-body2")

        def confirm() -> None:
            err = robocopy_ops.delete_saved_list(opts, name)
            if err:
                ui.notify(err, type="warning")
                return
            save_config(opts)
            refresh_list_select()
            ui.notify(f"Deleted “{name}”.", type="positive")
            dialog.close()

        with ui.row().classes("w-full justify-end gap-2"):
            ui.button("Cancel", on_click=dialog.close).props("flat")
            ui.button("Delete", icon="delete", on_click=confirm).props(
                "unelevated color=negative"
            )

    dialog.open()


def apply_new_project() -> None:
    """Empty rows + Update newer only. Saved projects stay on disk."""
    robocopy_ops.reset_working_project(opts)
    save_config(opts)
    sync_widgets_from_opts()
    render_folder_pairs()
    refresh_preview()
    update_run_button_label()
    ui.notify(
        "New project — Update newer only. Saved projects are unchanged.",
        type="positive",
        timeout=6000,
    )


def show_new_project_dialog() -> None:
    dirty = bool(opts.active_list.strip()) or bool(robocopy_ops.filled_pairs(opts))
    if not dirty:
        apply_new_project()
        return

    with ui.dialog() as dialog, ui.card().classes("w-[28rem] max-w-[96vw]"):
        ui.label("New project").classes("text-subtitle1 font-bold")
        ui.label(
            "Clear the folder rows and reset Options to Update newer only "
            "(/E /XO /FFT). Saved projects stay. Unsaved changes on this screen "
            "are not kept unless you Save project first."
        ).classes("text-body2")

        def confirm() -> None:
            dialog.close()
            apply_new_project()

        with ui.row().classes("w-full justify-end gap-2"):
            ui.button("Cancel", on_click=dialog.close).props("flat")
            ui.button("New project", icon="note_add", on_click=confirm).props(
                "unelevated"
            )

    dialog.open()


def show_leftover_robocopy_dialog(
    others: list[robocopy_ops.RobocopyProcess],
    *,
    then_run: bool = False,
) -> None:
    """A window-less robocopy.exe from a previous test can outlive the UI."""
    pids = ", ".join(str(p.pid) for p in others)
    status_label.set_text(f"Blocked — leftover Robocopy (PID {pids}).")
    push_output(
        f"A hidden robocopy.exe is still running (PID {pids}). "
        "Stop it before starting another job.",
        stamp=True,
    )

    with ui.dialog() as dialog, ui.card().classes("w-[32rem] max-w-[96vw]"):
        ui.label("Robocopy is still running").classes("text-subtitle1 font-bold")
        ui.label(
            f"A hidden robocopy.exe is still active (PID {pids}). "
            "That is usually a leftover from an earlier test — the bar can say "
            "Ready while that process keeps going. Stop it, then run again."
        ).classes("text-body2")

        async def stop_leftover() -> None:
            dialog.close()
            await stop_sync()
            if then_run and not await asyncio.to_thread(robocopy_ops.find_running_robocopy):
                show_run_dialog()

        with ui.row().classes("w-full justify-end gap-2"):
            ui.button("Cancel", on_click=dialog.close).props("flat")
            ui.button(
                "Stop leftover",
                icon="stop",
                on_click=stop_leftover,
            ).props("unelevated color=negative")

    dialog.open()


async def check_leftover_robocopy() -> None:
    if _running:
        return
    others = await asyncio.to_thread(robocopy_ops.find_running_robocopy)
    if not others:
        return
    pids = ", ".join(str(p.pid) for p in others)
    status_label.set_text(f"Leftover Robocopy (PID {pids}). Click Stop Robocopy.")
    notify_user(
        f"A hidden robocopy.exe is still running (PID {pids}). "
        "Click Stop Robocopy before starting a new job.",
        kind="warning",
        linger=True,
    )


async def stop_sync() -> None:
    """Stop the job started here, or a leftover robocopy.exe from a closed window."""
    global _active_process, _cancel_queue
    _cancel_queue = True
    proc = _active_process
    pids: list[int] = []
    if proc is not None and proc.returncode is None and proc.pid:
        pids.append(int(proc.pid))
        try:
            proc.kill()
        except ProcessLookupError:
            pass
    others = await asyncio.to_thread(robocopy_ops.find_running_robocopy)
    for item in others:
        if item.pid not in pids:
            pids.append(item.pid)
    if not pids:
        ui.notify("No robocopy.exe is running.", type="info")
        status_label.set_text("Ready.")
        return
    stopped, errors = await asyncio.to_thread(robocopy_ops.stop_robocopy_processes, pids)
    if stopped:
        msg = f"Stopped Robocopy (PID {', '.join(str(p) for p in stopped)})."
        ui.notify(msg, type="positive")
        push_output(msg, stamp=True)
        show_running_chrome(False)
        set_run_status("Ready. Preview the command, then confirm.")
        status_label.set_text("Stopped leftover Robocopy. You can run again.")
    if errors:
        err = " ; ".join(errors)
        ui.notify(err, type="warning", multi_line=True, timeout=8000)
        push_output(err, tone="err")


def show_run_dialog() -> None:
    """Preview the exact argv, then confirm — extra ack for /MIR and drive roots."""
    err = robocopy_ops.validate_opts(opts)
    if err:
        ui.notify(err, type="negative", multi_line=True, timeout=8000)
        return

    pairs = robocopy_ops.filled_pairs(opts)
    job_count = len(pairs)
    comparing = bool(opts.compare_only)
    listing = robocopy_ops.effective_dry_run(opts)
    mirroring = bool(opts.mirror and not comparing)
    real_mirror = mirroring and not listing
    purging = bool(opts.purge and not comparing and not mirroring)
    real_purge = purging and not listing
    has_volume_root = robocopy_ops.pairs_include_drive_root(opts)
    preview = robocopy_ops.preview_all_commands(opts)
    job_word = "job" if job_count == 1 else "jobs"

    if comparing:
        title = "Compare folders" if job_count == 1 else f"Compare folders ({job_count} {job_word})"
        blurb = (
            "This runs robocopy /L as a list-only compare. Nothing is copied or "
            "deleted. A compare_*.log is written so you can open See reports."
        )
        if job_count > 1:
            blurb += " Pairs run one after another."
        action_label = "Compare folders"
        action_icon = "difference"
        action_color = "secondary"
    elif listing:
        title = "Dry-run Robocopy" if job_count == 1 else f"Dry-run Robocopy ({job_count} {job_word})"
        blurb = (
            "This runs robocopy /L (list only). Nothing is copied or deleted. "
            "Review the output, then uncheck Dry run for a real copy."
        )
        if job_count > 1:
            blurb += " Pairs run one after another."
        action_label = "Run dry-run"
        action_icon = "preview"
        action_color = "primary"
    elif real_mirror:
        title = "Mirror destination" if job_count == 1 else f"Mirror destinations ({job_count} {job_word})"
        blurb = (
            "This runs robocopy /MIR as the current Windows user. Extra files "
            "on the destination that are not in the source will be DELETED. "
            "There is no undo."
        )
        if job_count > 1:
            blurb += " Each pair is mirrored in order."
        action_label = "Mirror now"
        action_icon = "sync"
        action_color = "negative"
    elif real_purge:
        title = "Purge extra destination files" if job_count == 1 else f"Purge extras ({job_count} {job_word})"
        blurb = (
            "This runs robocopy /PURGE as the current Windows user. Files on the "
            "destination that are not in the source will be DELETED. There is no undo. "
            "Newer destination files are still skipped if Skip older (/XO) is on."
        )
        if job_count > 1:
            blurb += " Each pair runs in order."
        action_label = "Purge extras"
        action_icon = "delete_sweep"
        action_color = "negative"
    else:
        title = "Run Robocopy" if job_count == 1 else f"Run Robocopy ({job_count} {job_word})"
        blurb = (
            "This runs robocopy as the current Windows user. Files that match "
            "the selected rules will be copied to the destination."
        )
        if job_count > 1:
            blurb += " Folder pairs run one after another with the same options."
        action_label = "Run Robocopy"
        action_icon = "play_arrow"
        action_color = "primary"

    with ui.dialog() as dialog, ui.card().classes("w-[760px] max-w-[96vw]"):
        dialog.props("persistent")
        ui.label(title).classes("text-subtitle1 font-bold")
        ui.label(blurb).classes("text-body2")

        if job_count == 1:
            ui.label(f"Source: {pairs[0].source}").classes(
                "text-caption text-grey-8 break-all"
            )
            ui.label(f"Destination: {pairs[0].destination}").classes(
                "text-caption text-grey-8 break-all"
            )
        else:
            ui.label(f"{job_count} jobs will run in order:").classes(
                "text-caption text-grey-8"
            )
            for i, pair in enumerate(pairs, start=1):
                ui.label(f"{i}. {pair.source}  →  {pair.destination}").classes(
                    "text-caption text-grey-8 break-all"
                )

        ui.label(
            "Command that will run" if job_count == 1 else "Commands that will run"
        ).classes("text-caption text-grey-8")
        ui.code(preview).classes("w-full")
        ui.label(
            "Each job gets its own timestamped log file when it starts."
        ).classes("text-caption text-grey-7")

        w_ack_mirror = ui.checkbox(
            "I understand /MIR can delete extra files on the destination."
        )
        if not real_mirror:
            w_ack_mirror.visible = False

        w_ack_purge = ui.checkbox(
            "I understand /PURGE can delete destination files that are not in the source."
        )
        if not real_purge:
            w_ack_purge.visible = False

        w_ack_root = ui.checkbox(
            "I understand a drive root is selected — this can copy or "
            "(with Mirror or Purge) delete across that volume."
        )
        if not has_volume_root or listing:
            w_ack_root.visible = False

        with ui.row().classes("w-full justify-end gap-2"):
            ui.button("Cancel", on_click=dialog.close).props("flat")
            confirm_btn = ui.button(action_label, icon=action_icon).props(
                f"unelevated color={action_color}"
            )

        def refresh_confirm() -> None:
            if real_mirror and not w_ack_mirror.value:
                confirm_btn.disable()
                return
            if real_purge and not w_ack_purge.value:
                confirm_btn.disable()
                return
            if has_volume_root and not listing and not w_ack_root.value:
                confirm_btn.disable()
                return
            confirm_btn.enable()

        w_ack_mirror.on_value_change(lambda _e: refresh_confirm())
        w_ack_purge.on_value_change(lambda _e: refresh_confirm())
        w_ack_root.on_value_change(lambda _e: refresh_confirm())
        refresh_confirm()

        async def confirmed() -> None:
            if real_mirror and not w_ack_mirror.value:
                ui.notify("Confirm the Mirror warning first.", type="warning")
                return
            if real_purge and not w_ack_purge.value:
                ui.notify("Confirm the Purge warning first.", type="warning")
                return
            if has_volume_root and not listing and not w_ack_root.value:
                ui.notify("Confirm the drive-root warning first.", type="warning")
                return
            dialog.close()
            await asyncio.sleep(0.22)
            await run_sync()

        confirm_btn.on_click(confirmed)

    dialog.open()


async def request_run() -> None:
    if _running:
        ui.notify("A job is already running in this app window.", type="warning")
        return
    err = robocopy_ops.validate_opts(opts)
    if err:
        ui.notify(err, type="negative", multi_line=True, timeout=8000)
        return
    others = await asyncio.to_thread(robocopy_ops.find_running_robocopy)
    if others:
        show_leftover_robocopy_dialog(others, then_run=True)
        return
    show_run_dialog()


async def _run_one_pair(
    pair: robocopy_ops.FolderPair,
    job_index: int,
    job_count: int,
) -> dict[str, Any]:
    """Run robocopy for one source → destination pair. Returns a result dict."""
    global _last_run_log, _active_process, _session_logs
    comparing = bool(opts.compare_only)
    multi = job_count > 1
    log_index = job_index if multi else None

    run_log: Optional[str] = None
    if opts.write_log or opts.compare_only:
        run_log = robocopy_ops.make_run_log_path(opts, job_index=log_index)
        opts.log_path = run_log
        _last_run_log = run_log
        if run_log not in _session_logs:
            _session_logs.append(run_log)
        try:
            w_log.value = run_log
        except Exception:
            pass

    args = robocopy_ops.build_robocopy_args(
        opts,
        source=pair.source,
        destination=pair.destination,
        run_log=run_log,
        job_index=log_index,
    )

    if multi:
        push_output("")
        push_output(
            f"===== Job {job_index}/{job_count}: {pair.source}  →  {pair.destination} =====",
            stamp=True,
        )
    push_output(f"$ {robocopy_ops.format_command_preview(args)}")
    push_output(f"Source:      {pair.source}")
    push_output(f"Destination: {pair.destination}")
    if comparing:
        push_output(
            "Compare only: listing differences — no files will be copied or deleted."
        )
    if run_log:
        push_output(f"Log file:    {run_log}")
    push_output("")

    if multi:
        set_run_status(
            f"Comparing {job_index}/{job_count}…"
            if comparing
            else f"Running Robocopy {job_index}/{job_count}…"
        )
    else:
        set_run_status(
            "Comparing folders (no copy)…" if comparing else "Running Robocopy…"
        )

    creationflags = robocopy_ops.creationflags_no_window()
    tailer = robocopy_ops.LogFileTailer(run_log) if run_log else None
    shown_from_log = 0
    shown_from_out = 0
    try:
        process = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            creationflags=creationflags,
        )
        _active_process = process
        if process.pid:
            robocopy_ops.assign_pid_to_kill_job(int(process.pid))
            push_output(f"Robocopy PID {process.pid} — running…", stamp=True)
        assert process.stdout is not None
        wait_task = asyncio.create_task(process.wait())
        while not wait_task.done():
            read_task = asyncio.create_task(process.stdout.readline())
            done, _pending = await asyncio.wait(
                {read_task, wait_task},
                timeout=0.4,
                return_when=asyncio.FIRST_COMPLETED,
            )
            grew = False
            if read_task in done:
                raw = read_task.result() or b""
                if raw:
                    text = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                    if text and shown_from_log == 0:
                        push_output(text, flush=False)
                        shown_from_out += 1
                        grew = True
            elif not read_task.done():
                read_task.cancel()
                try:
                    await read_task
                except (asyncio.CancelledError, Exception):
                    pass
            if tailer is not None:
                new_lines = tailer.poll()
                if new_lines:
                    shown_from_log += len(new_lines)
                    for text in new_lines:
                        push_output(text, flush=False)
                    grew = True
            if grew:
                _flush_output()
                await scroll_output()
        code = await wait_task
        if process.stdout and not process.stdout.at_eof():
            try:
                leftover = await asyncio.wait_for(process.stdout.read(), timeout=0.3)
            except (asyncio.TimeoutError, Exception):
                leftover = b""
            if leftover and shown_from_log == 0:
                for text in leftover.decode("utf-8", errors="replace").splitlines():
                    if text.strip():
                        push_output(text, flush=False)
                        shown_from_out += 1
        if tailer is not None:
            for text in tailer.poll():
                push_output(text, flush=False)
                shown_from_log += 1
            _flush_output()
    except FileNotFoundError:
        push_output("ERROR: robocopy.exe not found. Is this Windows?", stamp=True)
        code = 16
    except Exception as exc:
        push_output(f"ERROR: {exc}", stamp=True)
        code = 16
    finally:
        still = _active_process
        _active_process = None
        if still is not None and still.returncode is None:
            try:
                still.kill()
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(still.wait(), timeout=5)
            except Exception:
                if still.pid:
                    await asyncio.to_thread(
                        robocopy_ops.stop_robocopy_processes, [int(still.pid)]
                    )

    append_log_file_to_output(run_log)

    meaning = robocopy_ops.ROBOCOPY_EXIT_MEANING.get(
        code,
        "Unknown / serious error" if code >= 8 else f"Combined flags (exit {code})",
    )
    job_tag = f"job {job_index} " if multi else ""
    result: dict[str, Any] = {
        "index": job_index,
        "pair": pair,
        "code": code,
        "log": run_log,
        "meaning": meaning,
        "headline": meaning,
        "folders_match": None,
        "detail": "",
    }

    if comparing:
        report: Optional[robocopy_ops.RobocopyReport] = None
        if run_log and Path(run_log).is_file():
            try:
                report = robocopy_ops.parse_robocopy_log(run_log)
            except Exception:
                report = None
        folders_match, headline, detail, banner = robocopy_ops.format_compare_verdict(
            code, report
        )
        result["folders_match"] = folders_match
        result["headline"] = headline
        result["detail"] = detail
        push_output("")
        for line in banner:
            push_output(line, flush=False)
        push_output("")
        push_output(f"— {job_tag}finished with exit code {code} —", stamp=True)
        if run_log:
            push_output(f"— log saved to: {run_log} —")
            robocopy_ops.append_compare_verdict_to_log(run_log, banner)
        await scroll_output()
        return result

    report: Optional[robocopy_ops.RobocopyReport] = None
    if run_log and Path(run_log).is_file():
        try:
            report = robocopy_ops.parse_robocopy_log(run_log)
        except Exception:
            report = None
    headline, result_lines = robocopy_ops.format_copy_result(
        code, meaning, report, log_path=run_log
    )
    result["headline"] = headline
    for line in result_lines:
        push_output(line, flush=False)
    _flush_output()
    await scroll_output()
    return result


def _notify_job_results(
    results: list[dict[str, Any]],
    *,
    comparing: bool,
    cancelled: bool,
    job_count: int,
) -> None:
    done = len(results)
    if not results:
        status_label.set_text("Stopped before any job started.")
        set_output_summary("Stopped before any job started.", tone="warn")
        return

    if comparing:
        failed = sum(
            1 for r in results if r["code"] > robocopy_ops.ROBOCOPY_SUCCESS_MAX
        )
        matched = sum(1 for r in results if r.get("folders_match") is True)
        differed = done - failed - matched
        if cancelled:
            status = f"Stopped after {done} of {job_count} compare jobs."
        elif job_count == 1:
            status = f"{results[0]['headline']}  (exit {results[0]['code']})"
        else:
            status = (
                f"Compared {done} pairs — {matched} match, {differed} differ, "
                f"{failed} failed."
            )
        status_label.set_text(status)
        if job_count == 1:
            r = results[0]
            cmp_tone = (
                "err"
                if r["code"] > robocopy_ops.ROBOCOPY_SUCCESS_MAX
                else ("ok" if r.get("folders_match") else "warn")
            )
            set_output_summary(str(r.get("headline") or status), tone=cmp_tone)
            kind = (
                "negative"
                if r["code"] > robocopy_ops.ROBOCOPY_SUCCESS_MAX
                else ("positive" if r.get("folders_match") else "warning")
            )
            notify_user(str(r.get("headline") or status), kind=kind, linger=True)
        else:
            kind = "warning" if differed or failed or cancelled else "positive"
            cmp_tone = "err" if failed else ("warn" if differed or cancelled else "ok")
            set_output_summary(status, tone=cmp_tone)
            notify_user(status, kind=kind, linger=True)
        return

    failed = sum(1 for r in results if r["code"] > robocopy_ops.ROBOCOPY_SUCCESS_MAX)
    ok = done - failed
    if cancelled:
        status = f"Stopped after {done} of {job_count} jobs."
    elif job_count == 1:
        r = results[0]
        status = (
            f"Done (exit {r['code']}) — {r['meaning']}"
            if r["code"] <= robocopy_ops.ROBOCOPY_SUCCESS_MAX
            else f"Failed (exit {r['code']}) — {r['meaning']}"
        )
    else:
        status = f"Finished {done} jobs — {ok} ok, {failed} failed."
    status_label.set_text(status)
    if job_count == 1:
        r0 = results[0]
        copy_tone = (
            "err" if r0["code"] > robocopy_ops.ROBOCOPY_SUCCESS_MAX else "ok"
        )
        set_output_summary(str(r0.get("headline") or status), tone=copy_tone)
    else:
        copy_tone = (
            "err"
            if failed and not ok
            else ("warn" if failed or cancelled else "ok")
        )
        set_output_summary(status, tone=copy_tone)

    if job_count == 1:
        r = results[0]
        kind = (
            "positive"
            if r["code"] <= robocopy_ops.ROBOCOPY_SUCCESS_MAX
            else "negative"
        )
        notify_user(str(r.get("headline") or status), kind=kind, linger=True)
        return

    kind = (
        "negative"
        if failed and not ok
        else ("warning" if failed or cancelled else "positive")
    )
    notify_user(status, kind=kind, linger=True)


async def run_sync() -> None:
    global _running, _cancel_queue, _run_started_at
    if _running:
        ui.notify("A job is already running in this app window.", type="warning")
        return

    err = robocopy_ops.validate_opts(opts)
    if err:
        ui.notify(err, type="negative", multi_line=True, timeout=8000)
        return

    others = await asyncio.to_thread(robocopy_ops.find_running_robocopy)
    if others:
        show_leftover_robocopy_dialog(others, then_run=True)
        return

    pairs = robocopy_ops.filled_pairs(opts)
    job_count = len(pairs)
    save_config(opts)

    comparing = bool(opts.compare_only)
    _running = True
    _cancel_queue = False
    _run_started_at = datetime.now()
    run_btn.disable()
    show_running_chrome(True)
    set_run_status(
        "Comparing folders…" if comparing else "Running Robocopy…"
    )
    clear_output()
    set_output_summary(
        "Robocopy is running… this terminal fills as the job writes lines.",
        tone="run",
    )
    started = _run_started_at.strftime("%Y-%m-%d %H:%M:%S")
    if job_count == 1:
        push_output(f"— started {started} —")
    else:
        push_output(f"— started {started} — {job_count} jobs in order —")
    push_output("Waiting for Robocopy output…")
    push_output("")
    await focus_output_panel()
    await scroll_output()

    results: list[dict[str, Any]] = []
    cancelled = False
    try:
        for i, pair in enumerate(pairs, start=1):
            if _cancel_queue:
                cancelled = True
                push_output("Stopped. Remaining jobs were not started.", tone="warn")
                break
            results.append(await _run_one_pair(pair, i, job_count))
            if _cancel_queue:
                cancelled = True
                leftover = job_count - i
                if leftover:
                    push_output("Stopped. Remaining jobs were not started.", tone="warn")
                break
        if job_count > 1 and results:
            push_output("")
            push_output(
                "===== Stopped =====" if cancelled else "===== All jobs finished =====",
                tone="head",
            )
            for r in results:
                pair = r["pair"]
                push_output(
                    f"{r['index']}. {pair.source}  →  {pair.destination}  "
                    f"(exit {r['code']})"
                )
    finally:
        _running = False
        _cancel_queue = False
        _run_started_at = None
        run_btn.enable()
        show_running_chrome(False)
        robocopy_ops.sync_legacy_paths(opts)

    _notify_job_results(
        results,
        comparing=comparing,
        cancelled=cancelled,
        job_count=job_count,
    )
    _flush_output()
    await scroll_output()
    await focus_output_panel()


def open_reports_dialog() -> None:
    """Modal: pick a log from this session and show which files were transferred."""
    logs = robocopy_ops.list_available_logs(
        opts,
        extra=_last_run_log,
        session_paths=_session_logs,
    )
    if not logs:
        with ui.dialog() as empty, ui.card().classes("w-[28rem] max-w-[96vw]"):
            ui.label("No reports this session").classes("text-subtitle1 font-bold")
            ui.label(
                "See reports only lists jobs you run after opening the app. "
                "Older log files stay on disk in the log folder."
            ).classes("text-body2")

            def open_folder() -> None:
                try:
                    folder = robocopy_ops.log_directory_from_opts(opts)
                    folder.mkdir(parents=True, exist_ok=True)
                    robocopy_ops.open_in_explorer(str(folder))
                except Exception as exc:
                    ui.notify(f"Could not open log folder: {exc}", type="warning")

            with ui.row().classes("w-full justify-end gap-2"):
                ui.button("Open log folder", icon="folder_open", on_click=open_folder).props(
                    "flat"
                )
                ui.button("OK", on_click=empty.close).props("unelevated")
        empty.open()
        return

    options = {
        str(p): f"{p.name}  —  {datetime.fromtimestamp(p.stat().st_mtime).strftime('%Y-%m-%d %H:%M:%S')}"
        for p in logs
    }

    default = None
    if _last_run_log and _last_run_log in options:
        default = _last_run_log
    else:
        default = str(logs[0])

    with ui.dialog() as dialog, ui.card().classes("w-full").style(
        "min-width: min(960px, 96vw); max-width: 980px; max-height: 92vh;"
    ):
        with ui.row().classes("w-full items-center justify-between no-wrap"):
            with ui.row().classes("items-center gap-2"):
                ui.icon("assessment", color="primary")
                ui.label("Robocopy reports").classes("text-h6")
            ui.button(icon="close", on_click=dialog.close).props("flat round dense")

        intro = ui.label(
            "This session only. Parsed from the log: files Robocopy copied "
            "(or would copy in dry-run / compare), extras on the destination, and errors."
        ).classes("text-caption text-grey-8")

        verdict_banner = (
            ui.label("")
            .classes("w-full text-body1 text-weight-medium q-pa-md rounded-borders")
        )
        verdict_banner.visible = False

        log_select = ui.select(
            options=options,
            value=default,
            label="Log file",
        ).classes("w-full")

        filter_select = ui.select(
            options={
                "differences": "Differences (compare view)",
                "transferred": "Would copy / transferred",
                "extra": "Extras on destination",
                "error": "Errors",
                "skipped": "Skipped (same / older)",
                "all": "All parsed actions",
            },
            value="differences",
            label="Show",
        ).classes("w-72")

        meta = ui.column().classes("w-full gap-1")
        stats = ui.row().classes("w-full gap-3 flex-wrap")
        table_host = ui.column().classes("w-full").style(
            "max-height: 48vh; overflow: auto;"
        )
        note_label = ui.label("").classes("text-caption text-orange-9")

        state: dict = {"report": None}

        def render() -> None:
            path = log_select.value
            if not path:
                return
            report = robocopy_ops.parse_robocopy_log(path)
            state["report"] = report
            compare = robocopy_ops.is_compare_log(report)

            intro.set_text(
                "Compare log: differences only (nothing was copied). "
                "New / Newer / Modified / Older / Extra on dest."
                if compare
                else "Parsed from the log: files Robocopy actually copied "
                "(New / Newer / Modified), plus extras on the destination and errors."
            )

            if compare:
                has_diff = bool(report.differences) or bool(report.errors)
                if report.incomplete and not has_diff:
                    verdict_banner.set_text(
                        "COMPARE LOG LOOKS INCOMPLETE — re-run Compare only if you are "
                        "not sure. A finished match shows: ALL FILES ARE THE SAME."
                    )
                    verdict_banner.classes(
                        replace="w-full text-body1 text-weight-medium q-pa-md "
                        "rounded-borders bg-orange-1 text-orange-10"
                    )
                elif has_diff:
                    verdict_banner.set_text(
                        f"CHECK THE LOG — THERE ARE DIFFERENCES  "
                        f"({len(report.differences)} difference line(s), "
                        f"{len(report.extras)} extra on destination, "
                        f"{len(report.errors)} error(s)). "
                        "Folders are NOT the same. Nothing was copied. "
                        "Use the table below or Copy list for the full list."
                    )
                    verdict_banner.classes(
                        replace="w-full text-body1 text-weight-medium q-pa-md "
                        "rounded-borders bg-orange-2 text-orange-10"
                    )
                else:
                    verdict_banner.set_text(
                        "ALL FILES ARE THE SAME  —  Compare found no new, newer, older, "
                        "modified, or extra files in this log. Source and destination "
                        "match for this scan. Nothing was copied."
                    )
                    verdict_banner.classes(
                        replace="w-full text-body1 text-weight-medium q-pa-md "
                        "rounded-borders bg-green-2 text-green-10"
                    )
                verdict_banner.visible = True
            else:
                verdict_banner.set_text("")
                verdict_banner.visible = False

            meta.clear()
            with meta:
                mode_line = (
                    "**Mode:** Compare only (list — no copy)  \n" if compare else ""
                )
                ui.markdown(
                    f"{mode_line}"
                    f"**Source:** `{report.source or '—'}`  \n"
                    f"**Destination:** `{report.destination or '—'}`  \n"
                    f"**Started:** {report.started or '—'}  \n"
                    f"**Ended:** {report.ended or ('*(incomplete / still running)*' if report.incomplete else '—')}  \n"
                    f"**Options:** `{report.options or '—'}`"
                ).classes("text-sm")
                if report.summary_block:
                    with ui.expansion("Robocopy summary table (raw)", icon="table_chart").classes(
                        "w-full"
                    ):
                        ui.code("\n".join(report.summary_block)).classes("w-full text-xs")

            stats.clear()
            with stats:
                diff_label = "Differences" if compare else "Would copy / transferred"
                ui.badge(
                    f"{diff_label}: {len(report.differences if compare else report.transferred)}"
                ).props("color=primary").classes("text-body2 px-3 py-2")
                with ui.row().classes("items-center gap-2"):
                    ui.badge(f"Extras: {len(report.extras)}").props(
                        "color=orange"
                    ).classes("text-body2 px-3 py-2")
                    ui.label(
                        f"Extra on dest: {robocopy_ops.format_bytes(report.extras_bytes)}"
                    ).classes("text-body2 text-orange-10")
                ui.badge(f"Errors: {len(report.errors)}").props("color=negative").classes(
                    "text-body2 px-3 py-2"
                )
                ui.badge(f"Skipped lines: {len(report.skipped)}").props(
                    "color=grey"
                ).classes("text-body2 px-3 py-2")

            kind = filter_select.value or ("differences" if compare else "transferred")
            if kind == "all":
                rows_src = report.entries
            elif kind == "differences":
                rows_src = report.differences
            elif kind == "transferred":
                rows_src = report.transferred
            elif kind == "extra":
                rows_src = report.extras
            elif kind == "error":
                rows_src = report.errors
            elif kind == "skipped":
                rows_src = report.skipped
            else:
                rows_src = report.differences if compare else report.transferred

            cap = 2000
            rows = [
                {
                    "id": str(i),
                    "action": e.label,
                    "size": e.size or "—",
                    "path": e.path,
                }
                for i, e in enumerate(rows_src[:cap])
            ]

            table_host.clear()
            with table_host:
                if not rows:
                    ui.label(
                        "No differences found for this filter — folders match (or filter is empty)."
                        if compare
                        else "No rows for this filter."
                    ).classes("text-grey-7 q-pa-md")
                else:
                    table = ui.table(
                        columns=[
                            {
                                "name": "action",
                                "label": "Action",
                                "field": "action",
                                "align": "left",
                                "sortable": True,
                            },
                            {
                                "name": "size",
                                "label": "Size",
                                "field": "size",
                                "align": "right",
                                "sortable": True,
                            },
                            {
                                "name": "path",
                                "label": "Path",
                                "field": "path",
                                "align": "left",
                                "sortable": True,
                            },
                            {
                                "name": "actions",
                                "label": "",
                                "field": "actions",
                                "align": "right",
                            },
                        ],
                        rows=rows,
                        row_key="id",
                        pagination=25,
                    ).classes("w-full").props("dense flat wrap-cells")
                    table.add_slot(
                        "body-cell-actions",
                        """
                        <q-td :props="props">
                            <q-btn flat dense round icon="folder_open" color="primary"
                                   @click.stop="$parent.$emit('open_explorer', props.row.path)">
                                <q-tooltip>Reveal in Explorer</q-tooltip>
                            </q-btn>
                        </q-td>
                        """,
                    )
                    table.on(
                        "open_explorer",
                        lambda e: _safe_open_explorer(_path_from_event(e)),
                    )
                    table.on(
                        "rowDblclick",
                        lambda e: _safe_open_explorer(_path_from_event(e)),
                        js_handler="(_evt, row) => emit(row && row.path)",
                    )
                    if len(rows_src) > cap:
                        ui.label(
                            f"Showing first {cap} of {len(rows_src)} rows — use "
                            "“Copy list” for the full set."
                        ).classes("text-caption text-grey-7")

            if report.parse_notes:
                note_label.set_text(" · ".join(report.parse_notes))
            elif report.incomplete:
                note_label.set_text(
                    "Log looks incomplete (no final summary). Counts may grow if the job is still running."
                )
            else:
                note_label.set_text(
                    f"Parsed {len(report.entries)} action line(s) from {Path(path).name}."
                )

        def copy_transferred() -> None:
            report = state.get("report")
            if not report:
                ui.notify("Nothing to copy yet.", type="warning")
                return
            text = robocopy_ops.format_report_transfer_list(report)
            ui.clipboard.write(text)
            if robocopy_ops.is_compare_log(report):
                ui.notify(
                    f"Copied compare report ({len(report.differences)} differences).",
                    type="positive",
                )
            else:
                ui.notify(
                    f"Copied report ({len(report.transferred)} transferred files).",
                    type="positive",
                )

        def open_log_folder() -> None:
            path = log_select.value
            folder = (
                str(Path(path).parent)
                if path
                else str(robocopy_ops.log_directory_from_opts(opts))
            )
            try:
                robocopy_ops.open_in_explorer(folder)
            except Exception as exc:
                ui.notify(f"Could not open folder: {exc}", type="warning")

        def open_raw_log() -> None:
            path = log_select.value
            if not path or not Path(path).is_file():
                ui.notify("Log file not found.", type="warning")
                return
            try:
                robocopy_ops.open_in_explorer(path)
            except Exception as exc:
                ui.notify(f"Could not open log: {exc}", type="warning")

        with ui.row().classes("w-full gap-2 flex-wrap"):
            ui.button("Refresh", icon="refresh", on_click=render).props("outline")
            ui.button(
                "Copy list",
                icon="content_copy",
                on_click=copy_transferred,
            ).props("outline").tooltip(
                "Copy a plain-text report (differences for compare logs, "
                "transferred files for real runs)"
            )
            ui.button("Open log folder", icon="folder_open", on_click=open_log_folder).props(
                "flat"
            )
            ui.button("Open raw log", icon="description", on_click=open_raw_log).props("flat")

        log_select.on("update:model-value", lambda _: render())
        filter_select.on("update:model-value", lambda _: render())
        render()
        dialog.open()


# ---------------------------------------------------------------------------
# Tab 1 — Copy / Compare
# ---------------------------------------------------------------------------


def build_action_tab() -> None:
    global output_view, cmd_preview, run_btn, status_label, progress
    global output_card, output_spinner, output_badge, output_summary, run_spinner, run_clock
    global pairs_host, w_list_select, w_log
    global w_copy_subdirs, w_exclude_older, w_fft, w_dry, w_compare, w_mirror, w_tee, w_write_log
    global w_retries, w_wait, w_mt, preset_buttons
    global w_purge, w_skip_junctions, w_copy_security, w_dst_adjust, w_unbuffered, w_restartable
    global w_exclude_dirs, w_exclude_files, w_max_age, w_copy_all

    with ui.column().classes("w-full max-w-[96rem] mx-auto p-4 gap-4"):
        with ui.card().classes("w-full"):
            ui.markdown(
                "**Copy or compare one or more folder pairs** with `robocopy`. "
                "Three source → destination rows are ready; use **+** to add more. "
                "Empty rows are skipped. Faster than Explorer for large “update this "
                "folder” jobs: skip older files (`/XO`), multi-thread (`/MT`), "
                "dry-run (`/L`), and a timestamped log. Default flags mean **copy "
                "only what is newer** — not “make both folders look identical.” "
                "Preview the command, then confirm."
            )

        with ui.card().classes("w-full"):
            ui.label("Presets").classes("text-subtitle1 font-bold")
            ui.markdown(
                "The **filled** button is the preset in use. "
                "**Update newer only** is the default when you open the app — "
                "you can run with that without clicking it. "
                "Click another to switch; it fills the Options below."
            ).classes("text-caption text-grey-8")
            with ui.row().classes("flex-wrap gap-2"):
                preset_buttons["update"] = ui.button(
                    "Update newer only",
                    icon="update",
                    on_click=lambda: apply_preset("update"),
                ).props("unelevated color=primary")
                preset_buttons["full_copy"] = ui.button(
                    "Full copy",
                    icon="content_copy",
                    on_click=lambda: apply_preset("full_copy"),
                ).props("outline color=primary")
                preset_buttons["mirror"] = ui.button(
                    "Mirror (safe dry-run first)",
                    icon="sync",
                    on_click=lambda: apply_preset("mirror"),
                ).props("outline color=orange")
                preset_buttons["compare"] = ui.button(
                    "Compare only",
                    icon="difference",
                    on_click=lambda: apply_preset("compare"),
                ).props("outline color=secondary").tooltip(
                    "List differences in a log — no files copied or deleted"
                )
                preset_buttons["dry"] = ui.button(
                    "Dry run only",
                    icon="preview",
                    on_click=lambda: apply_preset("dry"),
                ).props("outline color=primary").tooltip(
                    "Add /L to your current options (preview only)"
                )
            refresh_preset_buttons()

        with ui.card().classes("w-full"):
            ui.label("Folders").classes("text-subtitle1 font-bold")
            ui.label(
                "Each row is one source → destination job. Empty rows are skipped. "
                "Click + to add more pairs; they run one after another with the same options. "
                "A project stores these folders and the Options below together "
                "(for example My games). Load restores both. New project starts from "
                "Update newer only."
            ).classes("text-caption text-grey-8")

            with ui.row().classes("w-full items-end gap-2 flex-wrap"):
                ui.button(
                    "New",
                    icon="note_add",
                    on_click=show_new_project_dialog,
                ).props("outline")
                w_list_select = (
                    ui.select(
                        options=saved_list_options(),
                        value=opts.active_list
                        if robocopy_ops.find_saved_list(opts, opts.active_list)
                        else "",
                        label="Project",
                        on_change=on_saved_list_selected,
                    )
                    .classes("min-w-[16rem] flex-grow")
                    .props("dense emit-value map-options")
                )
                ui.button(
                    "Save project",
                    icon="bookmark",
                    on_click=save_active_folder_list,
                ).props("unelevated").tooltip(
                    "Store these folders and the current Options under the selected name"
                )
                ui.button(
                    "Save as…",
                    icon="bookmark_add",
                    on_click=show_save_list_dialog,
                ).props("outline")
                ui.button(
                    icon="delete_outline",
                    on_click=show_delete_list_dialog,
                ).props("flat round").tooltip("Delete the selected saved project")

            pairs_host = ui.column().classes("w-full gap-1")
            render_folder_pairs()

            with ui.row().classes("w-full items-end gap-2 no-wrap mt-2"):
                w_log = (
                    ui.input(
                        "Log location (folder or last file)",
                        value=opts.log_path,
                        placeholder=str(
                            robocopy_ops.LOGS_DIR / "robocopy_YYYYMMDD_HHMMSS.log"
                        ),
                    )
                    .classes("flex-grow")
                    .props("clearable")
                    .on("update:model-value", bind_str("log_path"))
                    .tooltip(
                        "Each run creates a new timestamped log "
                        "(robocopy_YYYYMMDD_HHMMSS.log) in this folder so jobs never "
                        "overwrite each other."
                    )
                )
            ui.label(
                "Each job writes a unique log: robocopy_YYYYMMDD_HHMMSS.log "
                "(or compare_YYYYMMDD_HHMMSS.log in Compare only). "
                "Several pairs in one run get a _1, _2, … suffix."
            ).classes("text-caption text-grey-7")

        with ui.card().classes("w-full"):
            ui.label("Options").classes("text-subtitle1 font-bold")
            with ui.grid(columns=2).classes("w-full gap-2"):
                w_copy_subdirs = ui.checkbox(
                    "Include subfolders (/E)",
                    value=opts.copy_subdirs,
                    on_change=bind_bool("copy_subdirs"),
                )
                w_exclude_older = ui.checkbox(
                    "Skip older destination files (/XO)",
                    value=opts.exclude_older,
                    on_change=bind_bool("exclude_older"),
                ).tooltip(
                    "Only copy when source is newer. Does NOT make file counts equal, "
                    "and does not delete extra destination files. Turn off for Full copy. "
                    "Ignored during Compare only (so both Newer and Older show up)."
                )
                w_fft = ui.checkbox(
                    "FAT-compatible times (/FFT)",
                    value=opts.fat_file_times,
                    on_change=bind_bool("fat_file_times"),
                ).tooltip("2-second time granularity — helpful across drives / USB")
                w_dry = ui.checkbox(
                    "Dry run — list only (/L)",
                    value=opts.dry_run,
                    on_change=bind_bool("dry_run"),
                ).tooltip("No files are copied; preview what would happen")
                w_compare = ui.checkbox(
                    "Compare only (differences log, no copy)",
                    value=opts.compare_only,
                    on_change=bind_bool("compare_only"),
                ).tooltip(
                    "Forces list-only (/L), disables Mirror, writes compare_*.log, "
                    "and lists both newer and older differences for visual confirmation."
                )
                w_mirror = ui.checkbox(
                    "Mirror (/MIR) — can delete extra dest files",
                    value=opts.mirror,
                    on_change=bind_bool("mirror"),
                ).classes("text-orange-8")
                w_write_log = ui.checkbox(
                    "Write log file (/LOG)",
                    value=opts.write_log,
                    on_change=bind_bool("write_log"),
                )
                w_tee = ui.checkbox(
                    "Also show log in console (/TEE)",
                    value=opts.tee_console,
                    on_change=bind_bool("tee_console"),
                )

            with ui.row().classes("w-full gap-4 flex-wrap"):
                w_retries = ui.number(
                    "Retries (/R)",
                    value=opts.retries,
                    min=0,
                    max=100,
                    step=1,
                    on_change=bind_int("retries"),
                ).classes("w-28")
                w_wait = ui.number(
                    "Wait sec (/W)",
                    value=opts.wait_seconds,
                    min=0,
                    max=60,
                    step=1,
                    on_change=bind_int("wait_seconds"),
                ).classes("w-28")
                w_mt = ui.number(
                    "Threads (/MT)",
                    value=opts.multi_thread,
                    min=1,
                    max=128,
                    step=1,
                    on_change=bind_int("multi_thread"),
                ).classes("w-28").tooltip("1 = single-threaded; 8–16 is often a good start")

        with ui.card().classes("w-full"):
            ui.label("More flags").classes("text-subtitle1 font-bold")
            ui.label(
                "Optional switches. /PURGE deletes destination files that vanished "
                "from the source; a real run needs an extra confirmation. "
                "/MIR already includes that delete, so Purge is skipped while Mirror is on."
            ).classes("text-caption text-grey-8")
            with ui.grid(columns=2).classes("w-full gap-2"):
                w_purge = ui.checkbox(
                    "Delete dest extras (/PURGE)",
                    value=opts.purge,
                    on_change=bind_bool("purge"),
                ).classes("text-orange-8").tooltip(
                    "Delete destination files that are not in the source. "
                    "Keeps Update newer only (/XO) if that is on. Extra confirm on a real run."
                )
                w_skip_junctions = ui.checkbox(
                    "Don’t follow directory junctions (/XJD)",
                    value=opts.skip_junctions,
                    on_change=bind_bool("skip_junctions"),
                ).tooltip("Safer backups — skip junction points that point at other volumes")
                w_copy_security = ui.checkbox(
                    "Copy NTFS ACLs (/SEC)",
                    value=opts.copy_security,
                    on_change=bind_bool("copy_security"),
                ).tooltip("Include security descriptors. Implied by /COPYALL below.")
                w_dst_adjust = ui.checkbox(
                    "Compensate DST 1-hour shift (/DST)",
                    value=opts.dst_adjust,
                    on_change=bind_bool("dst_adjust"),
                ).tooltip("Stops seasonal “everything looks 1 hour off” on some USB/NAS copies")
                w_unbuffered = ui.checkbox(
                    "Unbuffered I/O for large files (/J)",
                    value=opts.unbuffered,
                    on_change=bind_bool("unbuffered"),
                ).tooltip("Often better for big video/ISO files")
                w_restartable = ui.checkbox(
                    "Restartable network copy (/Z)",
                    value=opts.restartable,
                    on_change=bind_bool("restartable"),
                ).tooltip("Resume large copies over a flaky share")
            refresh_copy_security_enabled()

        with ui.card().classes("w-full"):
            ui.label("Filters and metadata").classes("text-subtitle1 font-bold")
            ui.label(
                "Skip named folders and files, limit how old a file may be, "
                "or copy extra NTFS information. Separate names with spaces or commas."
            ).classes("text-caption text-grey-8")
            w_exclude_dirs = (
                ui.input(
                    "Exclude folders (/XD)",
                    value=opts.exclude_dirs,
                    placeholder="node_modules .git __pycache__",
                )
                .classes("w-full")
                .props("clearable")
                .on("update:model-value", bind_str("exclude_dirs"))
                .tooltip("Folder names or paths to skip, e.g. node_modules .git")
            )
            w_exclude_files = (
                ui.input(
                    "Exclude files (/XF)",
                    value=opts.exclude_files,
                    placeholder="*.tmp *.log Thumbs.db",
                )
                .classes("w-full")
                .props("clearable")
                .on("update:model-value", bind_str("exclude_files"))
                .tooltip("File names or patterns to skip, e.g. *.tmp *.log")
            )
            with ui.row().classes("w-full items-end gap-4 flex-wrap"):
                w_max_age = (
                    ui.number(
                        "Max age days (/MAXAGE)",
                        value=opts.max_age_days,
                        min=0,
                        max=36500,
                        step=1,
                        on_change=bind_int("max_age_days"),
                    )
                    .classes("w-48")
                    .tooltip("0 = no age limit. 7 = only files from the last 7 days.")
                )
                w_copy_all = ui.checkbox(
                    "Copy all NTFS info (/COPYALL)",
                    value=opts.copy_all,
                    on_change=bind_bool("copy_all"),
                ).tooltip(
                    "Data, attributes, timestamps, ACLs, owner, and auditing. "
                    "May need extra privileges. Turns off the separate /SEC checkbox."
                )

        output_card = ui.card().classes("w-full mb-64 robocopy-output-card").props(
            "id=robocopy-output"
        )
        with output_card:
            with ui.row().classes("w-full items-center justify-between"):
                with ui.row().classes("items-center gap-2"):
                    ui.label("Output").classes("text-subtitle1 font-bold")
                    output_spinner = ui.spinner("dots", size="sm", color="primary")
                    output_spinner.visible = False
                    output_badge = ui.badge("Running", color="primary")
                    output_badge.visible = False
                with ui.row().classes("gap-1"):
                    ui.button(
                        "See reports",
                        icon="assessment",
                        on_click=open_reports_dialog,
                    ).props("flat dense color=secondary")
                    ui.button(
                        "Clear",
                        icon="delete_outline",
                        on_click=lambda: clear_output(idle=True),
                    ).props("flat dense")
            output_summary = ui.label(
                "This terminal shows the live Robocopy session. Run a job to fill it."
            ).classes("text-body2 text-grey-8")
            output_view = (
                ui.html(_terminal_html(), sanitize=False)
                .classes("w-full robocopy-terminal")
            )

        with ui.element("div").classes(
            "fixed left-0 right-0 bottom-0 z-[4000] flex justify-center p-3 pointer-events-none"
        ):
            with ui.card().classes("pointer-events-auto shadow-2xl w-full max-w-[96rem]").style(
                "box-shadow: 0 10px 32px rgba(21, 101, 192, 0.28);"
            ):
                ui.label("Command preview").classes("text-subtitle2 font-bold")
                cmd_preview = ui.code("", language="powershell").classes("w-full").style(
                    "max-height: 8.5rem; overflow: auto;"
                )
                refresh_preview()

                with ui.row().classes("w-full items-center gap-3 flex-wrap"):
                    run_btn = ui.button(
                        "Compare folders" if opts.compare_only else "Run Robocopy",
                        icon="difference" if opts.compare_only else "play_arrow",
                        on_click=request_run,
                    ).props(
                        f"unelevated size=lg color={'secondary' if opts.compare_only else 'primary'}"
                    )

                    ui.button(
                        "Stop Robocopy",
                        icon="stop",
                        on_click=stop_sync,
                    ).props("outline color=negative").tooltip(
                        "Stop a running job, including a leftover robocopy.exe "
                        "after the Python window was closed"
                    )

                    ui.button(
                        "Copy command",
                        icon="content_copy",
                        on_click=lambda: (
                            ui.clipboard.write(robocopy_ops.preview_all_commands(opts)),
                            ui.notify(
                                "Command copied (log name is filled with a real timestamp when you Run)",
                                type="positive",
                            ),
                        ),
                    ).props("outline")

                    ui.button(
                        "Save settings",
                        icon="save",
                        on_click=save_settings_clicked,
                    ).props("flat")

                    ui.button(
                        "See reports",
                        icon="assessment",
                        on_click=open_reports_dialog,
                    ).props("outline color=secondary").tooltip(
                        "Open a report of transferred files or compare differences (from logs)"
                    )

                with ui.row().classes("w-full items-center gap-3"):
                    run_spinner = ui.spinner("dots", size="sm", color="primary")
                    run_spinner.visible = False
                    status_label = ui.label(
                        "Ready. Preview the command, then confirm."
                    ).classes("text-caption text-grey-8")
                progress = (
                    ui.linear_progress(value=0, show_value=False)
                    .props("color=primary")
                    .classes("w-full")
                    .style("height: 8px;")
                )
                progress.visible = False
                run_clock = ui.timer(1.0, tick_run_clock, active=False)
                ui.timer(0.8, check_leftover_robocopy, once=True)
                update_run_button_label()


# ---------------------------------------------------------------------------
# Tab 2 — Manual
# ---------------------------------------------------------------------------


def build_manual_tab() -> None:
    with ui.column().classes("w-full max-w-6xl mx-auto p-4 gap-4"):
        with ui.card().classes("w-full"):
            ui.label("Robocopy manual").classes("text-h6")
            ui.markdown(
                "A field guide to **`robocopy.exe`**, the Windows command for "
                "bulk folder copy and sync. Switches below match `robocopy /?` "
                "on this PC. The **Copy / Compare** tab wraps the daily set "
                "(`/E`, `/XO`, `/FFT`, `/MT`, `/L`, `/MIR`, `/PURGE`, `/LOG`) "
                "plus named projects, extra flags, and excludes.\n\n"
                "Chapters: overview, quick start, **projects**, "
                "**Purge vs Mirror**, **More flags**, **Filters and metadata**, "
                "safety, exit codes."
            )
            with ui.row().classes("items-center gap-2 mt-2"):
                ui.icon("auto_stories", color="primary")
                ui.link(
                    "Open the full Robocopy options guide (interesting flags explained)",
                    "/docs/robocopy-options.html",
                    new_tab=True,
                ).classes("text-primary")

        with ui.card().classes("w-full"):
            ui.markdown(robocopy_docs.OVERVIEW_MD)

        with ui.card().classes("w-full"):
            ui.label("Quick start").classes("text-subtitle1 font-bold")
            ui.markdown(robocopy_docs.QUICKSTART_MD)

        with ui.card().classes("w-full"):
            ui.label("Projects").classes("text-subtitle1 font-bold")
            ui.markdown(robocopy_docs.LISTS_MD)

        with ui.card().classes("w-full"):
            ui.label("Purge vs Mirror").classes("text-subtitle1 font-bold")
            ui.markdown(robocopy_docs.PURGE_MD)

        with ui.card().classes("w-full"):
            ui.label("More flags").classes("text-subtitle1 font-bold")
            ui.markdown(robocopy_docs.EXTRA_FLAGS_MD)

        with ui.card().classes("w-full"):
            ui.label("Filters and metadata").classes("text-subtitle1 font-bold")
            ui.markdown(robocopy_docs.FILTERS_MD)

        with ui.card().classes("w-full"):
            ui.label("Safety").classes("text-subtitle1 font-bold")
            ui.markdown(robocopy_docs.SAFETY_MD)

        with ui.expansion("Full switch list (same as Command map)", icon="list").classes(
            "w-full"
        ):
            ui.markdown(
                "See the **Command map** tab for every switch, an example, and "
                "whether this app already wraps it."
            )
            for group, cmds in robocopy_docs.commands_by_group():
                ui.label(group).classes("text-subtitle2 mt-3")
                lines = [
                    f"- `{c['switch']}` — **{c['title']}** — {c['summary']}" for c in cmds
                ]
                ui.markdown("\n".join(lines))

        with ui.card().classes("w-full"):
            ui.label("Not the same as…").classes("text-subtitle1 font-bold")
            ui.markdown(robocopy_docs.NOT_THE_SAME_MD)

        with ui.card().classes("w-full"):
            ui.label("Exit codes").classes("text-subtitle1 font-bold")
            ui.markdown(
                "Robocopy does **not** use exit code `0` only for success.\n\n"
                "| Code | Meaning |\n|---:|---|\n"
                "| 0 | Nothing to copy (already up to date) |\n"
                "| 1 | Files copied OK |\n"
                "| 2–7 | OK-ish (extras / mismatches / combinations) |\n"
                "| ≥ 8 | Failure (some files could not be copied) |\n\n"
                "So an exit code of **1** after a real update is normal and good."
            )


# ---------------------------------------------------------------------------
# Tab 3 — Command map
# ---------------------------------------------------------------------------


def build_command_map_tab() -> None:
    with ui.column().classes("w-full max-w-6xl mx-auto p-4 gap-4"):
        with ui.card().classes("w-full"):
            ui.label("What robocopy can do").classes("text-h6")
            ui.markdown(robocopy_docs.ROADMAP_MD)
            with ui.row().classes("gap-2 flex-wrap"):
                ui.badge("In this app", color="positive")
                ui.badge("UI later", color="grey")
                ui.badge("Destructive", color="negative")

        filter_box = (
            ui.input(placeholder="Filter switches…")
            .props("dense clearable debounce=200 outlined")
            .classes("w-full max-w-md")
        )

        cards_host = ui.column().classes("w-full gap-3")

        def render(query: str = "") -> None:
            cards_host.clear()
            q = (query or "").strip().lower()
            with cards_host:
                shown = 0
                for group, cmds in robocopy_docs.commands_by_group():
                    filtered = [
                        c
                        for c in cmds
                        if not q
                        or q in c["switch"].lower()
                        or q in c["title"].lower()
                        or q in c["summary"].lower()
                        or q in c["group"].lower()
                    ]
                    if not filtered:
                        continue
                    ui.label(group).classes("text-subtitle1 font-bold mt-2")
                    for cmd in filtered:
                        shown += 1
                        danger = cmd["danger"]
                        with ui.card().classes("w-full"):
                            with ui.row().classes(
                                "w-full items-start justify-between gap-2 flex-wrap"
                            ):
                                with ui.column().classes("gap-0"):
                                    ui.label(cmd["title"]).classes("text-subtitle1 font-bold")
                                    ui.label(cmd["syntax"]).classes(
                                        "text-caption font-mono text-grey-8"
                                    )
                                with ui.row().classes("gap-1"):
                                    ui.badge(cmd["switch"], color="primary")
                                    ui.badge(
                                        robocopy_docs.STATUS_LABEL[cmd["status"]],
                                        color=robocopy_docs.STATUS_COLOR[cmd["status"]],
                                    )
                                    if danger:
                                        ui.badge("Destructive", color="negative")
                            ui.label(cmd["summary"]).classes("text-body2")
                            with ui.expansion("Details & examples", icon="code").classes(
                                "w-full"
                            ):
                                ui.markdown(cmd["details"])
                                for ex in cmd["examples"]:
                                    ui.code(ex).classes("w-full")
                                    ui.button(
                                        "Copy",
                                        icon="content_copy",
                                        on_click=lambda _e, example=ex: (
                                            ui.clipboard.write(example),
                                            ui.notify("Copied", type="positive"),
                                        ),
                                    ).props("flat dense")
                if shown == 0:
                    ui.label("No command matches that filter.").classes("text-grey-7")

        render()
        filter_box.on("update:model-value", lambda e: render(e.args or ""))


# ---------------------------------------------------------------------------
# Tab 4 — Options guide (companion HTML)
# ---------------------------------------------------------------------------


def build_options_guide_tab() -> None:
    with ui.column().classes("w-full p-0"):
        ui.element("iframe").props(
            'src="/docs/robocopy-options.html" title="Robocopy options guide"'
        ).classes("w-full").style(
            "height: calc(100vh - 7.5rem); border: 0; display: block; background: #f4f6f9;"
        )


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------


@ui.page("/")
def index() -> None:
    ui.colors(primary="#1565C0", secondary="#00897B", accent="#FF8F00")
    ui.add_css(
        """
        #robocopy-output, .robocopy-output-card {
            scroll-margin-top: 1rem;
            scroll-margin-bottom: 12rem;
        }
        .robocopy-terminal {
            background-color: #1e1e1e;
            min-height: 20rem;
            max-height: 28rem;
            overflow: auto;
            padding: 0.75rem 1rem;
            border-radius: 4px;
        }
        .robocopy-terminal-pre {
            margin: 0;
            color: #d6d6d6;
            font-family: ui-monospace, SFMono-Regular, Consolas, "Courier New", monospace;
            font-size: 0.8rem;
            line-height: 1.4;
            white-space: pre-wrap;
            word-break: break-word;
        }
        """
    )

    if not robocopy_ops.is_windows():
        with ui.column().classes("w-full max-w-xl mx-auto p-8"):
            ui.label("Windows only").classes("text-h5")
            ui.label("`robocopy` exists only on Windows.")
        return

    with ui.header().classes("items-center justify-between px-4"):
        with ui.row().classes("items-center gap-3"):
            ui.icon("folder_copy", size="md")
            ui.label("Explorer Advance").classes("text-h6")
            ui.badge("Robocopy").props("outline color=white")
        with ui.row().classes("items-center gap-3"):
            ui.link(
                "Robocopy options guide",
                "/docs/robocopy-options.html",
                new_tab=True,
            ).classes("text-white text-weight-medium").style(
                "text-decoration: underline; text-underline-offset: 3px;"
            )
            ui.label(f"Local only · http://{APP_HOST}:{APP_PORT}").classes(
                "text-caption opacity-70"
            )

    with ui.tabs().classes("w-full px-2").props(
        "active-color=primary indicator-color=primary"
    ) as tabs:
        tab_action = ui.tab("action", label="Copy / Compare", icon="folder_copy")
        tab_manual = ui.tab("manual", label="Robocopy manual", icon="menu_book")
        tab_guide = ui.tab("guide", label="Options guide", icon="auto_stories")
        tab_map = ui.tab("map", label="Command map", icon="map")

    with ui.tab_panels(tabs, value=tab_action).classes("w-full"):
        with ui.tab_panel(tab_action).classes("p-0"):
            build_action_tab()
        with ui.tab_panel(tab_manual).classes("p-0"):
            build_manual_tab()
        with ui.tab_panel(tab_guide).classes("p-0"):
            build_options_guide_tab()
        with ui.tab_panel(tab_map).classes("p-0"):
            build_command_map_tab()


app.add_static_files("/docs", str(APP_DIR / "docs"))


def _stop_child_on_shutdown() -> None:
    """Stop only the job this window started. Job object covers hard window close."""
    proc = _active_process
    if proc is not None and proc.returncode is None:
        try:
            proc.kill()
        except ProcessLookupError:
            pass


app.on_shutdown(_stop_child_on_shutdown)


def parse_server_args() -> argparse.Namespace:
    """Bind address: --host / --port, or EXPLORER_HOST / EXPLORER_PORT."""
    parser = argparse.ArgumentParser(description="Explorer Advance — Robocopy UI")
    parser.add_argument(
        "--host",
        default=os.environ.get("EXPLORER_HOST", APP_HOST),
        help="Listen address (default: 127.0.0.1). Use 127.0.0.2 if another "
        "app or Chrome extension already owns localhost.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("EXPLORER_PORT", str(APP_PORT))),
        help=f"HTTP port (default: {APP_PORT}).",
    )
    args, _ = parser.parse_known_args()
    return args


def port_in_use(host: str, port: int) -> bool:
    probe = "127.0.0.1" if host in {"0.0.0.0", "::", ""} else host
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.3)
        return sock.connect_ex((probe, port)) == 0


def pick_listen_port(host: str, preferred: int, *, span: int = 20) -> int:
    """Use `preferred` when free; otherwise the next free port in the span."""
    for port in range(preferred, preferred + span):
        if not port_in_use(host, port):
            return port
    return preferred


if __name__ in {"__main__", "__mp_main__"}:
    server = parse_server_args()
    port = pick_listen_port(server.host, server.port)
    if port != server.port:
        print(
            f"Port {server.port} is already in use "
            f"(another copy of this app is probably still running).\n"
            f"Starting on http://{server.host}:{port} instead.\n"
            f"To keep a chosen port: close the other window, or start with "
            f"`py -3 main.py --port {port + 1}`.",
            file=sys.stderr,
        )
    ui.run(
        title=APP_TITLE,
        reload=False,
        host=server.host,
        port=port,
        show=True,
        favicon="📁",
    )
