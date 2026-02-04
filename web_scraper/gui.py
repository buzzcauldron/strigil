"""Minimal GUI for the basic scraper. Run with: scrape-gui"""

import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import ttk

from web_scraper._deps import check_required


def main() -> None:
    check_required()
    root = tk.Tk()
    root.title("Basic Scraper")
    root.minsize(400, 320)

    main_frame = ttk.Frame(root, padding=10)
    main_frame.pack(fill=tk.BOTH, expand=True)

    ttk.Label(main_frame, text="URL").grid(row=0, column=0, sticky=tk.W, pady=(0, 2))
    url_var = tk.StringVar(value="https://example.com")
    url_entry = ttk.Entry(main_frame, textvariable=url_var, width=50)
    url_entry.grid(row=1, column=0, columnspan=2, sticky=tk.EW, pady=(0, 8))

    ttk.Label(main_frame, text="Output directory").grid(row=2, column=0, sticky=tk.W, pady=(0, 2))
    out_var = tk.StringVar(value="output")
    out_entry = ttk.Entry(main_frame, textvariable=out_var, width=50)
    out_entry.grid(row=3, column=0, columnspan=2, sticky=tk.EW, pady=(0, 8))

    opts_frame = ttk.Frame(main_frame)
    opts_frame.grid(row=4, column=0, columnspan=2, sticky=tk.W, pady=(0, 8))
    delay_var = tk.DoubleVar(value=1.0)
    ttk.Label(opts_frame, text="Delay (s):").pack(side=tk.LEFT)
    delay_spin = ttk.Spinbox(opts_frame, from_=0.5, to=10, increment=0.5, width=5, textvariable=delay_var)
    delay_spin.pack(side=tk.LEFT, padx=(4, 12))
    crawl_var = tk.BooleanVar(value=False)
    ttk.Checkbutton(opts_frame, text="Crawl links", variable=crawl_var).pack(side=tk.LEFT, padx=(0, 8))
    depth_var = tk.IntVar(value=2)
    ttk.Label(opts_frame, text="Max depth:").pack(side=tk.LEFT, padx=(8, 0))
    depth_spin = ttk.Spinbox(opts_frame, from_=1, to=10, width=3, textvariable=depth_var)
    depth_spin.pack(side=tk.LEFT, padx=(4, 8))
    same_domain_var = tk.BooleanVar(value=True)
    ttk.Checkbutton(opts_frame, text="Same domain only", variable=same_domain_var).pack(side=tk.LEFT)

    log_frame = ttk.LabelFrame(main_frame, text="Log")
    log_frame.grid(row=5, column=0, columnspan=2, sticky=tk.NSEW, pady=(0, 8))
    main_frame.columnconfigure(0, weight=1)
    main_frame.rowconfigure(5, weight=1)

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

    output_queue: queue.Queue[str | None] = queue.Queue()

    btn_frame = ttk.Frame(main_frame)
    btn_frame.grid(row=6, column=0, columnspan=2)
    scrape_btn = ttk.Button(btn_frame, text="Scrape", command=run_scrape)

    def run_scrape() -> None:
        url = url_var.get().strip()
        if not url:
            append_log("Error: URL is required.\n")
            return
        scrape_btn.config(state=tk.DISABLED)
        try:
            delay = float(delay_var.get())
        except (ValueError, tk.TclError):
            delay = 1.0
        try:
            depth = int(depth_var.get())
        except (ValueError, tk.TclError):
            depth = 2
        if getattr(sys, "frozen", False):
            base = os.path.dirname(sys.executable)
            scrape_bin = os.path.join(base, "scrape.exe" if sys.platform == "win32" else "scrape")
            cmd = [
                scrape_bin,
                "--url",
                url,
            ]
        else:
            cmd = [
                sys.executable,
                "-m",
                "web_scraper.cli",
                "--url",
                url,
            ]
        cmd.extend([
            "--out-dir", out_var.get().strip() or "output",
            "--delay", str(delay),
        ])
        if crawl_var.get():
            cmd.extend(["--crawl", "--max-depth", str(depth)])
            if same_domain_var.get():
                cmd.append("--same-domain-only")

        def worker() -> None:
            try:
                proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                )
                if proc.stdout:
                    for line in proc.stdout:
                        output_queue.put(line)
            except Exception as e:
                output_queue.put(f"Error: {e}\n")
            output_queue.put(None)

        def poll_queue() -> None:
            saw_done = False
            try:
                while True:
                    line = output_queue.get_nowait()
                    if line is None:
                        saw_done = True
                        break
                    root.after(0, lambda l=line: append_log(l))
            except queue.Empty:
                pass
            if saw_done:
                root.after(0, lambda: scrape_btn.config(state=tk.NORMAL))
            else:
                root.after(100, poll_queue)

        append_log(f"Running: {' '.join(cmd)}\n\n")
        threading.Thread(target=worker, daemon=True).start()
        poll_queue()

    scrape_btn.pack(side=tk.LEFT, padx=(0, 8))
    ttk.Button(btn_frame, text="Clear log", command=lambda: (log_text.config(state=tk.NORMAL), log_text.delete("1.0", tk.END), log_text.config(state=tk.DISABLED))).pack(side=tk.LEFT)

    root.mainloop()


if __name__ == "__main__":
    main()
