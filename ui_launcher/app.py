"""
Playwright Test Executor — Main UI Application
A lightweight local launcher for the p13n-marketing-experiences-qa-automation framework.
Uses Tkinter (stdlib) — no additional pip dependencies required.
"""

import os
import sys
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext
import tkinter as tk
from tkinter import ttk

# Ensure the project root is on sys.path so imports work both when the file is
# run directly and when invoked via "python -m ui_launcher".
_PROJECT_ROOT = Path(__file__).parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from ui_launcher.config_reader import ConfigReader
from ui_launcher.command_builder import CommandBuilder
from ui_launcher.report_resolver import ReportResolver
from ui_launcher.runner import TestRunner
from ui_launcher.test_discovery import TestDiscovery

VERSION = "1.0.0"

# ── Colour palette (dark terminal + light chrome) ─────────────────────────────
_DARK_BG   = "#1e1e1e"
_DARK_FG   = "#d4d4d4"
_GREEN     = "#27ae60"
_GREEN_ACT = "#229954"
_RED       = "#e74c3c"
_RED_ACT   = "#c0392b"
_ORANGE    = "#e67e22"
_HEADER_BG = "#2c3e50"


class PlaywrightExecutorApp:
    """Main application window."""

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title(f"Playwright Test Executor  v{VERSION}")

        self._cfg_reader = ConfigReader()
        self._cfg = self._cfg_reader.load()

        self.root.geometry(self._cfg.get("window_geometry", "1250x820"))
        self.root.minsize(1000, 680)

        # Runtime state
        self._runner: TestRunner | None = None
        self._is_running = False
        self._last_report: str | None = None
        self._test_tree: dict = {}

        self._setup_styles()
        self._build_ui()
        self._load_defaults()
        self._discover_tests()

    # ── Styles ─────────────────────────────────────────────────────────────────

    def _setup_styles(self) -> None:
        s = ttk.Style()
        s.theme_use("clam")
        s.configure("Section.TLabel", font=("Segoe UI", 9, "bold"), foreground="#2c3e50")
        s.configure("Dim.TLabel",    font=("Segoe UI", 8),          foreground="#7f8c8d")
        s.configure("TLabelframe.Label", font=("Segoe UI", 9, "bold"))

    # ── UI Construction ────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        self._build_header()
        self._build_body()
        self._build_statusbar()

    def _build_header(self) -> None:
        hdr = tk.Frame(self.root, bg=_HEADER_BG, pady=10, padx=18)
        hdr.pack(fill="x")
        tk.Label(
            hdr, text="Playwright Test Executor",
            font=("Segoe UI", 15, "bold"), fg="white", bg=_HEADER_BG,
        ).pack(side="left")
        tk.Label(
            hdr, text="  —  P13n Marketing Experiences QA Automation",
            font=("Segoe UI", 10), fg="#95a5a6", bg=_HEADER_BG,
        ).pack(side="left")
        tk.Label(
            hdr, text=f"v{VERSION}",
            font=("Segoe UI", 9), fg="#7f8c8d", bg=_HEADER_BG,
        ).pack(side="right")

    def _build_body(self) -> None:
        body = ttk.Frame(self.root, padding="8")
        body.pack(fill="both", expand=True)

        left = ttk.LabelFrame(body, text="Configuration", padding="10")
        left.pack(side="left", fill="y", padx=(0, 6))
        left.pack_propagate(False)
        left.configure(width=440)

        right = ttk.LabelFrame(body, text="Output Log", padding="4")
        right.pack(side="right", fill="both", expand=True)

        self._build_left_panel(left)
        self._build_right_panel(right)

    def _build_left_panel(self, parent: ttk.LabelFrame) -> None:
        r = 0

        # ── Repo root ──────────────────────────────────────────────────────
        ttk.Label(parent, text="Framework Repository Root", style="Section.TLabel").grid(
            row=r, column=0, columnspan=3, sticky="w", pady=(0, 3)); r += 1

        self._repo_var = tk.StringVar()
        ttk.Entry(parent, textvariable=self._repo_var, width=36).grid(
            row=r, column=0, columnspan=2, sticky="ew", pady=2)
        ttk.Button(parent, text="Browse…", command=self._browse_repo).grid(
            row=r, column=2, sticky="ew", padx=(4, 0)); r += 1

        ttk.Label(
            parent,
            text="Must point to the root of p13n-marketing-experiences-qa-automation",
            style="Dim.TLabel",
        ).grid(row=r, column=0, columnspan=3, sticky="w"); r += 1

        ttk.Separator(parent, orient="horizontal").grid(
            row=r, column=0, columnspan=3, sticky="ew", pady=6); r += 1

        # ── Test selection ─────────────────────────────────────────────────
        ttk.Label(parent, text="Test Selection", style="Section.TLabel").grid(
            row=r, column=0, columnspan=3, sticky="w", pady=(0, 3)); r += 1

        ttk.Label(parent, text="Suite / Folder:").grid(row=r, column=0, sticky="w", pady=2)
        self._suite_var = tk.StringVar(value="All Tests")
        self._suite_cb = ttk.Combobox(
            parent, textvariable=self._suite_var, state="readonly", width=30)
        self._suite_cb.grid(row=r, column=1, columnspan=2, sticky="ew", pady=2)
        self._suite_cb.bind("<<ComboboxSelected>>", self._on_suite_change); r += 1

        ttk.Label(parent, text="Test File:").grid(row=r, column=0, sticky="w", pady=2)
        self._file_var = tk.StringVar(value="All in Suite")
        self._file_cb = ttk.Combobox(
            parent, textvariable=self._file_var, state="readonly", width=30)
        self._file_cb.grid(row=r, column=1, columnspan=2, sticky="ew", pady=2); r += 1

        ttk.Label(parent, text="Name filter (-k):").grid(row=r, column=0, sticky="w", pady=2)
        self._k_var = tk.StringVar()
        ttk.Entry(parent, textvariable=self._k_var, width=30).grid(
            row=r, column=1, columnspan=2, sticky="ew", pady=2); r += 1

        ttk.Separator(parent, orient="horizontal").grid(
            row=r, column=0, columnspan=3, sticky="ew", pady=6); r += 1

        # ── Execution options ──────────────────────────────────────────────
        ttk.Label(parent, text="Execution Options", style="Section.TLabel").grid(
            row=r, column=0, columnspan=3, sticky="w", pady=(0, 3)); r += 1

        ttk.Label(parent, text="Browser:").grid(row=r, column=0, sticky="w", pady=2)
        self._browser_var = tk.StringVar()
        self._browser_cb = ttk.Combobox(
            parent, textvariable=self._browser_var, state="readonly", width=20)
        self._browser_cb.grid(row=r, column=1, columnspan=2, sticky="ew", pady=2); r += 1

        ttk.Label(parent, text="Marker (-m):").grid(row=r, column=0, sticky="w", pady=2)
        self._marker_var = tk.StringVar(value="(none)")
        self._marker_cb = ttk.Combobox(
            parent, textvariable=self._marker_var, state="readonly", width=20)
        self._marker_cb.grid(row=r, column=1, columnspan=2, sticky="ew", pady=2); r += 1

        ttk.Label(parent, text="Workers (-n):").grid(row=r, column=0, sticky="w", pady=2)
        self._workers_var = tk.IntVar(value=1)
        ttk.Spinbox(parent, from_=1, to=16, textvariable=self._workers_var, width=5).grid(
            row=r, column=1, sticky="w", pady=2); r += 1

        # Checkboxes row
        chk = ttk.Frame(parent)
        chk.grid(row=r, column=0, columnspan=3, sticky="w", pady=4); r += 1
        self._headed_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(chk, text="Headed", variable=self._headed_var).pack(side="left", padx=(0, 12))
        self._verbose_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(chk, text="Verbose (-v)", variable=self._verbose_var).pack(side="left", padx=(0, 12))
        self._auto_open_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(chk, text="Auto-open Report", variable=self._auto_open_var).pack(side="left")

        # .env file selector
        ttk.Label(parent, text=".env File:").grid(row=r, column=0, sticky="w", pady=2)
        self._env_file_var = tk.StringVar()
        ttk.Entry(parent, textvariable=self._env_file_var, width=24).grid(
            row=r, column=1, sticky="ew", pady=2)
        ttk.Button(parent, text="Browse…", command=self._browse_env).grid(
            row=r, column=2, sticky="ew", padx=(4, 0)); r += 1
        ttk.Label(
            parent,
            text="Selects the environment (qa, uat, …) for this run",
            style="Dim.TLabel",
        ).grid(row=r, column=0, columnspan=3, sticky="w"); r += 1

        # Extra args
        ttk.Label(parent, text="Extra Args:").grid(row=r, column=0, sticky="w", pady=2)
        self._extra_var = tk.StringVar()
        ttk.Entry(parent, textvariable=self._extra_var, width=30).grid(
            row=r, column=1, columnspan=2, sticky="ew", pady=2); r += 1

        ttk.Separator(parent, orient="horizontal").grid(
            row=r, column=0, columnspan=3, sticky="ew", pady=6); r += 1

        # ── Action buttons ─────────────────────────────────────────────────
        btns = ttk.Frame(parent)
        btns.grid(row=r, column=0, columnspan=3, pady=6); r += 1

        self._run_btn = tk.Button(
            btns, text="▶  RUN",
            font=("Segoe UI", 11, "bold"), bg=_GREEN, fg="white",
            activebackground=_GREEN_ACT, activeforeground="white",
            relief="flat", padx=22, pady=7, command=self._run_tests, cursor="hand2",
        )
        self._run_btn.pack(side="left", padx=4)

        self._stop_btn = tk.Button(
            btns, text="■  STOP",
            font=("Segoe UI", 11, "bold"), bg=_RED, fg="white",
            activebackground=_RED_ACT, activeforeground="white",
            relief="flat", padx=22, pady=7, command=self._stop_tests,
            state="disabled", cursor="hand2",
        )
        self._stop_btn.pack(side="left", padx=4)

        tk.Button(
            btns, text="Clear Log",
            font=("Segoe UI", 9), relief="flat", padx=12, pady=7,
            command=self._clear_log,
        ).pack(side="left", padx=4)

        # ── Report section ─────────────────────────────────────────────────
        rpt = ttk.LabelFrame(parent, text="Report", padding="8")
        rpt.grid(row=r, column=0, columnspan=3, sticky="ew", pady=4); r += 1

        self._report_status_var = tk.StringVar(value="No report yet — run tests first")
        ttk.Label(rpt, textvariable=self._report_status_var, style="Dim.TLabel").pack(anchor="w")

        rpt_btns = ttk.Frame(rpt)
        rpt_btns.pack(anchor="w", pady=(6, 0))
        self._open_rpt_btn = ttk.Button(
            rpt_btns, text="Open Latest Report", command=self._open_report, state="disabled")
        self._open_rpt_btn.pack(side="left", padx=(0, 8))
        ttk.Button(rpt_btns, text="Refresh", command=self._refresh_report).pack(side="left")

        # ── Refresh test list ──────────────────────────────────────────────
        ttk.Button(
            parent, text="↻  Refresh Test List", command=self._discover_tests,
        ).grid(row=r, column=0, columnspan=3, pady=(8, 0))

        parent.columnconfigure(1, weight=1)

    def _build_right_panel(self, parent: ttk.LabelFrame) -> None:
        self._log = scrolledtext.ScrolledText(
            parent,
            font=("Consolas", 9),
            bg=_DARK_BG, fg=_DARK_FG,
            insertbackground="white",
            selectbackground="#264f78",
            wrap="word",
            state="disabled",
        )
        self._log.pack(fill="both", expand=True)

        # Colour tags used by _append_line
        self._log.tag_config("cmd",       foreground="#c586c0")
        self._log.tag_config("sep",       foreground="#444444")
        self._log.tag_config("passed",    foreground="#4ec9b0")
        self._log.tag_config("failed",    foreground="#f14c4c")
        self._log.tag_config("error",     foreground="#f14c4c")
        self._log.tag_config("warning",   foreground="#cca700")
        self._log.tag_config("info",      foreground="#9cdcfe")
        self._log.tag_config("highlight", foreground="#dcdcaa")

    def _build_statusbar(self) -> None:
        sb = tk.Frame(self.root, bg="#ecf0f1", pady=3)
        sb.pack(fill="x", side="bottom")

        self._status_lbl = tk.Label(
            sb, text="● IDLE",
            font=("Segoe UI", 9, "bold"), fg="#7f8c8d", bg="#ecf0f1", padx=10,
        )
        self._status_lbl.pack(side="left")

        self._cmd_lbl = tk.Label(
            sb, text="",
            font=("Courier", 8), fg="#95a5a6", bg="#ecf0f1", anchor="w",
        )
        self._cmd_lbl.pack(side="left", fill="x", expand=True, padx=4)

    # ── Default values ─────────────────────────────────────────────────────────

    def _load_defaults(self) -> None:
        self._repo_var.set(self._cfg.get("repo_root", ""))

        browsers = self._cfg.get("browsers", ["chromium", "firefox", "webkit"])
        self._browser_cb["values"] = browsers
        self._browser_var.set(self._cfg.get("default_browser", browsers[0]))

        markers = ["(none)"] + self._cfg.get("markers", [])
        self._marker_cb["values"] = markers
        self._marker_var.set("(none)")

        self._workers_var.set(self._cfg.get("default_workers", 1))
        self._auto_open_var.set(self._cfg.get("auto_open_report", False))

        # Auto-detect the .env in the repo root
        repo = self._cfg.get("repo_root", "")
        if repo:
            default_env = Path(repo) / ".env"
            if default_env.exists():
                self._env_file_var.set(str(default_env))

    # ── Test discovery ─────────────────────────────────────────────────────────

    def _discover_tests(self) -> None:
        repo = self._repo_var.get().strip()
        if not repo or not os.path.isdir(repo):
            self._log_msg(
                "⚠  Repo root not set or not found.  Use Browse… to select it.", "warning")
            return

        try:
            disc = TestDiscovery(repo)
            self._test_tree = disc.discover()

            # Merge markers from conftest with those in config
            discovered_markers = disc.discover_markers()
            existing = list(self._cfg.get("markers", []))
            for m in discovered_markers:
                if m not in existing:
                    existing.append(m)
            self._marker_cb["values"] = ["(none)"] + existing

            suites = ["All Tests"] + sorted(self._test_tree.keys())
            self._suite_cb["values"] = suites
            self._suite_var.set("All Tests")
            self._on_suite_change()

            total = sum(len(v) for v in self._test_tree.values())
            self._log_msg(
                f"✓  Discovered {total} test files in {len(self._test_tree)} suite(s).", "passed")

            # Also scan for .env files to hint the user
            env_files = disc.discover_env_files()
            if len(env_files) > 1:
                names = "  |  ".join(Path(f).name for f in env_files)
                self._log_msg(f"ℹ  Environment files found: {names}", "info")

        except FileNotFoundError as exc:
            self._log_msg(f"✗  {exc}", "error")
        except Exception as exc:
            self._log_msg(f"✗  Test discovery failed: {exc}", "error")

    def _on_suite_change(self, _event=None) -> None:
        suite = self._suite_var.get()
        if suite == "All Tests":
            self._file_cb["values"] = ["All in Suite"]
        else:
            files = self._test_tree.get(suite, [])
            self._file_cb["values"] = ["All in Suite"] + [Path(f).name for f in files]
        self._file_var.set("All in Suite")

    # ── File dialogs ───────────────────────────────────────────────────────────

    def _browse_repo(self) -> None:
        path = filedialog.askdirectory(title="Select Framework Repository Root")
        if not path:
            return
        self._repo_var.set(path)
        env_candidate = Path(path) / ".env"
        if env_candidate.exists():
            self._env_file_var.set(str(env_candidate))
        self._discover_tests()

    def _browse_env(self) -> None:
        repo = self._repo_var.get().strip() or "/"
        path = filedialog.askopenfilename(
            title="Select .env file",
            initialdir=repo,
            filetypes=[("Env files", "*.env .env*"), ("All files", "*.*")],
        )
        if path:
            self._env_file_var.set(path)

    # ── Run / Stop ─────────────────────────────────────────────────────────────

    def _run_tests(self) -> None:
        if self._is_running:
            return

        repo = self._repo_var.get().strip()
        if not repo or not os.path.isdir(repo):
            messagebox.showerror(
                "Repository Not Found",
                "The framework repository root does not exist.\n"
                "Use Browse… to locate it.",
            )
            return

        try:
            cmd, env_overrides = self._build_command()
        except Exception as exc:
            messagebox.showerror("Command Error", str(exc))
            return

        self._clear_log()
        self._log_msg(f"$ {' '.join(cmd)}", "cmd")
        self._log_msg("─" * 72, "sep")

        self._is_running = True
        self._run_btn.config(state="disabled")
        self._stop_btn.config(state="normal")
        self._report_status_var.set("Waiting for test completion…")
        self._open_rpt_btn.config(state="disabled")
        self._set_status("RUNNING", _ORANGE)
        self._cmd_lbl.config(text=" ".join(cmd[:10]) + (" …" if len(cmd) > 10 else ""))

        self._runner = TestRunner(
            cmd=cmd,
            cwd=repo,
            env_overrides=env_overrides,
            on_output=self._on_output,
            on_finish=self._on_finish,
        )
        self._runner.start()

    def _build_command(self) -> tuple[list, dict]:
        repo = Path(self._repo_var.get().strip())
        builder = CommandBuilder(str(repo))

        cmd = builder.build(
            suite=self._suite_var.get(),
            file_sel=self._file_var.get(),
            k_filter=self._k_var.get().strip(),
            browser=self._browser_var.get().strip(),
            marker=self._marker_var.get().strip() if self._marker_var.get() != "(none)" else None,
            workers=self._workers_var.get(),
            verbose=self._verbose_var.get(),
            headed=self._headed_var.get(),
            extra=self._extra_var.get().strip(),
            test_tree=self._test_tree,
        )

        env_overrides: dict = {}
        env_file = self._env_file_var.get().strip()
        if env_file and os.path.isfile(env_file):
            # Make it available so custom test setup can pick it up if needed.
            env_overrides["EXECUTOR_ENV_FILE"] = env_file
        return cmd, env_overrides

    def _stop_tests(self) -> None:
        if self._runner and self._is_running:
            self._runner.stop()
            self._log_msg("\n⚠  Stop requested — terminating…", "warning")
            self._set_status("CANCELLING", "#f39c12")

    # ── Runner callbacks ───────────────────────────────────────────────────────

    def _on_output(self, line: str) -> None:
        # Called from a background thread — schedule on the main thread.
        self.root.after(0, self._append_line, line)

    def _on_finish(self, exit_code: int, cancelled: bool) -> None:
        self.root.after(0, self._handle_finish, exit_code, cancelled)

    def _handle_finish(self, exit_code: int, cancelled: bool) -> None:
        self._is_running = False
        self._run_btn.config(state="normal")
        self._stop_btn.config(state="disabled")
        self._log_msg("─" * 72, "sep")

        if cancelled:
            self._log_msg("⚠  Execution cancelled by user.", "warning")
            self._set_status("CANCELLED", "#f39c12")
        elif exit_code == 0:
            self._log_msg("✓  All tests passed!", "passed")
            self._set_status("PASSED", _GREEN)
        else:
            self._log_msg(f"✗  Tests finished with failures  (exit code {exit_code}).", "failed")
            self._set_status("FAILED", _RED)

        self._refresh_report()

        if not cancelled and self._auto_open_var.get() and self._last_report:
            self._open_report()

    # ── Report ─────────────────────────────────────────────────────────────────

    def _refresh_report(self) -> None:
        repo = self._repo_var.get().strip()
        if not repo:
            return
        resolver = ReportResolver(repo, self._cfg.get("report_paths", []))
        path = resolver.find_latest()
        if path:
            self._last_report = path
            rel = os.path.relpath(path, repo)
            self._report_status_var.set(f"✓  {rel}")
            self._open_rpt_btn.config(state="normal")
        else:
            self._last_report = None
            self._report_status_var.set("No report found in allure/reports/")
            self._open_rpt_btn.config(state="disabled")

    def _open_report(self) -> None:
        if not self._last_report:
            self._refresh_report()
        if self._last_report and os.path.exists(self._last_report):
            webbrowser.open(f"file://{self._last_report}")
            self._log_msg(f"📊 Opening report: {self._last_report}", "info")
        else:
            messagebox.showwarning(
                "Report Not Found",
                "No report file was found.\n"
                "Run tests first so the framework generates a report, then retry.",
            )

    # ── Status bar ─────────────────────────────────────────────────────────────

    def _set_status(self, text: str, color: str) -> None:
        self._status_lbl.config(text=f"● {text}", fg=color)

    # ── Log helpers ────────────────────────────────────────────────────────────

    def _log_msg(self, msg: str, tag: str = "") -> None:
        self._log.config(state="normal")
        if tag:
            self._log.insert("end", msg + "\n", tag)
        else:
            self._log.insert("end", msg + "\n")
        self._log.see("end")
        self._log.config(state="disabled")

    def _append_line(self, line: str) -> None:
        """Colour-tag pytest output lines and append them to the log."""
        self._log.config(state="normal")
        lo = line.lower()

        if line.startswith("PASSED") or line.startswith("passed"):
            tag = "passed"
        elif line.startswith("FAILED") or line.startswith("failed"):
            tag = "failed"
        elif line.startswith("ERROR"):
            tag = "error"
        elif "passed" in lo and "failed" not in lo and "error" not in lo:
            tag = "highlight"
        elif "failed" in lo or "error" in lo or "traceback" in lo:
            tag = "failed"
        elif "warning" in lo or "warn" in lo:
            tag = "warning"
        elif line.startswith("=") or line.startswith("-"):
            tag = "sep"
        else:
            tag = ""

        if tag:
            self._log.insert("end", line + "\n", tag)
        else:
            self._log.insert("end", line + "\n")

        self._log.see("end")
        self._log.config(state="disabled")

    def _clear_log(self) -> None:
        self._log.config(state="normal")
        self._log.delete("1.0", "end")
        self._log.config(state="disabled")


# ── Entry point ────────────────────────────────────────────────────────────────

def main() -> None:
    root = tk.Tk()
    try:
        # Suppress Tkinter deprecation warning on macOS
        root.tk.call("::tk::unsupported::MacWindowStyle", "style", root._w, "document", "none")
    except Exception:
        pass
    PlaywrightExecutorApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
