"""Minimal GUI for the basic scraper. Run with: scrape-gui"""

import os
import queue
import re
import subprocess
import sys
import threading
from pathlib import Path

import tkinter as tk
from tkinter import ttk

from web_scraper._deps import check_required, ensure_optional
from web_scraper.hardware import (
    default_workers,
    get_aggressiveness_params,
    suggest_aggressiveness,
)

LAST_URLS_FILE = Path.home() / ".basic-scraper" / "last_urls.txt"


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
    root.title("Basic Scraper")
    root.minsize(400, 320)

    main_frame = ttk.Frame(root, padding=10)
    main_frame.pack(fill=tk.BOTH, expand=True)

    ttk.Label(main_frame, text="URLs (one per line)").grid(row=0, column=0, sticky=tk.NW, pady=(0, 2))
    url_text = tk.Text(main_frame, height=4, width=50, wrap=tk.WORD)
    url_text.insert("1.0", _load_last_urls())
    url_text.grid(row=1, column=0, columnspan=2, sticky=tk.EW, pady=(0, 8))

    ttk.Label(main_frame, text="Output directory").grid(row=2, column=0, sticky=tk.W, pady=(0, 2))
    out_row = ttk.Frame(main_frame)
    out_row.grid(row=3, column=0, columnspan=2, sticky=tk.EW, pady=(0, 8))
    main_frame.columnconfigure(0, weight=1)
    out_var = tk.StringVar(value="output")
    out_entry = ttk.Entry(out_row, textvariable=out_var, width=50)
    out_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8))
    ttk.Button(out_row, text="Open folder", command=lambda: _open_folder(out_var.get())).pack(side=tk.LEFT)

    ttk.Label(main_frame, text="Done script (optional, use {out_dir})").grid(row=4, column=0, sticky=tk.W, pady=(8, 2))
    done_script_var = tk.StringVar(value="")
    done_script_entry = ttk.Entry(main_frame, textvariable=done_script_var, width=50)
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
        crawl_var.set(True)
        depth_var.set(2)
        same_domain_var.set(True)

    suggest_cb = ttk.Checkbutton(
        main_frame,
        text="Suggest likely choices",
        variable=suggest_var,
        command=apply_suggested,
    )
    suggest_cb.grid(row=6, column=0, columnspan=2, sticky=tk.W, pady=(0, 8))

    types_frame = ttk.LabelFrame(main_frame, text="File types")
    types_frame.grid(row=7, column=0, columnspan=2, sticky=tk.W, pady=(0, 8))
    type_pdf_var = tk.BooleanVar(value=True)
    type_text_var = tk.BooleanVar(value=True)
    type_images_var = tk.BooleanVar(value=True)
    ttk.Checkbutton(types_frame, text="PDF", variable=type_pdf_var).pack(side=tk.LEFT, padx=(0, 12))
    ttk.Checkbutton(types_frame, text="Text", variable=type_text_var).pack(side=tk.LEFT, padx=(0, 12))
    ttk.Checkbutton(types_frame, text="Images", variable=type_images_var).pack(side=tk.LEFT)

    size_frame = ttk.LabelFrame(main_frame, text="Image size (include)")
    size_frame.grid(row=8, column=0, columnspan=2, sticky=tk.W, pady=(0, 8))
    size_small_var = tk.BooleanVar(value=True)
    size_medium_var = tk.BooleanVar(value=True)
    size_large_var = tk.BooleanVar(value=True)
    ttk.Checkbutton(size_frame, text="Small (< 100 KB)", variable=size_small_var).pack(side=tk.LEFT, padx=(0, 12))
    ttk.Checkbutton(size_frame, text="Medium (100 KB – 1 MB)", variable=size_medium_var).pack(side=tk.LEFT, padx=(0, 12))
    ttk.Checkbutton(size_frame, text="Large (> 1 MB)", variable=size_large_var).pack(side=tk.LEFT)

    opts_frame = ttk.Frame(main_frame)
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
        main_frame,
        text="Keep system awake (for long scrapes)",
        variable=keep_awake_var,
    ).grid(row=10, column=0, columnspan=2, sticky=tk.W, pady=(4, 0))

    log_frame = ttk.LabelFrame(main_frame, text="Log")
    log_frame.grid(row=11, column=0, columnspan=2, sticky=tk.NSEW, pady=(0, 8))
    main_frame.columnconfigure(0, weight=1)
    main_frame.rowconfigure(11, weight=1)

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

    # Status bar: scanning + scraping
    status_frame = ttk.Frame(main_frame)
    status_frame.grid(row=12, column=0, columnspan=2, sticky=tk.EW, pady=(0, 4))
    main_frame.columnconfigure(0, weight=1)
    scan_status_var = tk.StringVar(value="")
    scrape_status_var = tk.StringVar(value="")
    ttk.Label(status_frame, text="Scan:").pack(side=tk.LEFT, padx=(0, 4))
    ttk.Label(status_frame, textvariable=scan_status_var).pack(side=tk.LEFT, padx=(0, 16))
    ttk.Label(status_frame, text="Scrape:").pack(side=tk.LEFT, padx=(0, 4))
    ttk.Label(status_frame, textvariable=scrape_status_var).pack(side=tk.LEFT)

    output_queue: queue.Queue[str | None] = queue.Queue()
    current_proc: list[subprocess.Popen | None] = [None]

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

        def update_status(line: str) -> None:
            if "Running:" in line or "Scrape:" in line or "Iteration" in line:
                scan_status_var.set("Scanning resources...")
            elif "Found:" in line:
                scan_status_var.set("Mapping complete")
            elif "→ Downloading" in line:
                scan_status_var.set("Downloading assets...")
            elif "  [" in line and "/" in line and "] " in line:
                # Parse [3/12] style progress
                scan_status_var.set("Downloading assets...")
                m = re.search(r"\[(\d+)/(\d+)\]", line)
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
            scrape_bin = os.path.join(base, "scrape.exe" if sys.platform == "win32" else "scrape")
            cmd = [scrape_bin]
        else:
            cmd = [sys.executable, "-m", "web_scraper.cli"]
        cmd.extend(["--url"] + urls)
        cmd.extend([
            "--out-dir", out_var.get().strip() or "output",
            "--delay", str(delay),
        ])
        selected_types = []
        if type_pdf_var.get():
            selected_types.append("pdf")
        if type_text_var.get():
            selected_types.append("text")
        if type_images_var.get():
            selected_types.append("images")
        if selected_types and len(selected_types) < 3:
            cmd.append("--types")
            cmd.extend(selected_types)
        elif not selected_types:
            append_log("Error: Select at least one file type.\n")
            scrape_btn_ref.config(state=tk.NORMAL)
            return
        # Union of selected size ranges -> --min-image-size / --max-image-size
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
                cmd.extend(["--min-image-size", _size_to_arg(low)])
            if high is not None:
                cmd.extend(["--max-image-size", _size_to_arg(high)])
        if crawl_var.get():
            cmd.extend(["--crawl", "--max-depth", str(depth)])
            try:
                w = int(workers_var.get())
                w = max(1, min(12, w))
                cmd.extend(["--workers", str(w)])
            except (ValueError, tk.TclError):
                pass
            if same_domain_var.get():
                cmd.append("--same-domain-only")
        done_script = done_script_var.get().strip()
        if done_script:
            cmd.extend(["--done-script", done_script])
        if keep_awake_var.get():
            cmd.append("--keep-awake")

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

    btn_frame = ttk.Frame(main_frame)
    btn_frame.grid(row=13, column=0, columnspan=2)

    def do_stop() -> None:
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
