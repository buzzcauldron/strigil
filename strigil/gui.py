"""Minimal GUI for Strigil. Run with: strigil-gui"""

import os
import queue
import re
import subprocess
import sys
import threading
from pathlib import Path

import tkinter as tk
from tkinter import ttk

from strigil._deps import check_required, ensure_optional
from strigil.hardware import (
    default_workers,
    get_aggressiveness_params,
    suggest_aggressiveness,
)

LAST_URLS_FILE = Path.home() / ".strigil" / "last_urls.txt"


def _load_last_urls() -> str:
    """Return last used URL(s), one per line, or default."""
    try:
        if LAST_URLS_FILE.exists():
            text = LAST_URLS_FILE.read_text(encoding="utf-8").strip()
            if text:
                return text
    except OSError:
        pass
    return "https://example.com"


def _save_last_urls(text: str) -> None:
    """Persist URL(s) for next launch."""
    if not text or not text.strip():
        return
    try:
        LAST_URLS_FILE.parent.mkdir(parents=True, exist_ok=True)
        LAST_URLS_FILE.write_text(text.strip(), encoding="utf-8")
    except OSError:
        pass

# Image size presets (bytes): Small < 100 KB, Medium 100 KB–1 MB, Large > 1 MB
SIZE_SMALL_MAX = 100 * 1024
SIZE_MEDIUM_MIN = 100 * 1024
SIZE_MEDIUM_MAX = 1024 * 1024
SIZE_LARGE_MIN = 1024 * 1024


def _open_folder(path: str) -> None:
    """Open path in the system file manager; create dir if missing."""
    if not path or not path.strip():
        return
    abs_path = os.path.abspath(path.strip())
    try:
        os.makedirs(abs_path, exist_ok=True)
    except OSError:
        pass
    if sys.platform == "darwin":
        subprocess.run(["open", abs_path], check=False)
    elif sys.platform == "win32":
        os.startfile(abs_path)  # type: ignore[attr-defined]
    else:
        subprocess.run(["xdg-open", abs_path], check=False)


def main() -> None:
    check_required()
    ensure_optional()
    root = tk.Tk()
    root.title("Strigil")
    root.minsize(400, 320)

    main_frame = ttk.Frame(root, padding=10)
    main_frame.pack(fill=tk.BOTH, expand=True)

    bottom_frame = ttk.Frame(main_frame)
    bottom_frame.pack(side=tk.BOTTOM, fill=tk.X, padx=0, pady=(8, 0))

    content_frame = ttk.Frame(main_frame)
    content_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

    url_row = ttk.Frame(content_frame)
    url_row.grid(row=0, column=0, columnspan=2, sticky=tk.EW, pady=(0, 2))
    ttk.Label(url_row, text="URLs (one per line)").pack(side=tk.LEFT)
    content_frame.columnconfigure(0, weight=1)
    urls_mode_var = tk.StringVar(value="sequential")
    urls_mode_frame = ttk.Frame(url_row)
    urls_mode_frame.pack(side=tk.RIGHT)
    ttk.Label(urls_mode_frame, text="Run:").pack(side=tk.LEFT, padx=(8, 4))
    ttk.Radiobutton(urls_mode_frame, text="Sequential", variable=urls_mode_var, value="sequential").pack(side=tk.LEFT)
    ttk.Radiobutton(urls_mode_frame, text="Parallel", variable=urls_mode_var, value="parallel").pack(side=tk.LEFT, padx=(4, 0))

    url_text = tk.Text(content_frame, height=4, width=50, wrap=tk.WORD)
    url_text.insert("1.0", _load_last_urls())
    url_text.grid(row=1, column=0, columnspan=2, sticky=tk.EW, pady=(0, 8))

    def _url_context_menu(event: tk.Event) -> None:
        menu = tk.Menu(root, tearoff=0)
        menu.add_command(label="Cut", command=lambda: url_text.event_generate("<<Cut>>"))
        menu.add_command(label="Copy", command=lambda: url_text.event_generate("<<Copy>>"))
        menu.add_command(label="Paste", command=lambda: url_text.event_generate("<<Paste>>"))
        menu.add_separator()
        menu.add_command(
            label="Replace selection with clipboard",
            command=lambda: _replace_selection_with_clipboard(url_text, root),
        )
        menu.tk_popup(event.x_root, event.y_root)

    def _replace_selection_with_clipboard(text_widget: tk.Text, w: tk.Tk) -> None:
        try:
            clip = w.clipboard_get()
        except tk.TclError:
            return
        if text_widget.tag_ranges(tk.SEL):
            text_widget.delete(tk.SEL_FIRST, tk.SEL_LAST)
        text_widget.insert(tk.INSERT, clip)

    url_text.bind("<Button-3>", _url_context_menu)

    ttk.Label(content_frame, text="Output directory").grid(row=2, column=0, sticky=tk.W, pady=(0, 2))
    out_row = ttk.Frame(content_frame)
    out_row.grid(row=3, column=0, columnspan=2, sticky=tk.EW, pady=(0, 8))
    content_frame.columnconfigure(0, weight=1)
    out_var = tk.StringVar(value="output")
    out_entry = ttk.Entry(out_row, textvariable=out_var, width=50)
    out_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8))
    ttk.Button(out_row, text="Open folder", command=lambda: _open_folder(out_var.get())).pack(side=tk.LEFT)

    ttk.Label(content_frame, text="Done script (optional, use {out_dir})").grid(row=4, column=0, sticky=tk.W, pady=(8, 2))
    done_script_var = tk.StringVar(value="")
    done_script_entry = ttk.Entry(content_frame, textvariable=done_script_var, width=50)
    done_script_entry.grid(row=5, column=0, columnspan=2, sticky=tk.EW, pady=(0, 4))

    suggest_var = tk.BooleanVar(value=False)

    def apply_suggested() -> None:
        if not suggest_var.get():
            return
        type_pdf_var.set(True)
        type_text_var.set(True)
        type_images_var.set(True)
        size_small_var.set(False)
        size_medium_var.set(True)
        size_large_var.set(True)
        delay_var.set(0.5)
        js_var.set(True)
        crawl_var.set(True)
        depth_var.set(2)
        same_domain_var.set(True)

    suggest_cb = ttk.Checkbutton(
        content_frame,
        text="Suggest likely choices",
        variable=suggest_var,
        command=apply_suggested,
    )
    suggest_cb.grid(row=6, column=0, columnspan=2, sticky=tk.W, pady=(0, 8))

    types_frame = ttk.LabelFrame(content_frame, text="File types")
    types_frame.grid(row=7, column=0, columnspan=2, sticky=tk.W, pady=(0, 8))
    type_pdf_var = tk.BooleanVar(value=True)
    type_text_var = tk.BooleanVar(value=True)
    type_images_var = tk.BooleanVar(value=True)
    ttk.Checkbutton(types_frame, text="PDF", variable=type_pdf_var).pack(side=tk.LEFT, padx=(0, 12))
    ttk.Checkbutton(types_frame, text="Text", variable=type_text_var).pack(side=tk.LEFT, padx=(0, 12))
    ttk.Checkbutton(types_frame, text="Images", variable=type_images_var).pack(side=tk.LEFT)

    size_frame = ttk.LabelFrame(content_frame, text="Image size (include)")
    size_frame.grid(row=8, column=0, columnspan=2, sticky=tk.W, pady=(0, 8))
    size_small_var = tk.BooleanVar(value=True)
    size_medium_var = tk.BooleanVar(value=True)
    size_large_var = tk.BooleanVar(value=True)
    ttk.Checkbutton(size_frame, text="Small (< 100 KB)", variable=size_small_var).pack(side=tk.LEFT, padx=(0, 12))
    ttk.Checkbutton(size_frame, text="Medium (100 KB – 1 MB)", variable=size_medium_var).pack(side=tk.LEFT, padx=(0, 12))
    ttk.Checkbutton(size_frame, text="Large (> 1 MB)", variable=size_large_var).pack(side=tk.LEFT)

    opts_frame = ttk.Frame(content_frame)
    opts_frame.grid(row=9, column=0, columnspan=2, sticky=tk.W, pady=(0, 8))
    suggested = suggest_aggressiveness()
    initial_params = get_aggressiveness_params("auto")
    delay_var = tk.DoubleVar(value=initial_params["delay"])
    workers_var = tk.IntVar(value=initial_params["workers"])
    ttk.Label(opts_frame, text="Delay (s):").pack(side=tk.LEFT)
    delay_spin = ttk.Spinbox(opts_frame, from_=0.25, to=10, increment=0.25, width=5, textvariable=delay_var)
    delay_spin.pack(side=tk.LEFT, padx=(4, 12))
    ttk.Label(opts_frame, text="Aggressiveness:").pack(side=tk.LEFT, padx=(8, 4))
    agg_var = tk.StringVar(value="auto")
    agg_combo = ttk.Combobox(
        opts_frame,
        textvariable=agg_var,
        values=("auto", "conservative", "balanced", "aggressive"),
        state="readonly",
        width=14,
    )
    agg_combo.pack(side=tk.LEFT, padx=(4, 12))

    def on_aggressiveness_change(*args: object) -> None:
        val = agg_var.get()
        if not val:
            return
        raw = "auto" if val.startswith("auto") else val
        params = get_aggressiveness_params(raw)
        workers_var.set(max(1, min(12, params["workers"])))
        delay_var.set(params["delay"])

    agg_var.trace_add("write", on_aggressiveness_change)
    agg_combo.set(f"auto ({suggested})")

    js_var = tk.BooleanVar(value=True)
    ttk.Checkbutton(
        opts_frame,
        text="Use JavaScript (needed for NYPL, etc.)",
        variable=js_var,
    ).pack(side=tk.LEFT, padx=(0, 8))
    flaresolverr_var = tk.BooleanVar(value=False)
    flaresolverr_url_var = tk.StringVar(value="")
    ttk.Checkbutton(
        opts_frame,
        text="FlareSolverr (Cloudflare bypass)",
        variable=flaresolverr_var,
    ).pack(side=tk.LEFT, padx=(0, 4))
    ttk.Label(opts_frame, text="URL:").pack(side=tk.LEFT, padx=(0, 2))
    flaresolverr_entry = ttk.Entry(opts_frame, textvariable=flaresolverr_url_var, width=22)
    flaresolverr_entry.pack(side=tk.LEFT, padx=(0, 8))
    crawl_var = tk.BooleanVar(value=True)
    ttk.Checkbutton(
        opts_frame,
        text="Follow links (crawl subtrees for images)",
        variable=crawl_var,
    ).pack(side=tk.LEFT, padx=(0, 8))
    depth_var = tk.IntVar(value=2)
    ttk.Label(opts_frame, text="Max depth:").pack(side=tk.LEFT, padx=(8, 0))
    depth_spin = ttk.Spinbox(opts_frame, from_=1, to=10, width=3, textvariable=depth_var)
    depth_spin.pack(side=tk.LEFT, padx=(4, 8))
    same_domain_var = tk.BooleanVar(value=True)
    ttk.Checkbutton(opts_frame, text="Same domain only", variable=same_domain_var).pack(side=tk.LEFT)
    ttk.Label(opts_frame, text="Workers:").pack(side=tk.LEFT, padx=(8, 0))
    workers_spin = ttk.Spinbox(opts_frame, from_=1, to=12, width=2, textvariable=workers_var)
    workers_spin.pack(side=tk.LEFT, padx=(4, 0))

    keep_awake_var = tk.BooleanVar(value=False)
    ttk.Checkbutton(
        content_frame,
        text="Keep system awake (for long scrapes)",
        variable=keep_awake_var,
    ).grid(row=10, column=0, columnspan=2, sticky=tk.W, pady=(4, 0))

    log_frame = ttk.LabelFrame(content_frame, text="Log")
    log_frame.grid(row=11, column=0, columnspan=2, sticky=tk.NSEW, pady=(0, 8))
    content_frame.columnconfigure(0, weight=1)
    content_frame.rowconfigure(11, weight=1)

    log_text = tk.Text(log_frame, height=8, wrap=tk.WORD, state=tk.DISABLED)
    log_scroll = ttk.Scrollbar(log_frame)
    log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    log_scroll.pack(side=tk.RIGHT, fill=tk.Y)
    log_text.config(yscrollcommand=log_scroll.set)
    log_scroll.config(command=log_text.yview)

    def append_log(line: str) -> None:
        log_text.config(state=tk.NORMAL)
        log_text.insert(tk.END, line)
        log_text.see(tk.END)
        log_text.config(state=tk.DISABLED)

    def clear_log() -> None:
        log_text.config(state=tk.NORMAL)
        log_text.delete("1.0", tk.END)
        log_text.config(state=tk.DISABLED)

    # Status bar and buttons (in bottom_frame, packed first so they stay visible when maximized)
    status_frame = ttk.Frame(bottom_frame)
    scan_status_var = tk.StringVar(value="")
    scrape_status_var = tk.StringVar(value="")
    status_frame.pack(side=tk.LEFT, fill=tk.X, expand=True)
    ttk.Label(status_frame, text="Scan:").pack(side=tk.LEFT, padx=(0, 4))
    ttk.Label(status_frame, textvariable=scan_status_var).pack(side=tk.LEFT, padx=(0, 16))
    ttk.Label(status_frame, text="Scrape:").pack(side=tk.LEFT, padx=(0, 4))
    ttk.Label(status_frame, textvariable=scrape_status_var).pack(side=tk.LEFT)

    output_queue: queue.Queue[str | None] = queue.Queue()
    current_proc: list[subprocess.Popen | None] = [None]
    current_procs: list[subprocess.Popen] = []
    procs_lock = threading.Lock()

    def run_scrape(scrape_btn_ref: tk.Widget, stop_btn_ref: tk.Widget) -> None:
        urls = [
            line.strip()
            for line in url_text.get("1.0", tk.END).splitlines()
            if line.strip()
        ]
        if not urls:
            append_log("Error: At least one URL is required.\n")
            return
        _save_last_urls(url_text.get("1.0", tk.END))
        scrape_btn_ref.config(state=tk.DISABLED)
        scan_status_var.set("Scanning resources...")
        scrape_status_var.set("—")
        scrape_counts: list[int] = [0, 0, 0]  # pdf, text, images
        run_parallel = urls_mode_var.get() == "parallel" and len(urls) > 1

        def update_status(line: str) -> None:
            if "Running:" in line or "Scrape:" in line or "Iteration" in line:
                scan_status_var.set("Scanning resources...")
            elif "Found:" in line:
                scan_status_var.set("Mapping complete")
            elif "→ Downloading" in line:
                scan_status_var.set("Downloading assets...")
            elif "  [" in line and "/" in line and "] " in line:
                # Parse [3/12] style progress (require "  " prefix to skip parallel URL prefix e.g. [1/3])
                scan_status_var.set("Downloading assets...")
                m = re.search(r"  \[(\d+)/(\d+)\]", line)
                if m:
                    scrape_status_var.set(f"{m.group(1)}/{m.group(2)} assets")
            elif "  Text:" in line:
                scrape_counts[1] += 1
                scan_status_var.set("Page loaded")
                scrape_status_var.set(f"{scrape_counts[0]} PDFs, {scrape_counts[1]} texts, {scrape_counts[2]} images")
            elif "  Image:" in line:
                scrape_counts[2] += 1
                scan_status_var.set("Page loaded")
                scrape_status_var.set(f"{scrape_counts[0]} PDFs, {scrape_counts[1]} texts, {scrape_counts[2]} images")
            elif "  PDF:" in line:
                scrape_counts[0] += 1
                scan_status_var.set("Page loaded")
                scrape_status_var.set(f"{scrape_counts[0]} PDFs, {scrape_counts[1]} texts, {scrape_counts[2]} images")
            elif "Done." in line:
                scan_status_var.set("Complete")
                scrape_status_var.set(f"{scrape_counts[0]} PDFs, {scrape_counts[1]} texts, {scrape_counts[2]} images")

        try:
            delay = float(delay_var.get())
        except (ValueError, tk.TclError):
            delay = 0.5
        try:
            depth = int(depth_var.get())
        except (ValueError, tk.TclError):
            depth = 2
        if getattr(sys, "frozen", False):
            base = os.path.dirname(sys.executable)
            strigil_bin = os.path.join(base, "strigil.exe" if sys.platform == "win32" else "strigil")
            base_cmd = [strigil_bin]
        else:
            base_cmd = [sys.executable, "-m", "strigil.cli"]
        selected_types = []
        if type_pdf_var.get():
            selected_types.append("pdf")
        if type_text_var.get():
            selected_types.append("text")
        if type_images_var.get():
            selected_types.append("images")
        if selected_types and len(selected_types) < 3:
            pass
        elif not selected_types:
            append_log("Error: Select at least one file type.\n")
            scrape_btn_ref.config(state=tk.NORMAL)
            return

        def _size_to_arg(n: int) -> str:
            if n >= 1024 * 1024:
                return f"{n // (1024 * 1024)}m"
            if n >= 1024:
                return f"{n // 1024}k"
            return str(n)

        lows: list[int] = []
        highs: list[int | None] = []
        if size_small_var.get():
            lows.append(0)
            highs.append(SIZE_SMALL_MAX)
        if size_medium_var.get():
            lows.append(SIZE_MEDIUM_MIN)
            highs.append(SIZE_MEDIUM_MAX)
        if size_large_var.get():
            lows.append(SIZE_LARGE_MIN)
            highs.append(None)  # no max
        if lows:
            low = min(lows)
            high = None if None in highs else max(h for h in highs if h is not None)
            if low > 0:
                base_cmd.extend(["--min-image-size", _size_to_arg(low)])
            if high is not None:
                base_cmd.extend(["--max-image-size", _size_to_arg(high)])
        if js_var.get():
            base_cmd.append("--js")
        if flaresolverr_var.get():
            url_opt = flaresolverr_url_var.get().strip()
            if url_opt:
                base_cmd.extend(["--flaresolverr", url_opt])
            else:
                base_cmd.append("--flaresolverr")
        if crawl_var.get():
            base_cmd.extend(["--crawl", "--max-depth", str(depth)])
            try:
                w = int(workers_var.get())
                w = max(1, min(12, w))
                base_cmd.extend(["--workers", str(w)])
            except (ValueError, tk.TclError):
                pass
            if same_domain_var.get():
                base_cmd.append("--same-domain-only")
        done_script = done_script_var.get().strip()
        if done_script:
            base_cmd.extend(["--done-script", done_script])
        if keep_awake_var.get():
            base_cmd.append("--keep-awake")

        def build_cmd(url_list: list[str]) -> list[str]:
            c = base_cmd + ["--url"] + url_list + [
                "--out-dir", out_var.get().strip() or "output",
                "--delay", str(delay),
            ]
            if selected_types and len(selected_types) < 3:
                c.extend(["--types"] + selected_types)
            lows: list[int] = []
            highs: list[int | None] = []
            if size_small_var.get():
                lows.append(0)
                highs.append(SIZE_SMALL_MAX)
            if size_medium_var.get():
                lows.append(SIZE_MEDIUM_MIN)
                highs.append(SIZE_MEDIUM_MAX)
            if size_large_var.get():
                lows.append(SIZE_LARGE_MIN)
                highs.append(None)
            if lows:
                low = min(lows)
                high = None if None in highs else max(h for h in highs if h is not None)
                if low > 0:
                    c.extend(["--min-image-size", _size_to_arg(low)])
                if high is not None:
                    c.extend(["--max-image-size", _size_to_arg(high)])
            if crawl_var.get():
                c.extend(["--crawl", "--max-depth", str(depth)])
                try:
                    w = int(workers_var.get())
                    w = max(1, min(12, w))
                    c.extend(["--workers", str(w)])
                except (ValueError, tk.TclError):
                    pass
                if same_domain_var.get():
                    c.append("--same-domain-only")
            done_script = done_script_var.get().strip()
            if done_script and not run_parallel:
                c.extend(["--done-script", done_script])
            if keep_awake_var.get():
                c.append("--keep-awake")
            return c

        if run_parallel:
            current_procs.clear()
            num_urls = len(urls)
            parallel_done_count: list[int] = [0]

            def parallel_worker(idx: int, url: str) -> None:
                cmd = build_cmd([url])
                try:
                    proc = subprocess.Popen(
                        cmd,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                        bufsize=1,
                    )
                    with procs_lock:
                        current_procs.append(proc)
                    prefix = f"[{idx + 1}/{num_urls}] "
                    if proc.stdout:
                        for line in proc.stdout:
                            output_queue.put(prefix + line)
                except Exception as e:
                    output_queue.put(f"[{idx + 1}/{num_urls}] Error: {e}\n")
                finally:
                    output_queue.put(None)

            append_log(f"Running {num_urls} URLs in parallel.\n\n")
            stop_btn.config(state=tk.NORMAL)
            for i, u in enumerate(urls):
                t = threading.Thread(target=parallel_worker, args=(i, u), daemon=True)
                t.start()

            def poll_queue_parallel(btn: tk.Widget) -> None:
                try:
                    while True:
                        line = output_queue.get_nowait()
                        if line is None:
                            parallel_done_count[0] += 1
                            if parallel_done_count[0] >= num_urls:
                                break
                        else:
                            root.after(0, lambda l=line: (append_log(l), update_status(l)))
                except queue.Empty:
                    pass
                if parallel_done_count[0] >= num_urls:
                    ds = done_script_var.get().strip()
                    if ds:
                        root.after(0, lambda: run_done_script_async(ds, out_var.get().strip() or "output"))
                    root.after(0, lambda: (stop_btn_ref.config(state=tk.DISABLED), btn.config(state=tk.NORMAL)))
                else:
                    root.after(150, lambda: poll_queue_parallel(btn))

            def run_done_script_async(script: str, out: str) -> None:
                if not script:
                    return
                import subprocess as sp
                cmd = script.strip().replace("{out_dir}", os.path.abspath(out))
                try:
                    sp.run(cmd, shell=True, check=False)
                except Exception:
                    pass

            poll_queue_parallel(scrape_btn_ref)
        else:
            cmd = build_cmd(urls)

            def worker() -> None:
                try:
                    proc = subprocess.Popen(
                        cmd,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                        bufsize=1,
                    )
                    current_proc[0] = proc
                    if proc.stdout:
                        for line in proc.stdout:
                            output_queue.put(line)
                except Exception as e:
                    output_queue.put(f"Error: {e}\n")
                finally:
                    current_proc[0] = None
                output_queue.put(None)

            def poll_queue(btn: tk.Widget) -> None:
                saw_done = False
                try:
                    while True:
                        line = output_queue.get_nowait()
                        if line is None:
                            saw_done = True
                            break
                        root.after(0, lambda l=line: (append_log(l), update_status(l)))
                except queue.Empty:
                    pass
                if saw_done:
                    root.after(0, lambda: (stop_btn_ref.config(state=tk.DISABLED), btn.config(state=tk.NORMAL)))
                else:
                    root.after(150, lambda: poll_queue(btn))

            append_log(f"Running: {' '.join(cmd)}\n\n")
            stop_btn.config(state=tk.NORMAL)
            threading.Thread(target=worker, daemon=True).start()
            poll_queue(scrape_btn_ref)

    btn_frame = ttk.Frame(bottom_frame)
    btn_frame.pack(side=tk.RIGHT)

    def do_stop() -> None:
        with procs_lock:
            procs = list(current_procs)
            current_procs.clear()
        for p in procs:
            try:
                p.terminate()
            except Exception:
                pass
        if current_proc[0] is not None:
            try:
                current_proc[0].terminate()
            except Exception:
                pass
            output_queue.put(None)

    scrape_btn = ttk.Button(btn_frame, text="Scrape")
    stop_btn = ttk.Button(btn_frame, text="Stop", command=do_stop, state=tk.DISABLED)
    scrape_btn.config(command=lambda: run_scrape(scrape_btn, stop_btn))
    scrape_btn.pack(side=tk.LEFT, padx=(0, 8))
    stop_btn.pack(side=tk.LEFT, padx=(0, 8))
    ttk.Button(btn_frame, text="Clear log", command=clear_log).pack(side=tk.LEFT)

    root.mainloop()


if __name__ == "__main__":
    main()
